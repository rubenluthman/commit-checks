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
from .pins import Pin, PinType, Reading, Target, _local_clone, _verified_tag, form

REMOTE = r"https://github\.com/[^/\"\s]+/judarn(?:\.git)?"
NPM_PACKAGE = "@judarn/ui"
NPM_TIMEOUT = 600


def _read(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _tracked(root: str, rel: str) -> bool:
    """False only when Git says `rel` is not tracked; a folder outside Git keeps every file."""
    return gitutil.run(["ls-files", "--error-unmatch", "--", rel], root, ok=(0, 1, 128)).returncode != 1


def _read_tracked(root: str, rel: str) -> Optional[str]:
    """The text of a file the repository tracks, else None."""
    if not os.path.isfile(os.path.join(root, rel)) or not _tracked(root, rel):
        return None
    return _read(os.path.join(root, rel))


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
    text = _read_tracked(root, rel)
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
        text = _read_tracked(root, self.MANIFEST)
        if text is None:
            return False
        try:
            remote, local = _manifest_refs(text)
        except (CheckError, ValueError):
            return False
        return bool(remote or local)

    def inspect(self, root: str, target: Target) -> Pin:
        text = _read_tracked(root, self.MANIFEST) or ""
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
        notes = ["Package.swift reaches Judarn by a local path as well; that one is not edited"] if local else []
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
                text = _read_tracked(root, rel)
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
        text = _read_tracked(root, "package.json")
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
        text = _read_tracked(root, "package.json") or ""
        spec = _dep_spec(json.loads(text), NPM_PACKAGE) or ""
        want = _version(target.tag)
        if not re.fullmatch(r"\d+\.\d+\.\d+", spec):
            if spec.startswith(("file:", "link:", "workspace:")) or re.search(r"[/\\]judarn", spec, re.I):
                return Pin("local", spec, notes=["package.json reaches Judarn by a local path; not edited"])
            return Pin("manual", spec, notes=['range "%s" in package.json, not an exact version; edit it by hand' % spec])
        lock_text = _read_tracked(root, NpmPin.LOCK)
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


# ---------------------------------------------------------------- the reader

SKIP_DIRS = frozenset({".build", "build", "DerivedData", "node_modules", "scripts", "Pods"})
PIN_FILES = frozenset({"package-lock.json", "package.json", "Package.resolved", "Package.swift", "project.yml",
                       "project.xcproj", "project.pbxproj"})
SCAN_DEPTH = 2  # the repository folder plus two levels of subfolders
NPM_GROUPS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")
# Any host, HTTPS or SSH: owner/judarn with or without .git.
URL_RE = re.compile(r"[/:][\w.-]+/judarn(?:\.git)?(?=[\"'\s,)]|$)", re.I)
LOCAL_RE = re.compile(r"""(?:relativePath|path)["']?\s*[:=]\s*\(?\s*["']?([^"';,\n)]*[/\\]judarn)[/\\]?["']?""", re.I)
SWIFT_LOCAL_RE = re.compile(r'path:\s*"[^"]*[/\\]?judarn[/\\]?"', re.I)
RESOLVED_SUFFIX = "xcshareddata/swiftpm/Package.resolved"
LOCAL_REQ = {"type": "local", "version": None}


def _is_exact(version: str) -> bool:
    return re.fullmatch(r"\d+\.\d+\.\d+", version or "") is not None


def _range_req(lo: str, hi: str) -> dict:
    """A range from one version up to the next patch admits exactly one version: Xcode's project file has no exact key."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", lo or "")
    if m and hi == "%s.%s.%d" % (m.group(1), m.group(2), int(m.group(3)) + 1):
        return {"type": "exact", "version": lo}
    return {"type": "range", "version": lo, "max": hi}


def _tracked_paths(root: str) -> Optional[set]:
    proc = gitutil.run(["ls-files", "-z"], root, ok=(0, 128))
    if proc.returncode != 0:
        return None  # not a Git work tree
    return {os.path.join(root, p) for p in gitutil.split_z(proc.stdout)}


def collect_pin_files(root: str) -> List[str]:
    """Pin-bearing files in the top levels of a repository, plus Xcode's resolved file below its project folders.
    Only files Git tracks count: an ignored build leftover can hold a stale pin."""
    found = set()

    def visit(folder: str, depth: int) -> None:
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if e.is_file(follow_symlinks=False) and e.name in PIN_FILES:
                found.add(e.path)
            elif e.is_dir(follow_symlinks=False) and not e.name.startswith(".") and e.name not in SKIP_DIRS:
                if e.name.endswith((".xcodeproj", ".xcworkspace")):
                    nested = os.path.join(e.path, "project.xcworkspace") if e.name.endswith(".xcodeproj") else e.path
                    resolved = os.path.join(nested, RESOLVED_SUFFIX)
                    if os.path.isfile(resolved):
                        found.add(resolved)
                if depth < SCAN_DEPTH:
                    visit(e.path, depth + 1)

    visit(root, 0)
    tracked = _tracked_paths(root)
    return sorted(f for f in found if tracked is None or f in tracked)


def _parse_resolved(text: str) -> Optional[dict]:
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    pins = doc.get("pins") or (doc.get("object") or {}).get("pins") or []
    for p in pins if isinstance(pins, list) else []:
        if not isinstance(p, dict):
            continue
        ident = str(p.get("identity") or p.get("package") or "").lower()
        if ident == "judarn" or URL_RE.search(str(p.get("location") or p.get("repositoryURL") or "")):
            st = p.get("state") if isinstance(p.get("state"), dict) else {}
            return {"version": st.get("version"), "revision": st.get("revision"), "branch": st.get("branch"),
                    "_identity": "identity" in p}  # the editor needs the version 2 or 3 layout
    return None


def _balanced_text(text: str, start: int) -> str:
    """Text between the first `(` after `start` and its matching `)`; the rest of the file when it never closes."""
    open_at = text.find("(", start)
    if open_at < 0:
        return ""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return text[open_at + 1:]


def _req_from_keys(pairs: List[Tuple[str, str]]) -> Optional[dict]:
    lo = hi = None
    for k, v in pairs:
        key = re.sub(r"[-_ ]", "", k.lower())
        if key in ("exact", "exactversion", "version") and _is_exact(v):
            return {"type": "exact", "version": v}
        if re.search(r"upto.*minor|^minorversion$", key):
            return {"type": "upToNextMinor", "version": v}
        if re.search(r"upto.*major|^majorversion$|^from$", key):
            return {"type": "upToNextMajor", "version": v}
        if key in ("minimumversion", "minversion", "versionrangemin"):
            lo = v
        if key in ("maximumversion", "maxversion", "versionrangemax"):
            hi = v
        if key == "branch":
            return {"type": "branch", "version": v}
        if key == "revision":
            return {"type": "revision", "version": v}
    if lo:
        return _range_req(lo, hi) if hi else {"type": "upToNextMajor", "version": lo}
    return None


PBX_KINDS = {"exactVersion": "exact", "upToNextMinorVersion": "upToNextMinor", "upToNextMajorVersion": "upToNextMajor", "versionRange": "range"}
PROJECT_URL = re.compile(r'(?:"repository"|"repositoryURL"|repositoryURL)\s*[:=]\s*"?([^"\s;,]+)')
PROJECT_KV = re.compile(r'"?([A-Za-z][\w-]*)"?\s*[:=]\s*"?([\w.-]+)"?')


def _parse_swift_file(name: str, text: str) -> Tuple[List[dict], bool]:
    """(Judarn requirements, whether a local path to Judarn is named) in one manifest or project file."""
    remote: List[dict] = []
    local = False
    if name == "Package.swift":
        for m in re.finditer(r"\.package\(", text):
            args = _balanced_text(text, m.start())
            if SWIFT_LOCAL_RE.search(args):
                local = True
            elif URL_RE.search(args):
                req = None
                if (g := re.search(r'exact:\s*"([^"]+)"|\.exact\(\s*"([^"]+)"', args)):
                    req = {"type": "exact", "version": g.group(1) or g.group(2)}
                elif (g := re.search(r'\.upToNextMinor\(\s*from:\s*"([^"]+)"', args)):
                    req = {"type": "upToNextMinor", "version": g.group(1)}
                elif (g := re.search(r'\.upToNextMajor\(\s*from:\s*"([^"]+)"|from:\s*"([^"]+)"', args)):
                    req = {"type": "upToNextMajor", "version": g.group(1) or g.group(2)}
                elif (g := re.search(r'"([^"]+)"\s*\.\.[.<]\s*"([^"]+)"', args)):
                    req = _range_req(g.group(1), g.group(2))
                elif (g := re.search(r'branch:\s*"([^"]+)"', args)):
                    req = {"type": "branch", "version": g.group(1)}
                elif (g := re.search(r'revision:\s*"([^"]+)"', args)):
                    req = {"type": "revision", "version": g.group(1)}
                remote.append(req or {"type": "unknown", "version": None})
        return remote, local
    if name == "project.yml":
        lines = text.split("\n")

        def indent(line: str) -> int:
            return len(line) - len(line.lstrip())

        def plain(line: str) -> str:
            """The line with a trailing ` # comment` and one pair of matching quotes around the value removed."""
            m = re.match(r"^(\s*(?:url|path):\s*)(.*?)\s*$", line)
            if not m:
                return line
            value = re.sub(r"\s+#.*$", "", m.group(2)).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return m.group(1) + value

        for i, line in enumerate(lines):
            bare = plain(line)
            if re.match(r"^\s*path:\s*\S*[/\\]judarn[/\\]?\s*$", bare, re.I):
                local = True
            if not (re.match(r"^\s*url:\s*\S*judarn(\.git)?\s*$", bare, re.I) and URL_RE.search(bare)):
                continue
            ind = indent(line)
            block = []
            j = i
            while j >= 0 and indent(lines[j]) >= ind and lines[j].strip():
                block.append(lines[j])
                j -= 1
            j = i + 1
            while j < len(lines) and indent(lines[j]) >= ind and lines[j].strip():
                block.append(lines[j])
                j += 1
            kv = []
            for item in block:
                g = re.match(r'^(\w+):\s*"?([^"\s]+)"?$', item.strip())
                if g and g.group(1) != "url":
                    kv.append((g.group(1), g.group(2)))
            remote.append(_req_from_keys(kv) or {"type": "unknown", "version": None})
        return remote, local
    # project.xcproj (JSON-like) and project.pbxproj
    if LOCAL_RE.search(text):
        local = True
    for m in PROJECT_URL.finditer(text):
        if not URL_RE.search(m.group(1)):
            continue
        rest = text[m.end():m.end() + 400]
        cut = re.search(r'"kind"|isa\s*=|XCRemoteSwiftPackageReference|XCLocalSwiftPackageReference', rest)
        win = rest[:cut.start()] if cut else rest
        kv = [(x.group(1), x.group(2)) for x in PROJECT_KV.finditer(win)]
        kind = next((v for k, v in kv if k == "kind"), None)
        req = None
        if kind in PBX_KINDS:
            ver = next((v for k, v in kv if k in ("version", "minimumVersion")), None)
            hi = next((v for k, v in kv if k == "maximumVersion"), None)
            if ver:
                if PBX_KINDS[kind] == "range" and hi:
                    req = _range_req(ver, hi)
                else:
                    req = {"type": PBX_KINDS[kind], "version": ver}
                    if hi:
                        req["max"] = hi
        elif kind in ("branch", "revision"):
            req = {"type": kind, "version": next((v for k, v in kv if k == kind), None)}
        else:
            req = _req_from_keys(kv)
        remote.append(req or {"type": "unknown", "version": None})
    return remote, local


def _swift_editable(name: str, rel: str, text: str) -> bool:
    """Whether the editors can move this one reference: one remote GitHub reference, exact, in a top-level manifest or project."""
    if name == "Package.swift":
        if "/" in rel:
            return False
    elif not (rel.count("/") == 1 and rel.split("/")[0].endswith(".xcodeproj")):
        return False
    if name == "Package.swift":
        try:
            remote, _local = _manifest_refs(text)
        except (CheckError, ValueError):
            return False
        return len(remote) == 1 and EXACT.search(remote[0][2]) is not None
    if name == "project.xcproj":
        found = XCPROJ_REMOTE.findall(text)
        x = XCPROJ_REMOTE.search(text)
        return bool(x) and len(found) == 1 and _is_exact_range(x.group("min"), x.group("max")) and len(XCPROJ_ANY.findall(text)) == 1
    if name == "project.pbxproj":
        return len(PBX_REMOTE.findall(text)) == 1 and len(PBX_ANY.findall(text)) == 1
    return False


def _resolved_handled(manifest_rel: str, resolved_rel: str) -> bool:
    """Whether an editor moves this resolved file together with that manifest or project."""
    if manifest_rel == "Package.swift":
        return resolved_rel == "Package.resolved"
    return resolved_rel == os.path.dirname(manifest_rel) + "/project.xcworkspace/" + RESOLVED_SUFFIX


def _read_npm(root: str, files: List[str], rel, forms: List[dict], problems: List[str]) -> None:
    locks: Dict[str, Tuple[str, str]] = {}
    for f in files:
        if os.path.basename(f) != "package-lock.json":
            continue
        try:
            doc = json.loads(_read(f) or "")
        except ValueError:
            doc = None
        if not isinstance(doc, dict):
            continue
        entry = None
        for key, name in (("packages", "node_modules/" + NPM_PACKAGE), ("dependencies", NPM_PACKAGE)):
            group = doc.get(key)
            if isinstance(group, dict) and isinstance(group.get(name), dict):
                entry = group[name]
                break
        if entry and isinstance(entry.get("version"), str):
            locks[os.path.dirname(f)] = (f, entry["version"])
    used = set()
    for f in files:
        if os.path.basename(f) != "package.json":
            continue
        text = _read(f) or ""
        try:
            doc = json.loads(text)
        except ValueError:
            if NPM_PACKAGE in text:
                forms.append(form("npm", rel(f), {"type": "unknown", "version": None}))
                problems.append("%s: cannot be parsed, but names %s" % (rel(f), NPM_PACKAGE))
            continue
        if not isinstance(doc, dict):
            continue
        specs = []
        for group in NPM_GROUPS:
            deps = doc.get(group)
            if isinstance(deps, dict) and isinstance(deps.get(NPM_PACKAGE), str):
                specs.append((group, deps[NPM_PACKAGE]))
        remote_specs, has_local = [], False
        for group, spec in specs:
            if spec.startswith(("file:", "link:", "workspace:")) or re.search(r"[/\\]judarn", spec, re.I):
                has_local = True
            else:
                remote_specs.append((group, spec))
        if has_local:
            forms.append(form("npm", rel(f), dict(LOCAL_REQ)))
        if remote_specs:
            first_group, spec = remote_specs[0]
            distinct = {sp for _g, sp in remote_specs}
            if len(distinct) > 1:
                problems.append("%s: %s is listed with different versions: %s" % (rel(f), NPM_PACKAGE, ", ".join(sorted(distinct))))
            lock = locks.get(os.path.dirname(f))
            resolved = None
            if lock:
                used.add(lock[0])
                resolved = {"file": rel(lock[0]), "version": lock[1], "revision": None, "branch": None}
            exact = _is_exact(spec)
            forms.append(form("npm", rel(f), {"type": "exact" if exact else "range", "version": spec}, resolved,
                              rel(f) == "package.json" and exact and first_group != "peerDependencies" and len(distinct) == 1))
    for d, (lf, version) in sorted(locks.items()):
        if lf not in used:
            forms.append(form("npm", rel(lf), None, {"file": rel(lf), "version": version, "revision": None, "branch": None}))


def _pair_tiers(resolved: str) -> List[List[str]]:
    """Where a resolved file's own manifest or project may be: each tier is tried in turn."""
    d = os.path.dirname(resolved)
    projects = lambda folder: [os.path.join(folder, n) for n in ("project.xcproj", "project.pbxproj")]
    if resolved.endswith(".xcodeproj/project.xcworkspace/" + RESOLVED_SUFFIX):
        return [projects(os.path.dirname(os.path.dirname(os.path.dirname(d))))]
    if resolved.endswith(".xcworkspace/" + RESOLVED_SUFFIX):
        ws = os.path.dirname(os.path.dirname(d))
        parent = os.path.dirname(ws)
        stem = os.path.basename(ws)[:-len(".xcworkspace")]
        tiers = [projects(os.path.join(parent, stem + ".xcodeproj")), [os.path.join(parent, "project.yml")],
                 [os.path.join(parent, "Package.swift")]]
        try:
            others = [e.path for e in sorted(os.scandir(parent), key=lambda e: e.name) if e.name.endswith(".xcodeproj")]
        except OSError:
            others = []
        tiers.append([p for o in others for p in projects(o)])
        return tiers
    return [[os.path.join(d, "Package.swift")], [os.path.join(d, "project.yml")]]


def _read_swift(root: str, files: List[str], rel, forms: List[dict], problems: List[str]) -> None:
    manifests: Dict[str, dict] = {}
    resolved_files: List[Tuple[str, dict]] = []
    for f in files:
        base = os.path.basename(f)
        if base in ("package.json", "package-lock.json"):
            continue
        text = _read(f)
        if text is None:
            continue
        if base == "Package.resolved":
            pin = _parse_resolved(text)
            if pin:
                resolved_files.append((f, pin))
            elif "judarn" in text.lower():
                try:
                    json.loads(text)
                except ValueError:
                    problems.append("%s: cannot be parsed, but names Judarn" % rel(f))
            continue
        remote, local = _parse_swift_file(base, text)
        if remote or local:
            manifests[f] = {"remote": remote, "local": local, "name": base, "text": text}
    paired: Dict[str, List[Tuple[str, dict]]] = {}
    standalone = []
    for rf, pin in resolved_files:
        owner = None
        for tier in _pair_tiers(rf):
            owner = next((m for m in tier if m in manifests and manifests[m]["remote"]), None)
            if owner:
                break
        if owner:
            paired.setdefault(owner, []).append((rf, pin))
        else:
            standalone.append((rf, pin))
    for mf, info in sorted(manifests.items()):
        mrel = rel(mf)
        if len(info["remote"]) > 1:
            problems.append("%s: has %d Judarn references" % (mrel, len(info["remote"])))
        for n, req in enumerate(info["remote"]):
            editable = len(info["remote"]) == 1 and _swift_editable(info["name"], mrel, info["text"])
            copies = paired.get(mf) or [(None, None)]
            for rf, pin in copies:
                resolved = None
                if rf:
                    resolved = {"file": rel(rf), "version": pin["version"], "revision": pin["revision"], "branch": pin["branch"]}
                forms.append(form("swift", mrel, dict(req), resolved,
                                  editable and (rf is None or (_resolved_handled(mrel, rel(rf)) and pin["_identity"])), _ref=(mrel, n)))
        if info["local"]:
            forms.append(form("swift", mrel, dict(LOCAL_REQ)))
    for rf, pin in standalone:
        forms.append(form("swift", rel(rf), None, {"file": rel(rf), "version": pin["version"], "revision": pin["revision"], "branch": pin["branch"]}))


def _judge(forms: List[dict], problems: List[str]) -> None:
    """Mismatch and local-path texts, worded once."""
    seen = set()
    for f in forms:
        req, res = f["requirement"], f["resolved"]
        if req is None:
            continue
        text = None
        if req["type"] == "local":
            text = "%s reaches Judarn by a local path" % f["file"]
        elif f["kind"] == "npm":
            if req["type"] == "range":
                text = 'range "%s" in %s, not an exact version' % (req["version"], f["file"])
            elif res and res["version"] and res["version"] != req["version"]:
                text = "lockfile holds %s, package.json says %s (%s)" % (res["version"], req["version"], f["file"])
        elif res and res["version"] and req.get("version"):
            if req["type"] == "exact" and res["version"] != req["version"]:
                text = "resolved %s differs from exact %s (%s)" % (res["version"], req["version"], f["file"])
            elif req["type"] in ("upToNextMinor", "upToNextMajor", "range") and res["version"] != req["version"]:
                text = "resolved %s differs from floor %s (%s)" % (res["version"], req["version"], f["file"])
        if text and text not in seen:
            seen.add(text)
            problems.append(text)


def read_judarn(root: str) -> Reading:
    files = collect_pin_files(root)

    def rel(f: str) -> str:
        return os.path.relpath(f, root).replace(os.sep, "/")

    forms: List[dict] = []
    problems: List[str] = []
    _read_npm(root, files, rel, forms, problems)
    _read_swift(root, files, rel, forms, problems)
    _judge(forms, problems)
    reading = Reading(forms, problems)
    versions = sorted({(f["resolved"] or {}).get("version") or (f["requirement"] or {}).get("version")
                       for f in forms if (f["resolved"] or {}).get("version") or (f["requirement"] or {}).get("type") == "exact"})
    if len(versions) > 1:
        problems.append("the forms disagree: " + ", ".join(versions))
    return reading


class JudarnPin(PinType):
    """The Judarn design system, in whichever forms a repository loads it: npm, a Swift manifest, an Xcode project.

    `read` sees every form (see the reader above). The editors move only the exact forms in top-level
    files; any other form `read` finds becomes a "manual" or "local" part with its file named.
    """

    name = "judarn"
    FORMS = (NpmJudarnPin(), SwiftManifestPin(), XcodeProjectPin())

    def subject(self, tag: str) -> str:
        return "build: pin Judarn at " + tag

    def read(self, root: str) -> Reading:
        return read_judarn(root)

    def resolve_target(self, tag: str) -> Target:
        return _resolve_judarn(tag, "judarn")

    def _handled(self, root: str) -> Tuple[set, set]:
        """Files the editors report on: manifests and projects, and the resolved files they move."""
        manifests, resolved = set(), set()
        npm, swift, xcode = self.FORMS
        if npm.detect(root):
            manifests.add("package.json")
            resolved.add(NpmPin.LOCK)
        if swift.detect(root):
            manifests.add("Package.swift")
            resolved.add("Package.resolved")
        for rel, _text in xcode._projects(root):
            manifests.add(rel)
            resolved.add(os.path.dirname(rel) + "/project.xcworkspace/" + RESOLVED_SUFFIX)
        return manifests, resolved

    def inspect(self, root: str, target: Target) -> Pin:
        handled, handled_resolved = self._handled(root)
        reading = self.read(root)
        refs: Dict[str, set] = {}
        for f in reading.forms:
            if "_ref" in f and f["file"] in handled:
                refs.setdefault(f["file"], set()).add(f["_ref"])
        for rel, found in sorted(refs.items()):
            if len(found) > 1:
                raise CheckError("%s has %d Judarn references; roll-out cannot tell which to move" % (rel, len(found)))
        parts = [f.inspect(root, target) for f in self.FORMS if f.detect(root)]
        done = set()
        for f in reading.forms:
            req, res = f["requirement"], f["resolved"]
            if f["file"] not in handled and f["file"] not in done:
                done.add(f["file"])
                if req and req["type"] == "local":
                    parts.append(Pin("local", "", notes=["%s reaches Judarn by a local path; not edited" % f["file"]]))
                else:
                    how = "%s %s" % (req["type"], req["version"] or "") if req else "a lock"
                    parts.append(Pin("manual", (res or {}).get("version") or (req or {}).get("version") or "",
                                     notes=["%s pins Judarn (%s); roll-out does not edit this file, change it by hand" % (f["file"], how.strip())]))
            elif res and f["file"] in handled and res["file"] not in handled_resolved and res["file"] not in done:
                done.add(res["file"])
                parts.append(Pin("manual", res["version"] or "",
                                 notes=["%s also pins Judarn %s; roll-out does not edit this file, change it by hand" % (res["file"], res["version"])]))
        if not parts:
            raise CheckError("Judarn is pinned in a form this version cannot describe")
        return CombinedPin(parts)


JUDARN_PIN_TYPES = [JudarnPin()]
