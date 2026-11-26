"""Checks that repositories used to copy into their own verify scripts: rule globs, Swift file names,
staged binary media, em dashes in file types Vale does not read, and hook drift.
"""
from __future__ import annotations

import fnmatch
import os
import pathlib
import re
from typing import List, Optional

from . import gitutil
from .docs import read_index
from .errors import CheckError, Finding

RULES_DIR = ".agents/rules"
HOOK_MARKER = "# --- repository checks ---"
HOOKS = ("pre-commit", "commit-msg", "pre-push")


# ---------------------------------------------------------------- rule globs

def expand_braces(text: str) -> List[str]:
    """`a/*.{x,y}` becomes `a/*.x` and `a/*.y`; nested and repeated groups expand too."""
    results = [text]
    while True:
        expanded = []  # type: List[str]
        found = False
        for s in results:
            m = re.search(r"\{([^{}]*)\}", s)
            if m:
                found = True
                for opt in m.group(1).split(","):
                    expanded.append(s[:m.start()] + opt.strip() + s[m.end():])
            else:
                expanded.append(s)
        results = expanded
        if not found:
            return results


def _frontmatter(text: str) -> str:
    """The block between the leading `---` lines; the whole text when there is none."""
    m = re.match(r"\s*---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(\r?\n|$)", text, re.S)
    return m.group(1) if m else text


def _field(block: str, name: str) -> Optional[str]:
    m = re.search(r"^%s:[ \t]*(.*?)[ \t]*$" % re.escape(name), block, re.M)
    if not m:
        return None
    value = m.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def check_rule_globs(ctx) -> List[Finding]:
    """Every `trigger: glob` rule must match at least one tracked file, so a rename cannot orphan it."""
    paths = gitutil.index_paths(ctx.root)
    rules = sorted(p for p in paths if p.startswith(RULES_DIR + "/") and p.endswith(".md") and "/" not in p[len(RULES_DIR) + 1:])
    if not rules:
        return []
    out = []  # type: List[Finding]
    for rule in rules:
        block = _frontmatter(read_index(ctx.root, rule))
        if _field(block, "trigger") != "glob":
            continue
        raw = _field(block, "glob")
        if not raw:
            out.append(Finding("rule-globs", rule, 0, "trigger is glob but no glob pattern is set", "rule-globs"))
            continue
        matched = any(pathlib.PurePath(p).match(pat) for pat in expand_braces(raw) if pat for p in paths)
        if not matched:
            out.append(Finding("rule-globs", rule, 0, "glob '%s' matches no tracked file" % raw, "rule-globs"))
    return [f for f in out if not ctx.allowed(f)]


# ---------------------------------------------------------------- Swift file names

SWIFT_NAME = re.compile(r"^[A-Z][A-Za-z0-9]*(\+[A-Z][A-Za-z0-9]*)*\.swift$")
SWIFT_EXCEPTIONS = {"main.swift"}
SWIFT_MANIFEST = re.compile(r"^Package(@swift-[0-9.]+)?\.swift$")


def check_swift_filenames(ctx) -> List[Finding]:
    out = []  # type: List[Finding]
    for p in gitutil.index_paths(ctx.root):
        if not p.endswith(".swift"):
            continue
        base = p.rsplit("/", 1)[-1]
        if base in SWIFT_EXCEPTIONS or SWIFT_MANIFEST.match(base) or SWIFT_NAME.match(base):
            continue
        out.append(Finding("swift-filenames", p, 0, "Swift file name is not PascalCase (Type.swift or Type+Extension.swift)", "swift-filenames"))
    return [f for f in out if not ctx.allowed(f)]


# ---------------------------------------------------------------- binary media

BINARY_EXT = re.compile(
    r"\.(png|jpe?g|gif|webp|heic|heif|bmp|tiff?|ico|m4a|caf|wav|mp3|aac|flac|ogg|aiff?|mp4|mov|m4v|avi|mkv|webm"
    r"|zip|tar|tar\.gz|tgz|gz|bz2|xz|7z|rar)$", re.I)


def check_binary_media(ctx) -> List[Finding]:
    """Staged images, audio, video and archives fail unless `binary_allow` lists them. Off without that key."""
    allow = ctx.settings.data.get("binary_allow")
    if allow is None:
        return []
    # Added or modified only: a rename of a file that is already tracked is not new media.
    paths = gitutil.split_z(gitutil.run(["diff", "--cached", "--name-only", "--diff-filter=AM", "-z"], ctx.root).stdout)
    out = []  # type: List[Finding]
    for p in paths:
        if BINARY_EXT.search(p) and not any(fnmatch.fnmatchcase(p, g) for g in allow):
            out.append(Finding("binary-media", p, 0, "staged binary media or archive (list it in binary_allow if it belongs here)", "binary-media"))
    return [f for f in out if not ctx.allowed(f)]


# ---------------------------------------------------------------- plain-text dashes

EM_DASH = chr(0x2014)
DASH_FILES = ["*.yml", "*.yaml", "*.sh", "*.toml", "*.svg"]


def check_dashes_plain(ctx) -> List[Finding]:
    """Vale does not read these file types, so the em dash rule is a plain search."""
    proc = gitutil.run(["grep", "--cached", "-I", "-l", "-z", "-e", EM_DASH, "--"] + DASH_FILES + [":!docs/private/*"],
                       ctx.root, ok=(0, 1))
    out = [Finding("dashes-plain", p, 0, "em dash found (use a spaced en dash)", "dashes-plain") for p in gitutil.split_z(proc.stdout)]
    return [f for f in out if not ctx.allowed(f)]


# ---------------------------------------------------------------- hook drift

def _template_hook(hook: str) -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "githooks", hook)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise CheckError("cannot read the hook template %s: %s" % (hook, exc))


def _header(text: str) -> Optional[List[str]]:
    """The lines up to and including the marker line, or None when there is no marker."""
    lines = text.replace("\r\n", "\n").split("\n")
    for i, line in enumerate(lines):
        if line.rstrip() == HOOK_MARKER:
            return [l.rstrip() for l in lines[:i + 1]]
    return None


def hook_drift(ctx, hook: str, path: str) -> List[str]:
    """Problems with one hook file: its part above the marker must match the pinned release's template."""
    if ctx.settings.is_self:
        return []
    want = _header(_template_hook(hook))
    if want is None:
        raise CheckError("the hook template %s has no %r line" % (hook, HOOK_MARKER))
    with open(path, encoding="utf-8", errors="replace") as fh:
        have = _header(fh.read())
    if have is None:
        return [".githooks/%s has no '%s' line: copy templates/githooks/%s from the pinned release and put this repository's own "
                "commands below the marker" % (hook, HOOK_MARKER, hook)]
    if have != want:
        return [".githooks/%s differs from the pinned release's template above the '%s' line: copy the template's lines above "
                "it and keep this repository's own commands below" % (hook, HOOK_MARKER)]
    return []
