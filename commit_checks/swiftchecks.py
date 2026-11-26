"""Checks for Swift apps: stored-setting keys change only with a migration, and public log values carry a reason.

The Swift reader is a small tokenizer, not a parser. It sees comments, strings (plain, raw, multi-line and
interpolated) and punctuation well enough to find the key given to @AppStorage, @SceneStorage, UserDefaults
calls and `FolderBookmark(key:)`, and the string constants those calls name. A key built at run time cannot be seen.
"""
from __future__ import annotations

import fnmatch
import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from . import gitutil
from .docs import read_index
from .errors import CheckError, Finding

SETTINGS_CHECK = "settings-keys"
LOG_CHECK = "log-privacy"

# UserDefaults methods that take forKey:. Dictionary calls (removeValue, updateValue) and Codable
# containers (encode, decode) are not on the list, so their keys are never read as settings.
DEFAULTS_METHODS = {"set", "object", "string", "bool", "integer", "double", "float", "array", "dictionary", "data",
                    "url", "stringArray", "removeObject", "value", "setValue"}
TYPE_WORDS = {"enum", "struct", "class", "extension", "actor", "protocol"}
NOT_NAMES = {"func", "var", "let", "init", "subscript", "where", "case"}
# setValue(_:forKey:) and value(forKey:) are key-value coding, which every Objective-C object has (an image filter's
# inputs, for one). They count only when called on something named like defaults: `defaults`, `UserDefaults.standard`.
KVC_METHODS = {"setValue", "value"}
STORAGE_WRAPPERS = {"AppStorage", "SceneStorage"}
# Types that store a value in UserDefaults under the key their initializer takes as `key:` (apple-app-support).
KEYED_STORES = {"FolderBookmark"}
IGNORED_CONTAINERS = {"CodingKeys"}


class Tok(NamedTuple):
    kind: str  # id | str | istr (a string with interpolation) | p
    value: str
    line: int
    start: int
    end: int


_STR_OPEN = re.compile(r'(#*)("{3}|")')
_WORD = re.compile(r"\w+")


def _skip_string(text: str, i: int, hashes: int, delim: str) -> Tuple[int, str, bool]:
    """i is just past the opening delimiter. Returns (index past the closing delimiter, content, has interpolation)."""
    n = len(text)
    esc = "\\" + "#" * hashes
    end = delim + "#" * hashes
    out = []  # type: List[str]
    interp = False
    while i < n:
        if text.startswith(esc, i):
            j = i + len(esc)
            if j < n and text[j] == "(":
                interp = True
                depth, j = 1, j + 1
                while j < n and depth:
                    c = text[j]
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                    elif c == '"':
                        m = _STR_OPEN.match(text, j)
                        j, _inner, _ = _skip_string(text, m.end(), len(m.group(1)), m.group(2))
                        continue
                    j += 1
                i = j
            else:
                out.append(text[i:j + 1])
                i = j + 1
        elif text.startswith(end, i):
            return i + len(end), "".join(out), interp
        else:
            out.append(text[i])
            i += 1
    raise CheckError("unterminated string in a Swift file")


def tokenize(text: str) -> List[Tok]:
    toks = []  # type: List[Tok]
    i, n, line = 0, len(text), 1
    while i < n:
        c = text[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            depth, i = 1, i + 2
            while i < n and depth:
                if text.startswith("/*", i):
                    depth, i = depth + 1, i + 2
                elif text.startswith("*/", i):
                    depth, i = depth - 1, i + 2
                else:
                    line += text[i] == "\n"
                    i += 1
        else:
            m = _STR_OPEN.match(text, i)
            if m:
                j, content, interp = _skip_string(text, m.end(), len(m.group(1)), m.group(2))
                toks.append(Tok("istr" if interp else "str", content, line, i, j))
                line += text.count("\n", i, j)
                i = j
                continue
            w = _WORD.match(text, i)
            if w:
                toks.append(Tok("id", w.group(0), line, i, w.end()))
                i = w.end()
            else:
                toks.append(Tok("p", c, line, i, i + 1))
                i += 1
    return toks


def code_text(text: str, toks: List[Tok]) -> str:
    """The source without comments, for finding a migration's name."""
    out, prev = [], 0
    for t in toks:
        if t.start > prev:
            out.append(" ")
        out.append(text[t.start:t.end])
        prev = t.end
    return "".join(out)


class Const(NamedTuple):
    container: Optional[str]
    name: str
    value: str


class Use(NamedTuple):
    literal: Optional[str]  # the key, when written as a string
    ref: Optional[List[str]]  # or the dotted name of a constant
    line: int
    containers: List[str]  # the types around the use, outermost first


def _arg(toks: List[Tok], j: int):
    """The key argument starting at token j: ('lit', text) or ('ref', [names]) or None when it can't be read."""
    if j >= len(toks):
        return None
    t = toks[j]
    if t.kind == "str":
        nxt = toks[j + 1] if j + 1 < len(toks) else None
        return ("lit", t.value) if nxt is not None and nxt.value in (",", ")") else None
    if t.kind != "id":
        return None
    names = [t.value]
    j += 1
    while j + 1 < len(toks) and toks[j].value == "." and toks[j + 1].kind == "id":
        names.append(toks[j + 1].value)
        j += 2
    if j >= len(toks) or toks[j].value not in (",", ")"):
        return None
    if names[-1] == "rawValue" and len(names) > 1:
        names.pop()
    return ("ref", names)


def _defaults_call(method: str, receiver: str) -> bool:
    if method not in DEFAULTS_METHODS:
        return False
    return method not in KVC_METHODS or "defaults" in receiver.lower() or receiver == "standard"


def analyse(toks: List[Tok]) -> Tuple[List[Const], List[Use]]:
    consts, uses = [], []  # type: List[Const], List[Use]
    braces = []  # type: List[Optional[str]]
    parens = []  # type: List[Tuple[str, str]]  # (method name, receiver) for each open parenthesis
    pending = None  # type: Optional[str]
    n = len(toks)
    for i, t in enumerate(toks):
        if t.kind == "p":
            if t.value == "{":
                braces.append(pending)
                pending = None
            elif t.value == "}":
                if braces:
                    braces.pop()
            elif t.value == "(":
                method = toks[i - 1].value if i and toks[i - 1].kind == "id" else ""
                receiver = toks[i - 3].value if i >= 3 and toks[i - 2].value == "." and toks[i - 3].kind == "id" else ""
                parens.append((method, receiver))
            elif t.value == ")":
                if parens:
                    parens.pop()
            continue
        if t.kind != "id":
            continue
        v = t.value
        container = next((b for b in reversed(braces) if b), None)
        if v in TYPE_WORDS and i + 1 < n and toks[i + 1].kind == "id" and toks[i + 1].value not in NOT_NAMES:
            j = i + 1
            while j + 2 < n and toks[j + 1].value == "." and toks[j + 2].kind == "id":
                j += 2
            pending = toks[j].value
        elif v in ("let", "var") and i + 1 < n and toks[i + 1].kind == "id":
            j = i + 2
            if j < n and toks[j].value == ":":
                j += 1
                while j < n and (toks[j].kind == "id" or toks[j].value in "?."):
                    j += 1
            if j + 1 < n and toks[j].value == "=" and toks[j + 1].kind == "str":
                after = toks[j + 2].value if j + 2 < n else ""
                if after != "+":
                    consts.append(Const(container, toks[i + 1].value, toks[j + 1].value))
        elif v == "case":
            j = i + 1
            while j < n and toks[j].kind == "id":
                if j + 2 < n and toks[j + 1].value == "=" and toks[j + 2].kind == "str":
                    consts.append(Const(container, toks[j].value, toks[j + 2].value))
                    j += 3
                    if j < n and toks[j].value == ",":
                        j += 1
                        continue
                break
        else:
            arg = None
            if v in STORAGE_WRAPPERS and i and toks[i - 1].value == "@" and i + 1 < n and toks[i + 1].value == "(":
                arg = _arg(toks, i + 2)
            elif (v in KEYED_STORES and i + 3 < n and toks[i + 1].value == "(" and toks[i + 2].value == "key"
                  and toks[i + 3].value == ":"):
                arg = _arg(toks, i + 4)
            elif v == "forKey" and i + 1 < n and toks[i + 1].value == ":" and parens and _defaults_call(*parens[-1]):
                arg = _arg(toks, i + 2)
            if arg:
                names = [b for b in braces if b]
                if arg[0] == "lit":
                    uses.append(Use(arg[1], None, t.line, names))
                else:
                    uses.append(Use(None, arg[1], t.line, names))
    return consts, uses


def _resolve(use: Use, consts: Dict[str, List[Const]]) -> Set[str]:
    names = use.ref or []
    found = [c for c in consts.get(names[-1], []) if c.container not in IGNORED_CONTAINERS]
    qual = names[-2] if len(names) > 1 else None
    if qual in ("Self", "self"):
        qual = use.containers[-1] if use.containers else None
    if qual:
        exact = [c for c in found if c.container == qual]
        found = exact or found
    else:
        found = [c for c in found if c.container is None or c.container in use.containers]
    return {c.value for c in found}


def _config(ctx) -> Optional[dict]:
    return ctx.settings.data.get("settings_keys")


def check_settings_keys(ctx) -> List[Finding]:
    conf = _config(ctx)
    if conf is None:
        return []
    paths = [p for p in gitutil.index_paths(ctx.root) if any(fnmatch.fnmatchcase(p, g) for g in conf["paths"])]
    if not paths:
        raise CheckError("settings_keys.paths matches no tracked file")
    consts = {}  # type: Dict[str, List[Const]]
    per_file = []  # type: List[Tuple[str, List[Use]]]
    code = []  # type: List[str]
    for path in sorted(paths):
        text = read_index(ctx.root, path)
        toks = tokenize(text)
        file_consts, uses = analyse(toks)
        for c in file_consts:
            consts.setdefault(c.name, []).append(c)
        per_file.append((path, uses))
        code.append(code_text(text, toks))
    live = list(conf.get("live", []))
    retired = [e["key"] for e in conf.get("retired", [])]
    system = list(conf.get("system", []))
    out = []  # type: List[Finding]
    seen = {}  # type: Dict[str, str]
    for label, keys in (("live", live), ("retired", retired), ("system", system)):
        for key in keys:
            if key in seen:
                where = "twice in " + label if seen[key] == label else "in both %s and %s" % (seen[key], label)
                out.append(Finding(SETTINGS_CHECK, ".commit-checks.json", 0,
                                   "key %r is listed %s; keep it in one list" % (key, where), SETTINGS_CHECK))
            else:
                seen[key] = label
    found = set()  # type: Set[str]
    for path, uses in per_file:
        for use in uses:
            keys = {use.literal} if use.literal is not None else _resolve(use, consts)
            for key in sorted(keys):
                found.add(key)
                if key not in seen:
                    out.append(Finding(SETTINGS_CHECK, path, use.line,
                                       "stored key %r is not registered; add it to settings_keys.live "
                                       "(settings_keys.system if the app only reads it)" % key, SETTINGS_CHECK))
    for key in live:
        if key not in found:
            out.append(Finding(SETTINGS_CHECK, ".commit-checks.json", 0,
                               "live key %r is no longer in the code; move it to settings_keys.retired and name its migration" % key,
                               SETTINGS_CHECK))
    joined = "\n".join(code)
    for entry in conf.get("retired", []):
        if entry["migration"] not in joined:
            out.append(Finding(SETTINGS_CHECK, ".commit-checks.json", 0,
                               "retired key %r names migration %r, which does not appear in the files under settings_keys.paths; "
                               "write the migration or fix its name" % (entry["key"], entry["migration"]), SETTINGS_CHECK))
    return [f for f in out if not ctx.allowed(f)]


PUBLIC_TRIGGER = re.compile(r"privacy:\s*\.public\b|%\{public[,}]")
PUBLIC_REASON = re.compile(r"//\s*public:\s*\S")


def check_log_privacy(ctx) -> List[Finding]:
    proc = gitutil.run(["grep", "--cached", "-I", "-n", "-z", "-F", "-e", "privacy:", "-e", "%{public", "--", "*.swift"],
                       ctx.root, ok=(0, 1))
    out = []  # type: List[Finding]
    for row in proc.stdout.decode("utf-8", "surrogateescape").split("\n"):
        if not row:
            continue
        parts = row.split("\0", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            raise CheckError("unexpected git grep output")
        path, number, line = parts[0], int(parts[1]), parts[2]
        m = PUBLIC_TRIGGER.search(line)
        if m and not PUBLIC_REASON.search(line, m.end()):
            f = Finding(LOG_CHECK, path, number,
                        "a public log value needs a same-line comment: // public: <reason>", LOG_CHECK)
            if not ctx.allowed(f):
                out.append(f)
    return out
