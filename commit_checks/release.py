"""Version, CHANGELOG, tag and commit-message checks."""
from __future__ import annotations

import fnmatch
import json
import re
from typing import List, Optional, Set

from . import changes, gitutil
from .docs import read_index
from .errors import CheckError, Finding

PRESETS = {
    "xcode": r'MARKETING_VERSION\s*=\s*"?([0-9][^;"\s]*)"?\s*;|"MARKETING_VERSION"\s*:\s*"([^"]+)"',
    "pyproject": r'(?m)^version\s*=\s*"([^"]+)"',
    "plist": r"<key>CFBundleShortVersionString</key>\s*<string>([^<]+)</string>",
}
HEADING = re.compile(r"^## (?:\[v?(\d+\.\d+\.\d+[^\]\s]*)\]|v?(\d+\.\d+\.\d+[^\s]*))", re.M)
TYPES = "feat|fix|refactor|perf|test|docs|chore|ci|build|style|revert"
CONVENTIONAL = re.compile(r"^(?:%s)(?:\([^()\s]+\))?!?: \S" % TYPES)
EXEMPT_SUBJECT = re.compile(r'^(?:Merge |Revert "|fixup! |squash! )')


def _versions(ctx, source, text: Optional[str] = None) -> Set[str]:
    kind = source.get("type")
    path = source.get("path")
    if not path:
        raise CheckError("release.source needs a path")
    if path not in set(gitutil.index_paths(ctx.root)):
        raise CheckError("release.source file is not tracked: " + path)
    if text is None:
        text = read_index(ctx.root, path)
    if kind == "package-json":
        try:
            return {str(json.loads(text)["version"])}
        except (ValueError, KeyError, TypeError):
            raise CheckError("cannot read the version from " + path)
    pattern = source.get("pattern") if kind == "regex" else PRESETS.get(kind or "")
    if not pattern:
        raise CheckError("release.source.type must be package-json, xcode, pyproject, plist or regex")
    found = set()
    for m in re.finditer(pattern, text):
        value = next((g for g in m.groups() if g), None)
        if value:
            found.add(value.strip())
    if not found:
        raise CheckError("no version found in " + path)
    return found


def _changelog_top(ctx, path: str) -> Optional[str]:
    if path not in set(gitutil.index_paths(ctx.root)):
        return None
    m = HEADING.search(read_index(ctx.root, path))
    return (m.group(1) or m.group(2)) if m else ""


def check_versions(ctx) -> List[Finding]:
    """The CHANGELOG's top release heading matches the version source, and no code changes sit under a released tag."""
    conf = ctx.settings.data.get("release")
    if not conf:
        return []
    out: List[Finding] = []
    versions = _versions(ctx, conf.get("source", {}))
    if len(versions) != 1:
        return [Finding("versions", conf["source"]["path"], 0, "the version source holds more than one value", "versions")]
    version = next(iter(versions))
    changelog = conf.get("changelog", "CHANGELOG.md")
    top = _changelog_top(ctx, changelog)
    if top is None:
        out.append(Finding("versions", changelog, 0, "CHANGELOG is missing", "versions"))
    elif top != version:
        out.append(Finding("versions", changelog, 0, "top release heading does not match the version %s" % version, "versions"))
    # A version that changed in this commit must not reuse an existing release tag.
    head = gitutil.run(["show", "HEAD:" + conf["source"]["path"]], ctx.root, ok=(0, 128))
    if head.returncode == 0:
        try:
            before = _versions(ctx, conf["source"], head.stdout.decode("utf-8", "replace"))
        except CheckError:
            before = set()
        if before and before != versions:
            for tag in ("v" + version, version):
                if gitutil.run(["rev-parse", "-q", "--verify", "refs/tags/" + tag], ctx.root, ok=(0, 1)).returncode == 0:
                    out.append(Finding("versions", conf["source"]["path"], 0, "version %s is already released as tag %s" % (version, tag), "versions"))
    for tag in ("v" + version, version):
        proc = gitutil.run(["rev-parse", "-q", "--verify", "refs/tags/%s^{commit}" % tag], ctx.root, ok=(0, 1))
        if proc.returncode != 0:
            continue
        tagged = proc.stdout.decode().strip()
        changed = gitutil.split_z(gitutil.run(["diff", "--cached", "--name-only", "-z", "--no-renames", tagged], ctx.root).stdout)
        globs = conf.get("code", ["*"])
        docs = ctx.settings.data.get("docs_only")
        code = [p for p in changed
                if any(fnmatch.fnmatchcase(p, g) or fnmatch.fnmatchcase(p.rsplit("/", 1)[-1], g) for g in globs)
                and not (docs is not None and changes.is_doc(p, docs))]
        if code:
            out.append(Finding("versions", code[0], 0, "code changed under the released tag %s; bump the version" % tag, "versions"))
    return [f for f in out if not ctx.allowed(f)]


def _version_tuple(text: str):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def check_release_tags(ctx) -> List[Finding]:
    """Release tags (vX.Y.Z or X.Y.Z) are annotated."""
    proc = gitutil.run(["for-each-ref", "--format=%(refname:short)%09%(objecttype)", "refs/tags"], ctx.root)
    out = []
    since = ctx.settings.data.get("annotated_tags_from")
    floor = _version_tuple(since) if since else None
    if since and floor is None:
        raise CheckError("annotated_tags_from must look like 1.2.3")
    for row in proc.stdout.decode("utf-8", "replace").splitlines():
        name, _sep, kind = row.partition("\t")
        if floor is not None:
            ver = _version_tuple(name)
            if ver is not None and ver < floor:
                continue
        if re.match(r"^v?\d+\.\d+\.\d+", name) and kind != "tag":
            out.append(Finding("release-tags", name, 0, "release tag is not annotated", "release-tags"))
    return [f for f in out if not ctx.allowed(f)]


def check_conventional_commits(ctx) -> List[Finding]:
    if ctx.settings.data.get("conventional_commits") is False:
        return []
    subjects = []
    if ctx.source == "message":
        text = ctx.message or ""
        lines = [l for l in text.splitlines() if not l.startswith("#")]
        subjects = [("commit message", next((l for l in lines if l.strip()), ""))]
    elif ctx.source == "range":
        proc = gitutil.run(["log", "--format=%h%x09%s"] + (ctx.revspec or "").split(), ctx.root)
        for row in proc.stdout.decode("utf-8", "replace").splitlines():
            sha, _s, subject = row.partition("\t")
            subjects.append(("commit " + sha, subject))
    out = []
    limit = ctx.settings.data.get("subject_max")
    for label, subject in subjects:
        if EXEMPT_SUBJECT.match(subject):
            continue
        if isinstance(limit, int) and len(subject) > limit:
            f = Finding("conventional-commits", label, 0, "subject is longer than %d characters" % limit, "conventional-commits")
            if not ctx.allowed(f):
                out.append(f)
            continue
        if CONVENTIONAL.match(subject):
            continue
        f = Finding("conventional-commits", label, 0, "subject must look like type(scope): summary (types: %s)" % TYPES.replace("|", ", "), "conventional-commits")
        if not ctx.allowed(f):
            out.append(f)
    return out
