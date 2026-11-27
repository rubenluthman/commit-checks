"""Swift package pins shared by every package that loads as a Swift dependency: Judarn, markdown-engine, apple-dev-support, and its earlier parts apple-app-support and apple-test-support.

`forms_for(name, label)` builds the readers and editors for one package, matched by the last part of its repository URL.
The editors move only an exact version: `exact:` (or `.exact(`) in the root `Package.swift`, a one-patch range or `exactVersion`
in an Xcode project, and the paired `Package.resolved`. Any other reference is never edited: status "manual" with a note, or
"range" when the entry is a library and the reference is a range. A local-path reference reports "local".
"""
from __future__ import annotations

import json
import os
import re
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

from . import gitutil
from .errors import CheckError
from .pins import PinType, Pin, Reading, Target, _local_clone, _verified_tag, form


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


def _version(tag: str) -> str:
    return tag[1:]



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


def _next_patch(version: str) -> str:
    a, b, c = version.split(".")
    return "%s.%s.%d" % (a, b, int(c) + 1)


def _is_exact_range(lo: str, hi: str) -> bool:
    return _next_patch(lo) == hi



SKIP_DIRS = frozenset({".build", "build", "DerivedData", "node_modules", "scripts", "Pods"})
PIN_FILES = frozenset({"package-lock.json", "package.json", "Package.resolved", "Package.swift", "project.yml",
                       "project.xcproj", "project.pbxproj"})
SCAN_DEPTH = 2  # the repository folder plus two levels of subfolders


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



def _resolved_handled(manifest_rel: str, resolved_rel: str) -> bool:
    """Whether an editor moves this resolved file together with that manifest or project."""
    if manifest_rel == "Package.swift":
        return resolved_rel == "Package.resolved"
    return resolved_rel == os.path.dirname(manifest_rel) + "/project.xcworkspace/" + RESOLVED_SUFFIX


def forms_for(name: str, label: str) -> SimpleNamespace:
    """The Swift readers and editors for one package. `name` is the repository name; `label` is how messages write it."""
    esc = re.escape(name)
    REMOTE = r"https://github\.com/[^/\"\s]+/" + esc + r"(?:\.git)?"
    LOCAL_NAME = r"[/\\]?" + esc + r"[/\\]?"

    RESOLVED_PIN = re.compile(
        r'("identity"\s*:\s*"' + esc + r'"[^{}]*?"state"\s*:\s*\{[^{}]*?"revision"\s*:\s*)"[0-9a-f]*"(\s*,\s*"version"\s*:\s*)"[^"]*"'
    )


    def resolved_version(text: str) -> Optional[str]:
        """The version of this package in a Package.resolved, or None when it has no entry for it."""
        try:
            doc = json.loads(text)
        except ValueError:
            return None
        pins = doc.get("pins") or (doc.get("object") or {}).get("pins") or []
        for p in pins:
            if str(p.get("identity") or p.get("package") or "").lower() == name.lower():
                return (p.get("state") or {}).get("version")
        return None


    def swap_resolved(text: str, version: str, revision: str) -> str:
        """Set the package version and revision in a Package.resolved (version 2 or 3), keeping its formatting. `originHash` stays as it is."""
        if len(RESOLVED_PIN.findall(text)) != 1:
            raise CheckError("Package.resolved: expected exactly one %s pin with a revision and a version" % name)
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
            notes.append("%s has no %s pin; not edited" % (rel, name))
            return
        if old != _version(target.tag):
            files[rel] = swap_resolved(text, _version(target.tag), target.commit).encode("utf-8")



    def _manifest_refs(text: str) -> Tuple[List[Tuple[int, int, str]], bool]:
        """Judarn `.package(` calls: (open, close, argument text) per remote reference; and whether a local path to judarn exists."""
        remote, local = [], False
        for m in re.finditer(r"\.package\(", text):
            open_at, close_at = _balanced(text, m.start())
            args = text[open_at + 1:close_at]
            if re.search(r'path:\s*"[^"]*' + LOCAL_NAME + '"', args, re.I):
                local = True
            elif re.search(REMOTE, args):
                remote.append((open_at + 1, close_at, args))
        return remote, local


    def _unmovable(rel: str, text: str, library: bool) -> Pin:
        """A reference the editors cannot move: "range" for a library's version range, else "manual"."""
        reqs, _local = _parse_swift_file(os.path.basename(rel), text)
        if library and reqs and all(r["type"] in RANGE_TYPES for r in reqs):
            first = reqs[0]
            how = ("%s %s" % (first["type"], first["version"] or "")).strip()
            return Pin("range", first["version"] or "", notes=["%s: %s, a range in a library; not edited" % (rel, how)])
        return Pin("manual", "", notes=["%s: the %s reference is not an exact version; edit it by hand" % (rel, label)])


    class SwiftManifestPin:
        """`.package(url: ".../<name>", exact: "X")` in the root `Package.swift`, plus the root `Package.resolved`."""

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

        def inspect(self, root: str, target: Target, library: bool = False) -> Pin:
            text = _read_tracked(root, self.MANIFEST) or ""
            remote, local = _manifest_refs(text)
            if not remote:
                return Pin("local", "", notes=["Package.swift reaches %s by a local path; not edited" % label])
            if len(remote) != 1:
                raise CheckError("Package.swift: expected exactly one remote %s reference" % label)
            start, end, args = remote[0]
            found = EXACT.search(args)
            if not found:
                return _unmovable(self.MANIFEST, text, library)
            old = found.group(2)
            notes = ["Package.swift reaches %s by a local path as well; that one is not edited" % label] if local else []
            want = _version(target.tag)
            files: Dict[str, bytes] = {}
            if old != want:
                new_args = EXACT.sub(lambda m: m.group(1) + want + m.group(3), args, count=1)
                files[self.MANIFEST] = (text[:start] + new_args + text[end:]).encode("utf-8")
            _resolved_edit(root, self.RESOLVED, target, files, notes)
            return Pin("update" if files else "current", old, files, notes)



    # project.xcproj (text project format): an exact version is a one-patch range, since the format has no exact key.
    XCPROJ_REMOTE = re.compile(
        r'("repository"\s*:\s*"' + REMOTE + r'"\s*,\s*"version"\s*:\s*\{\s*)'
        r'"version-range-min"(\s*:\s*)"(?P<min>\d+\.\d+\.\d+)"(\s*,\s*)"version-range-max"(\s*:\s*)"(?P<max>\d+\.\d+\.\d+)"'
    )
    XCPROJ_ANY = re.compile(r'"repository"\s*:\s*"' + REMOTE + '"')
    XCPROJ_LOCAL = re.compile(r'"kind"\s*:\s*"local"\s*,\s*"path"\s*:\s*"[^"]*' + LOCAL_NAME + '"', re.I)
    # project.pbxproj (classic format)
    PBX_REMOTE = re.compile(
        r'(repositoryURL\s*=\s*"' + REMOTE + r'";\s*requirement\s*=\s*\{\s*kind\s*=\s*exactVersion;\s*version\s*=\s*)"?(?P<v>\d+\.\d+\.\d+)"?(;)'
    )
    PBX_ANY = re.compile(r'repositoryURL\s*=\s*"' + REMOTE + '"')
    PBX_LOCAL = re.compile(r'XCLocalSwiftPackageReference[^}]*?relativePath\s*=\s*"?[^";]*' + esc + r'"?;', re.I | re.S)



    class XcodeProjectPin:
        """An exact version of the package in `*.xcodeproj/project.xcproj` or `project.pbxproj`, plus that project's `Package.resolved`."""

        FILES = ("project.xcproj", "project.pbxproj")

        def _projects(self, root: str) -> List[Tuple[str, str]]:
            """(relative project file, its text) for each Xcode project in the top folder that mentions the package."""
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

        def inspect(self, root: str, target: Target, library: bool = False) -> Pin:
            want = _version(target.tag)
            files: Dict[str, bytes] = {}
            notes: List[str] = []
            olds: List[str] = []
            manual = False
            ranged = False
            any_remote = False
            for rel, text in self._projects(root):
                remote = bool(XCPROJ_ANY.search(text) or PBX_ANY.search(text))
                if not remote:
                    notes.append("%s reaches %s by a local path; not edited" % (rel, label))
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
                    unmoved = _unmovable(rel, text, library)
                    notes.extend(unmoved.notes)
                    if unmoved.status == "range":
                        ranged = True
                        olds.append(unmoved.current)
                    else:
                        manual = True
                    continue
                olds.append(old)
                if new != text:
                    files[rel] = new.encode("utf-8")
                _resolved_edit(root, os.path.dirname(rel) + "/project.xcworkspace/xcshareddata/swiftpm/Package.resolved", target, files, notes)
            if not any_remote:
                return Pin("local", "", notes=notes)
            if manual and not files:
                return Pin("manual", "", notes=notes)
            if ranged and not files:
                return Pin("range", ", ".join(sorted(set(olds))), notes=notes)
            return Pin("update" if files else "current", ", ".join(sorted(set(olds))), files, notes)



    URL_RE = re.compile(r"[/:][\w.-]+/" + esc + r"(?:\.git)?(?=[\"'\s,)]|$)", re.I)
    LOCAL_RE = re.compile(r"""(?:relativePath|path)["']?\s*[:=]\s*\(?\s*["']?([^"';,\n)]*[/\\]""" + esc + r""")[/\\]?["']?""", re.I)
    SWIFT_LOCAL_RE = re.compile(r'path:\s*"[^"]*' + LOCAL_NAME + '"', re.I)


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
            if ident == name.lower() or URL_RE.search(str(p.get("location") or p.get("repositoryURL") or "")):
                st = p.get("state") if isinstance(p.get("state"), dict) else {}
                return {"version": st.get("version"), "revision": st.get("revision"), "branch": st.get("branch"),
                        "_identity": "identity" in p}  # the editor needs the version 2 or 3 layout
        return None



    def _parse_swift_file(name: str, text: str) -> Tuple[List[dict], bool]:
        """(requirements for this package, whether a local path to it is named) in one manifest or project file."""
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
                if re.match(r"^\s*path:\s*\S*[/\\]" + esc + r"[/\\]?\s*$", bare, re.I):
                    local = True
                if not (re.match(r"^\s*url:\s*\S*" + esc + r"(\.git)?\s*$", bare, re.I) and URL_RE.search(bare)):
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
                elif name.lower() in text.lower():
                    try:
                        json.loads(text)
                    except ValueError:
                        problems.append("%s: cannot be parsed, but names %s" % (rel(f), label))
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
                problems.append("%s: has %d %s references" % (mrel, len(info["remote"]), label))
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
                text = "%s reaches %s by a local path" % (f["file"], label)
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


    def read(root: str, extra=None) -> Reading:
        """Every form in which `root` pins this package. `extra(files, rel, forms, problems)` may add forms of another kind first."""
        files = collect_pin_files(root)

        def rel(f: str) -> str:
            return os.path.relpath(f, root).replace(os.sep, "/")

        forms: List[dict] = []
        problems: List[str] = []
        if extra is not None:
            extra(root, files, rel, forms, problems)
        _read_swift(root, files, rel, forms, problems)
        _judge(forms, problems)
        versions = sorted({(f["resolved"] or {}).get("version") or (f["requirement"] or {}).get("version")
                           for f in forms if (f["resolved"] or {}).get("version") or (f["requirement"] or {}).get("type") == "exact"})
        if len(versions) > 1:
            problems.append("the forms disagree: " + ", ".join(versions))
        return Reading(forms, problems)

    return SimpleNamespace(manifest=SwiftManifestPin(), xcode=XcodeProjectPin(), read=read, parse_file=_parse_swift_file,
                           resolved_version=resolved_version, swap_resolved=swap_resolved)


class SwiftPackagePin(PinType):
    """A package loaded through Swift Package Manager, found by the last part of its repository URL on any owner.

    `read` sees every form (see `forms_for`). The editors move only exact forms in top-level files; any other
    form `read` finds becomes a "manual" or "local" part with its file named, or "range" in a library.
    """

    def __init__(self, name: str, label: str = ""):
        self.name = name
        self.label = label or name
        self.swift = forms_for(name, self.label)
        self.forms = (self.swift.manifest, self.swift.xcode)

    def subject(self, tag: str) -> str:
        return "build: pin %s at %s" % (self.name, tag)

    def clone(self) -> str:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = self.name.upper().replace("-", "_") + "_PATH"
        return _local_clone(env, self.name + ".path", here)

    def resolve_target(self, tag: str) -> Target:
        repo = self.clone()
        return Target(self.name, tag, _verified_tag(repo, tag), repo)

    def read(self, root: str, entry: Optional[dict] = None) -> Reading:
        return self.swift.read(root)

    def _handled(self, root: str) -> Tuple[set, set]:
        """Files the editors report on: manifests and projects, and the resolved files they move."""
        manifests, resolved = set(), set()
        if self.swift.manifest.detect(root):
            manifests.add("Package.swift")
            resolved.add("Package.resolved")
        for rel, _text in self.swift.xcode._projects(root):
            manifests.add(rel)
            resolved.add(os.path.dirname(rel) + "/project.xcworkspace/" + RESOLVED_SUFFIX)
        return manifests, resolved

    def _parts(self, root: str, target: Target, library: bool) -> List[Pin]:
        return [f.inspect(root, target, library) for f in self.forms if f.detect(root)]

    def inspect(self, root: str, target: Target, entry: Optional[dict] = None) -> Pin:
        library = bool(entry and entry.get("library"))
        handled, handled_resolved = self._handled(root)
        reading = self.read(root, entry)
        refs: Dict[str, set] = {}
        for f in reading.forms:
            if "_ref" in f and f["file"] in handled:
                refs.setdefault(f["file"], set()).add(f["_ref"])
        for rel, found in sorted(refs.items()):
            if len(found) > 1:
                raise CheckError("%s has %d %s references; roll-out cannot tell which to move" % (rel, len(found), self.label))
        parts = self._parts(root, target, library)
        done = set()
        for f in reading.forms:
            req, res = f["requirement"], f["resolved"]
            if f["file"] not in handled and f["file"] not in done:
                done.add(f["file"])
                if req and req["type"] == "local":
                    parts.append(Pin("local", "", notes=["%s reaches %s by a local path; not edited" % (f["file"], self.label)]))
                elif library and req and req["type"] in RANGE_TYPES:
                    parts.append(Pin("range", req["version"] or "", notes=["%s: %s %s, a range in a library; not edited" % (f["file"], req["type"], req["version"] or "")]))
                else:
                    how = "%s %s" % (req["type"], req["version"] or "") if req else "a lock"
                    parts.append(Pin("manual", (res or {}).get("version") or (req or {}).get("version") or "",
                                     notes=["%s pins %s (%s); roll-out does not edit this file, change it by hand" % (f["file"], self.label, how.strip())]))
            elif res and f["file"] in handled and res["file"] not in handled_resolved and res["file"] not in done:
                done.add(res["file"])
                parts.append(Pin("manual", res["version"] or "",
                                 notes=["%s also pins %s %s; roll-out does not edit this file, change it by hand" % (res["file"], self.label, res["version"])]))
        if not parts:
            raise CheckError("%s is pinned in a form this version cannot describe" % self.label)
        return CombinedPin(parts)


RANGE_TYPES = ("range", "upToNextMinor", "upToNextMajor")


class CombinedPin(Pin):
    """One repository's pins across every form it matches; each part writes and checks itself."""

    ORDER = ("blocked", "update", "manual", "range", "current", "local")

    def __init__(self, parts: List[Pin]):
        status = next(st for st in self.ORDER if any(p.status == st for p in parts))
        files: Dict[str, bytes] = {}
        notes: List[str] = []
        for part in parts:
            for rel in part.files:
                if rel in files:
                    raise CheckError("two references edit " + rel)
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


SWIFT_PIN_TYPES = [SwiftPackagePin(n) for n in ("markdown-engine", "apple-dev-support", "apple-app-support", "apple-test-support")]
