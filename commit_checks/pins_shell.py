"""Pin type for a package pinned by two shell variables, `<PREFIX>_TAG="vX.Y.Z"` and `<PREFIX>_COMMIT="<40 hex>"`.

The file that holds them comes from the consumer's list entry (`"files": {"<package>": "scripts/eval.sh"}`). The prefix
belongs to the type, so one more shell-pinned package is one more registration in `SHELL_PIN_TYPES`.
Both lines must appear exactly once and be well formed; anything else is refused, never guessed. The edit changes the
two values in place and leaves every other byte of the file alone.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

from .errors import CheckError
from .pins import SHA, TAG, Pin, PinType, Reading, Target, _local_clone, _verified_tag, form
from .pins_swift import _read_tracked


class ShellPin(PinType):
    def __init__(self, name: str, prefix: str):
        self.name = name
        self.prefix = prefix
        self.env = name.upper().replace("-", "_") + "_PATH"
        # Any line that assigns the variable, however it is written: counted to refuse a doubled or a malformed one.
        self.loose = {key: re.compile(r"^[ \t]*(?:export[ \t]+)?%s_%s[ \t]*=" % (re.escape(prefix), key), re.M) for key in ("TAG", "COMMIT")}
        self.strict = {key: re.compile(r'^(?P<head>(?:export[ \t]+)?%s_%s=")(?P<v>[^"\r\n]*)(?P<tail>"[ \t]*(?:#[^\r\n]*)?)(?=\r?$)' % (re.escape(prefix), key), re.M)
                       for key in ("TAG", "COMMIT")}

    def subject(self, tag: str) -> str:
        return "build: pin %s at %s" % (self.name, tag)

    def clone(self) -> str:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return _local_clone(self.env, self.name + ".path", here)

    def resolve_target(self, tag: str) -> Target:
        repo = self.clone()
        return Target(self.name, tag, _verified_tag(repo, tag), repo)

    def _file(self, entry: Optional[dict]) -> Optional[str]:
        rel = ((entry or {}).get("files") or {}).get(self.name)
        return rel if isinstance(rel, str) and rel else None

    def _values(self, text: str) -> Tuple[Optional[str], Optional[str], List[str]]:
        """(tag, commit, problems): a value is None when its line is missing, doubled or malformed."""
        found: Dict[str, Optional[str]] = {}
        problems: List[str] = []
        for key, shape in (("TAG", TAG), ("COMMIT", SHA)):
            var = "%s_%s" % (self.prefix, key)
            count = len(self.loose[key].findall(text))
            good = list(self.strict[key].finditer(text))
            if count == 0:
                problems.append("no %s line" % var)
            elif count > 1:
                problems.append("%s is set %d times; expected exactly one line" % (var, count))
            elif len(good) != 1:
                problems.append('%s is not written as %s="..."' % (var, var))
            elif not shape.match(good[0].group("v")):
                problems.append("%s holds %r, not %s" % (var, good[0].group("v"), "a tag like v1.2.3" if key == "TAG" else "a 40-character commit"))
            else:
                found[key] = good[0].group("v")
                continue
            found[key] = None
        return found["TAG"], found["COMMIT"], problems

    def read(self, root: str, entry: Optional[dict] = None) -> Reading:
        rel = self._file(entry)
        if rel is None:
            return Reading()
        text = _read_tracked(root, rel)
        if text is None:
            return Reading([], ["%s is not a tracked file" % rel])
        tag, commit, problems = self._values(text)
        if tag is None or commit is None:
            return Reading([], ["%s: %s" % (rel, p) for p in problems])
        resolved = {"file": rel, "version": tag, "revision": commit, "branch": None}
        return Reading([form("shell", rel, {"type": "exact", "version": tag}, resolved, True)])

    def inspect(self, root: str, target: Target, entry: Optional[dict] = None) -> Pin:
        rel = self._file(entry)
        if rel is None:
            raise CheckError('the list entry has no "files" path for %s' % self.name)
        text = _read_tracked(root, rel)
        if text is None:
            raise CheckError("%s is not a tracked file" % rel)
        old, commit, problems = self._values(text)
        if problems:
            raise CheckError("%s: %s" % (rel, "; ".join(problems)))
        if (old, commit) == (target.tag, target.commit):
            return Pin("current", old)
        new = text
        for key, value in (("TAG", target.tag), ("COMMIT", target.commit)):
            new = self.strict[key].sub(lambda m, v=value: m.group("head") + v + m.group("tail"), new)
        after = self._values(new)
        if after != (target.tag, target.commit, []):
            raise CheckError("%s edit did not produce the new pin" % rel)
        return Pin("update", old, {rel: new.encode("utf-8")})


SHELL_PIN_TYPES = [ShellPin("ai-output-evals", "EVALS")]
