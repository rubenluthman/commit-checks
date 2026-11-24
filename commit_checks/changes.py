"""docs-only: tell a repository's verify script whether a change touches only docs."""
from __future__ import annotations

import fnmatch
import os
from typing import Any, List, Tuple

from . import gitutil


def changed_files(root: str) -> List[str]:
    """Staged and unstaged tracked changes (both sides of a rename), plus untracked files that are not ignored."""
    tracked = gitutil.split_z(gitutil.run(["diff", "HEAD", "--name-only", "-z", "--no-renames"], root).stdout)
    untracked = gitutil.split_z(gitutil.run(["ls-files", "--others", "--exclude-standard", "-z"], root).stdout)
    return sorted(set(tracked) | set(untracked))


def is_doc(path: str, conf: Any) -> bool:
    # The whole path only, never the bare file name: "README.md" must not cover "src/Button/README.md".
    match = lambda pats: any(fnmatch.fnmatchcase(path, p) for p in pats)
    return match(conf["paths"]) and not match(conf.get("except", []))


def classify(root: str, data: dict) -> Tuple[bool, str]:
    """(True, why) only when every changed file is provably docs. Anything unclear counts as code."""
    conf = data.get("docs_only")
    if conf is None:
        return False, "code: no docs_only setting"
    if os.environ.get("CI"):
        return False, "code: CI is set"
    if gitutil.run(["rev-parse", "--verify", "--quiet", "HEAD"], root, ok=(0, 1)).returncode != 0:
        return False, "code: no commits yet"
    files = changed_files(root)
    if not files:
        return False, "code: no changes (verify the whole repository)"
    code = [p for p in files if not is_doc(p, conf)]
    if code:
        more = " (and %d more)" % (len(code) - 1) if len(code) > 1 else ""
        return False, "code: %s%s" % (code[0], more)
    return True, "docs-only: %d changed file%s, all docs" % (len(files), "" if len(files) == 1 else "s")
