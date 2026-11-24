"""Pin types for the Judarn design system: npm (`@judarn/ui`), a Swift package manifest, and an Xcode project.

Each type edits plain files except npm, whose lockfile comes from `npm install --package-lock-only`.
A reference that is not an exact version is never edited: the pin reports status "manual" with a note.
A local-path reference reports "local".
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

from . import gitutil
from .errors import CheckError
from .pins import Pin, PinType, Target, _local_clone, _verified_tag

REMOTE = r"https://github\.com/[^/\"\s]+/judarn(?:\.git)?"
NPM_PACKAGE = "@judarn/ui"
NPM_TIMEOUT = 600


def _read(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _judarn_clone(cwd: str) -> str:
    return _local_clone("JUDARN_PATH", "judarn.path", cwd)


def _resolve_judarn(tag: str, name: str) -> Target:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo = _judarn_clone(here)
    return Target(name, tag, _verified_tag(repo, tag), repo)


def _version(tag: str) -> str:
    return tag[1:]


# ---------------------------------------------------------------- Package.resolved

RESOLVED_PIN = re.compile(
    r'("identity"\s*:\s*"judarn"[^{}]*?"state"\s*:\s*\{[^{}]*?"revision"\s*:\s*)"[0-9a-f]*"(\s*,\s*"version"\s*:\s*)"[^"]*"'
)


def resolved_version(text: str) -> Optional[str]:
    """The judarn version in a Package.resolved, or None when it has no judarn entry."""
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    pins = doc.get("pins") or (doc.get("object") or {}).get("pins") or []
    for p in pins:
        if str(p.get("identity") or p.get("package") or "").lower() == "judarn":
            return (p.get("state") or {}).get("version")
    return None


def swap_resolved(text: str, version: str, revision: str) -> str:
    """Set the judarn version and revision in a Package.resolved (version 2 or 3), keeping its formatting. `originHash` stays as it is."""
    if len(RESOLVED_PIN.findall(text)) != 1:
        raise CheckError("Package.resolved: expected exactly one judarn pin with a revision and a version")
    new = RESOLVED_PIN.sub(lambda m: '%s"%s"%s"%s"' % (m.group(1), revision, m.group(2), version), text)
    try:
        json.loads(new)
    except ValueError:
        raise CheckError("Package.resolved edit did not produce valid JSON")
    if resolved_version(new) != version:
        raise CheckError("Package.resolved edit did not produce the new pin")
    return new


def _resolved_edit(root: str, rel: str, target: Target, files: Dict[str, bytes], notes: List[str]) -> None:
    text = _read(os.path.join(root, rel))
    if text is None:
        notes.append("%s is missing; not created" % rel)
        return
    old = resolved_version(text)
    if old is None:
        notes.append("%s has no judarn pin; not edited" % rel)
        return
    if old != _version(target.tag):
        files[rel] = swap_resolved(text, _version(target.tag), target.commit).encode("utf-8")


# ---------------------------------------------------------------- Package.swift

def _balanced(text: str, start: int) -> Tuple[int, int]:
    """Indexes of the `(` after `start` and its matching `)`."""
    open_at = text.index("(", start)
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return open_at, i
    raise CheckError("Package.swift: unbalanced parentheses in a .package(")


EXACT = re.compile(r'(exact:\s*"|\.exact\(\s*")([^"]+)(")')


def _manifest_refs(text: str) -> Tuple[List[Tuple[int, int, str]], bool]:
    """Judarn `.package(` calls: (open, close, argument text) per remote reference; and whether a local path to judarn exists."""
    remote, local = [], False
    for m in re.finditer(r"\.package\(", text):
        open_at, close_at = _balanced(text, m.start())
        args = text[open_at + 1:close_at]
        if re.search(r'path:\s*"[^"]*[/\\]?judarn[/\\]?"', args, re.I):
            local = True
        elif re.search(REMOTE, args):
            remote.append((open_at + 1, close_at, args))
    return remote, local


class SwiftManifestPin:
    """`.package(url: ".../judarn", exact: "X")` in the root `Package.swift`, plus the root `Package.resolved`."""

    MANIFEST = "Package.swift"
    RESOLVED = "Package.resolved"

    def detect(self, root: str) -> bool:
        text = _read(os.path.join(root, self.MANIFEST))
        if text is None:
            return False
        try:
            remote, local = _manifest_refs(text)
        except (CheckError, ValueError):
            return False
        return bool(remote or local)

    def inspect(self, root: str, target: Target) -> Pin:
        text = _read(os.path.join(root, self.MANIFEST)) or ""
        remote, local = _manifest_refs(text)
        if not remote:
            return Pin("local", "", notes=["Package.swift reaches Judarn by a local path; not edited"])
        if len(remote) != 1:
            raise CheckError("Package.swift: expected exactly one remote Judarn reference")
        start, end, args = remote[0]
        found = EXACT.search(args)
        if not found:
            return Pin("manual", "", notes=["Package.swift: the Judarn reference is not an exact version; edit it by hand"])
        old = found.group(2)
        notes = ["Package.swift also reaches Judarn by a local path; that one is not edited"] if local else []
        want = _version(target.tag)
        files: Dict[str, bytes] = {}
        if old != want:
            new_args = EXACT.sub(lambda m: m.group(1) + want + m.group(3), args, count=1)
            files[self.MANIFEST] = (text[:start] + new_args + text[end:]).encode("utf-8")
        _resolved_edit(root, self.RESOLVED, target, files, notes)
        return Pin("update" if files else "current", old, files, notes)


# ---------------------------------------------------------------- Xcode project

# project.xcproj (text project format): an exact version is a one-patch range, since the format has no exact key.
XCPROJ_REMOTE = re.compile(
    r'("repository"\s*:\s*"' + REMOTE + r'"\s*,\s*"version"\s*:\s*\{\s*)'
    r'"version-range-min"(\s*:\s*)"(?P<min>\d+\.\d+\.\d+)"(\s*,\s*)"version-range-max"(\s*:\s*)"(?P<max>\d+\.\d+\.\d+)"'
)
XCPROJ_ANY = re.compile(r'"repository"\s*:\s*"' + REMOTE + '"')
XCPROJ_LOCAL = re.compile(r'"kind"\s*:\s*"local"\s*,\s*"path"\s*:\s*"[^"]*[/\\]?judarn[/\\]?"', re.I)
# project.pbxproj (classic format)
PBX_REMOTE = re.compile(
    r'(repositoryURL\s*=\s*"' + REMOTE + r'";\s*requirement\s*=\s*\{\s*kind\s*=\s*exactVersion;\s*version\s*=\s*)"?(?P<v>\d+\.\d+\.\d+)"?(;)'
)
PBX_ANY = re.compile(r'repositoryURL\s*=\s*"' + REMOTE + '"')
PBX_LOCAL = re.compile(r'XCLocalSwiftPackageReference[^}]*?relativePath\s*=\s*"?[^";]*judarn"?;', re.I | re.S)


def _next_patch(version: str) -> str:
    a, b, c = version.split(".")
    return "%s.%s.%d" % (a, b, int(c) + 1)


def _is_exact_range(lo: str, hi: str) -> bool:
    return _next_patch(lo) == hi


class XcodeProjectPin:
    """An exact Judarn version in `*.xcodeproj/project.xcproj` or `project.pbxproj`, plus that project's `Package.resolved`."""

    FILES = ("project.xcproj", "project.pbxproj")

    def _projects(self, root: str) -> List[Tuple[str, str]]:
        """(relative project file, its text) for each Xcode project in the top folder that mentions Judarn."""
        found = []
        for entry in sorted(os.listdir(root)):
            if not entry.endswith(".xcodeproj"):
                continue
            for name in self.FILES:
                rel = "%s/%s" % (entry, name)
                text = _read(os.path.join(root, rel))
                if text is not None and (XCPROJ_ANY.search(text) or PBX_ANY.search(text) or XCPROJ_LOCAL.search(text) or PBX_LOCAL.search(text)):
                    found.append((rel, text))
        return found

    def detect(self, root: str) -> bool:
        return bool(self._projects(root))

    def inspect(self, root: str, target: Target) -> Pin:
        want = _version(target.tag)
        files: Dict[str, bytes] = {}
        notes: List[str] = []
        olds: List[str] = []
        manual = False
        any_remote = False
        for rel, text in self._projects(root):
            remote = bool(XCPROJ_ANY.search(text) or PBX_ANY.search(text))
            if not remote:
                notes.append("%s reaches Judarn by a local path; not edited" % rel)
                continue
            any_remote = True
            new, old = text, ""
            x = XCPROJ_REMOTE.search(text)
            p = PBX_REMOTE.search(text)
            if x and _is_exact_range(x.group("min"), x.group("max")) and len(XCPROJ_REMOTE.findall(text)) == 1:
                old = x.group("min")
                if old != want:
                    new = XCPROJ_REMOTE.sub(
                        lambda m: '%s"version-range-min"%s"%s"%s"version-range-max"%s"%s"' % (m.group(1), m.group(2), want, m.group(4), m.group(5), _next_patch(want)),
                        text)
            elif p and len(PBX_REMOTE.findall(text)) == 1:
                old = p.group("v")
                if old != want:
                    new = PBX_REMOTE.sub(lambda m: m.group(1) + want + m.group(3), text)
            else:
                manual = True
                notes.append("%s: the Judarn reference is not an exact version; edit it by hand" % rel)
                continue
            olds.append(old)
            if new != text:
                files[rel] = new.encode("utf-8")
            _resolved_edit(root, os.path.dirname(rel) + "/project.xcworkspace/xcshareddata/swiftpm/Package.resolved", target, files, notes)
        if not any_remote:
            return Pin("local", "", notes=notes)
        if manual and not files:
            return Pin("manual", "", notes=notes)
        return Pin("update" if files else "current", ", ".join(sorted(set(olds))), files, notes)


# ---------------------------------------------------------------- npm

def npm_published(package: str, version: str, cwd: str) -> bool:
    """Whether the registry has this exact version. Tests replace this function."""
    try:
        proc = subprocess.run(["npm", "view", "%s@%s" % (package, version), "version"], cwd=cwd, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckError("cannot ask npm whether %s@%s exists: %s" % (package, version, exc))
    return proc.returncode == 0 and proc.stdout.decode().strip() == version


def _swap_dep(text: str, package: str, version: str) -> str:
    pat = re.compile(r'("%s"\s*:\s*)"[^"\\]*"' % re.escape(package))
    if len(pat.findall(text)) != 1:
        raise CheckError('package.json: expected exactly one "%s" entry' % package)
    return pat.sub(lambda m: m.group(1) + json.dumps(version), text)


def _dep_spec(doc: dict, package: str) -> Optional[str]:
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        spec = (doc.get(key) or {}).get(package)
        if isinstance(spec, str):
            return spec
    return None


def _lock_version(text: Optional[str], package: str) -> Optional[str]:
    if text is None:
        return None
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    return ((doc.get("packages") or {}).get("node_modules/" + package) or {}).get("version")


class NpmPin(Pin):
    """package.json is written as text; the lockfile is made by npm itself, never by hand."""

    LOCK = "package-lock.json"

    def __init__(self, current: str, files: Dict[str, bytes], package: str, version: str, notes: List[str]):
        super().__init__("update", current, files, notes)
        self.package = package
        self.version = version

    def write(self, root: str) -> None:
        with open(os.path.join(root, "package.json"), "wb") as fh:
            fh.write(self.files["package.json"])
        if self.LOCK in self.files:
            cmd = ["npm", "install", "--package-lock-only", "--save-exact", "--ignore-scripts", "%s@%s" % (self.package, self.version)]
            proc = subprocess.run(cmd, cwd=root, capture_output=True, timeout=NPM_TIMEOUT)
            if proc.returncode != 0:
                raise CheckError("npm install --package-lock-only failed: " + proc.stderr.decode("utf-8", "replace").strip()[-400:])
            with open(os.path.join(root, "package.json"), "rb") as fh:
                if fh.read() != self.files["package.json"]:
                    raise CheckError("npm rewrote package.json; refusing the edit")

    def committed(self, root: str, revision: str) -> bool:
        pkg = gitutil.run(["show", "%s:package.json" % revision], root, ok=(0, 128))
        if pkg.returncode != 0 or pkg.stdout != self.files["package.json"]:
            return False
        if self.LOCK in self.files:
            lock = gitutil.run(["show", "%s:%s" % (revision, self.LOCK)], root, ok=(0, 128))
            if lock.returncode != 0 or _lock_version(lock.stdout.decode("utf-8", "replace"), self.package) != self.version:
                return False
        return True


class NpmJudarnPin:
    """An exact `@judarn/ui` version in the root `package.json`, with `package-lock.json` regenerated by npm."""

    def _doc(self, root: str) -> Optional[dict]:
        text = _read(os.path.join(root, "package.json"))
        if text is None:
            return None
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        return doc if isinstance(doc, dict) else None

    def detect(self, root: str) -> bool:
        doc = self._doc(root)
        return doc is not None and _dep_spec(doc, NPM_PACKAGE) is not None

    def inspect(self, root: str, target: Target) -> Pin:
        text = _read(os.path.join(root, "package.json")) or ""
        spec = _dep_spec(json.loads(text), NPM_PACKAGE) or ""
        want = _version(target.tag)
        if not re.fullmatch(r"\d+\.\d+\.\d+", spec):
            if spec.startswith(("file:", "link:", "workspace:")):
                return Pin("local", spec, notes=["package.json reaches @judarn/ui by a local path; not edited"])
            return Pin("manual", spec, notes=['package.json: "%s" is "%s", not an exact version; edit it by hand' % (NPM_PACKAGE, spec)])
        lock_text = _read(os.path.join(root, NpmPin.LOCK))
        locked = _lock_version(lock_text, NPM_PACKAGE)
        if spec == want and locked == want:
            return Pin("current", spec)
        if not npm_published(NPM_PACKAGE, want, target.repo):
            blocked = Pin("blocked", spec, notes=[])
            blocked.detail = "%s@%s is not published on npm; package.json not edited" % (NPM_PACKAGE, want)
            return blocked
        notes: List[str] = []
        files = {"package.json": _swap_dep(text, NPM_PACKAGE, want).encode("utf-8")}
        if lock_text is None:
            notes.append("package-lock.json is missing; not created")
        else:
            files[NpmPin.LOCK] = lock_text.encode("utf-8")  # a marker: npm makes the new content in write()
        return NpmPin(spec, files, NPM_PACKAGE, want, notes)


class CombinedPin(Pin):
    """One repository's pins across every form it matches; each part writes and checks itself."""

    ORDER = ("blocked", "update", "manual", "current", "local")

    def __init__(self, parts: List[Pin]):
        status = next(st for st in self.ORDER if any(p.status == st for p in parts))
        files: Dict[str, bytes] = {}
        notes: List[str] = []
        for part in parts:
            for rel in part.files:
                if rel in files:
                    raise CheckError("two Judarn references edit " + rel)
            files.update(part.files)
            notes.extend(part.notes)
        olds = sorted({p.current for p in parts if p.current})
        super().__init__(status, ", ".join(olds), files, notes)
        self.parts = parts
        self.detail = "; ".join(p.detail for p in parts if p.status == "blocked" and p.detail)

    def write(self, root: str) -> None:
        for part in self.parts:
            if part.status == "update":
                part.write(root)

    def committed(self, root: str, revision: str) -> bool:
        return all(p.committed(root, revision) for p in self.parts if p.status == "update")


class JudarnPin(PinType):
    """The Judarn design system, in whichever forms a repository loads it: npm, a Swift manifest, an Xcode project."""

    name = "judarn"
    FORMS = (NpmJudarnPin(), SwiftManifestPin(), XcodeProjectPin())

    def subject(self, tag: str) -> str:
        return "build: pin Judarn at " + tag

    def detect(self, root: str) -> bool:
        return any(f.detect(root) for f in self.FORMS)

    def resolve_target(self, tag: str) -> Target:
        return _resolve_judarn(tag, "judarn")

    def inspect(self, root: str, target: Target) -> Pin:
        return CombinedPin([f.inspect(root, target) for f in self.FORMS if f.detect(root)])


JUDARN_PIN_TYPES = [JudarnPin()]
