"""Document checks on the staged snapshot: Markdown links and decision records."""
from __future__ import annotations

import fnmatch
import posixpath
import re
from typing import Dict, List, Set, Tuple
from urllib.parse import unquote

from . import gitutil
from .errors import CheckError, Finding

INLINE = re.compile(r"\]\(\s*(<[^>\n]*>|[^)\s]+)(?:\s+(?:\"[^\"]*\"|'[^']*'))?\s*\)")
REFERENCE = re.compile(r"^ {0,3}\[[^\]\n]+\]:\s*(<[^>\n]*>|\S+)", re.M)
SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
INLINE_CODE = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
LINE_SUFFIX = re.compile(r":\d+(?::\d+)?$")


def read_index(root: str, path: str) -> str:
    """The staged content of one file."""
    return gitutil.run(["show", ":" + path], root).stdout.decode("utf-8", "replace")


def strip_code(text: str) -> str:
    out, fence = [], None
    for line in text.split("\n"):
        stripped = line.lstrip()
        if fence is None:
            m = re.match(r"(`{3,}|~{3,})", stripped)
            if m and len(line) - len(stripped) <= 3:
                fence = m.group(1)
                out.append("")
                continue
            out.append(line)
        else:
            if stripped.startswith(fence[0] * len(fence)) and stripped.strip(fence[0]) == "":
                fence = None
            out.append("")
    return INLINE_CODE.sub("", "\n".join(out))


def _targets(text: str):
    for m in INLINE.finditer(text):
        yield m.group(1)
    for m in REFERENCE.finditer(text):
        yield m.group(1)


def _known(paths: List[str]) -> Tuple[Set[str], Set[str]]:
    files = set(paths)
    dirs = set()
    for p in paths:
        parts = p.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    return files, dirs


def check_links(ctx) -> List[Finding]:
    """Relative Markdown links must point at tracked files or folders, with exact case."""
    paths = gitutil.index_paths(ctx.root)
    files, dirs = _known(paths)
    exclude = ctx.settings.data.get("links_exclude", [])
    out: List[Finding] = []
    for f in sorted(p for p in paths if p.endswith(".md")):
        if any(fnmatch.fnmatchcase(f, pat) for pat in exclude):
            continue
        text = strip_code(read_index(ctx.root, f))
        for raw in _targets(text):
            target = raw.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1].strip()
            if target.lower().startswith("file:"):
                out.append(Finding("links", f, 0, "file: link is machine-specific", "links"))
                continue
            if not target or SCHEME.match(target) or target.startswith("//") or target.startswith("#"):
                continue
            path = unquote(re.split(r"[#?]", target, maxsplit=1)[0])
            if not path:
                continue
            resolved = posixpath.normpath(path.lstrip("/")) if path.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(f), path))
            if resolved.startswith(".."):
                out.append(Finding("links", f, 0, "link escapes the repository: " + target, "links"))
                continue
            if resolved in files or resolved in dirs or resolved == ".":
                continue
            stripped = LINE_SUFFIX.sub("", resolved)
            if stripped != resolved and (stripped in files or stripped in dirs):
                continue
            out.append(Finding("links", f, 0, "broken link: " + target, "links"))
    return [f for f in out if not ctx.allowed(f)]


# ---------------------------------------------------------------- decision records

ADR_DIR = "docs/adr"
INDEX_PATH = ADR_DIR + "/README.md"
ADR_FILE = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
HEADINGS = ["## Decision", "## Why", "## What we gave up", "## Revisit if"]
STATUS_LINE = re.compile(r"^Status: (Active|Retired) · (\d{4}-\d{2}-\d{2})$")
EM_DASH = chr(0x2014)
VALE_DIRECTIVE = re.compile(r"^<!-- vale [^>]*-->\s*$")


def check_adr_format(ctx) -> List[Finding]:
    paths = [p for p in gitutil.index_paths(ctx.root) if p.startswith(ADR_DIR + "/") and "/" not in p[len(ADR_DIR) + 1:]]
    if not paths:
        return []
    exempt = set(ctx.settings.data.get("adr_exempt", []))
    errors: List[str] = []
    adrs: List[Dict[str, str]] = []
    seen: Set[str] = set()
    names = sorted(p[len(ADR_DIR) + 1:] for p in paths if p != INDEX_PATH)
    for name in names:
        label = "%s/%s" % (ADR_DIR, name)
        m = ADR_FILE.match(name)
        if not m:
            errors.append("%s: file name must be NNNN-title.md" % label)
            continue
        adr_id = m.group(1)
        raw = read_index(ctx.root, label)
        # Vale directive comments (to switch a rule off around a title) are not part of the record's text.
        text = "\n".join(l for l in raw.split("\n") if not VALE_DIRECTIVE.match(l))
        lines = text.split("\n")
        if adr_id in seen:
            errors.append("%s: duplicate number %s" % (label, adr_id))
        seen.add(adr_id)
        title = lines[0][2:].strip() if lines[0].startswith("# ") else None
        if adr_id in exempt:
            title = title or next((l[2:].strip() for l in lines if l.startswith("# ")), None)
            if not title:
                errors.append("%s: first line must be \"# Title\"" % label)
            adrs.append({"id": adr_id, "file": name, "title": title or "", "status": ""})
            continue
        if text.startswith("---"):
            errors.append("%s: frontmatter is not used; start with the title line" % label)
        if EM_DASH in text:
            errors.append("%s: contains an em dash (U+2014); use a spaced en dash" % label)
        if not title:
            errors.append("%s: first line must be \"# Title\"" % label)
        sm = STATUS_LINE.match(lines[1] if len(lines) > 1 else "")
        if not sm:
            errors.append("%s: second line must be \"Status: Active | Retired · YYYY-MM-DD\"" % label)
        found = [l for l in lines if l.startswith("## ")]
        if found != HEADINGS:
            errors.append("%s: headings must be exactly Decision, Why, What we gave up, Revisit if, in that order" % label)
        for heading in HEADINGS:
            if heading not in lines:
                continue
            start = lines.index(heading)
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
            if not any(l.strip() for l in lines[start + 1:end]):
                errors.append("%s: \"%s\" is empty" % (label, heading[3:]))
        adrs.append({"id": adr_id, "file": name, "title": title or "", "status": sm.group(1) if sm else ""})
    if INDEX_PATH not in paths:
        errors.append("%s: file is missing" % INDEX_PATH)
    else:
        rows: Dict[str, Dict[str, str]] = {}
        for line in read_index(ctx.root, INDEX_PATH).split("\n"):
            cells = [c.strip() for c in line.split("|")]
            link = re.match(r"^\[(\d{4})\]\(([^)]+)\)$", cells[1] if len(cells) > 1 else "")
            if link:
                rows[link.group(1)] = {"file": link.group(2), "title": cells[2] if len(cells) > 2 else "", "status": cells[3] if len(cells) > 3 else ""}
        for adr in adrs:
            row = rows.get(adr["id"])
            if row is None:
                errors.append("%s: ADR-%s is missing from the index" % (INDEX_PATH, adr["id"]))
            elif row["file"] != adr["file"] or row["title"] != adr["title"] or (adr["id"] not in exempt and row["status"] != adr["status"]):
                errors.append("%s: ADR-%s row differs from the record (link, title, or status)" % (INDEX_PATH, adr["id"]))
        for adr_id in rows:
            if not any(a["id"] == adr_id for a in adrs):
                errors.append("%s: ADR-%s has no matching record" % (INDEX_PATH, adr_id))
    return [Finding("adr-format", "docs/adr", 0, e, "adr-format") for e in errors if not ctx.allowed(Finding("adr-format", "docs/adr", 0, e, "adr-format"))]
