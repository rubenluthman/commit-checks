"""The private consumer list shared by `roll-out` and `pins`.

{
  "scanRoot": "folder to scan for repositories that load a package but are not listed",
  "scanSkip": ["folder", ...],
  "gates": {"<package>": ["command", "{tag}", "{pins}"]},
  "consumers": [{"name": "...", "path": "...", "packages": ["commit-checks", "judarn"],
                 "checks": {"<package>": ["command", "arg"]},
                 "files": {"<package>": "relative/path"},
                 "library": false}]
}

A consumer's optional `checks` gives, per package, a cheaper command to run in place of the full verification.
`files` names, per package, the file that holds a shell pin (`ai-output-evals`). `library: true` reports a version range
of a Swift package as RANGE instead of MANUAL. `scanSkip` lists folders, relative to `scanRoot`, that the scan for
unlisted repositories never enters.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .errors import CheckError
from .pins import PIN_TYPES


class Consumers:
    def __init__(self, base: str, entries: List[dict], scan_root: Optional[str], gates: Dict[str, List[str]],
                 scan_skip: Optional[List[str]] = None):
        self.base = base
        self.entries = entries  # name, path, packages
        self.scan_root = scan_root
        self.gates = gates
        self.scan_skip = scan_skip or []  # absolute folders under scan_root


def _inside(rel: str) -> bool:
    """A relative path that stays inside the folder it is relative to."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel) or rel.startswith("~"):
        return False
    norm = os.path.normpath(rel)
    return norm != "." and norm != ".." and not norm.startswith(".." + os.sep)


def load_list(path: str) -> Consumers:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CheckError("cannot read the consumer list: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("consumers"), list):
        raise CheckError("the consumer list needs a consumers array")
    if "gate" in data:
        raise CheckError('the consumer list has a "gate" key; use "gates", keyed by package name: {"gates": {"<package>": [command...]}}')
    base = os.path.dirname(os.path.abspath(path))

    def resolve(p: str) -> str:
        return os.path.normpath(os.path.join(base, os.path.expanduser(p)))

    entries, seen = [], set()
    for item in data["consumers"]:
        if not (isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("path"), str)
                and item["name"] and item["path"]):
            raise CheckError("every consumer needs a name and a path")
        name = item["name"]
        if name in seen:
            raise CheckError("consumer listed twice: " + name)
        seen.add(name)
        packages = item.get("packages")
        if not (isinstance(packages, list) and all(isinstance(p, str) for p in packages)):
            raise CheckError("consumer %s needs a packages list (the packages it loads)" % name)
        for pkg in packages:
            if pkg not in PIN_TYPES:
                raise CheckError("consumer %s names an unknown package %s (known: %s)" % (name, pkg, ", ".join(sorted(PIN_TYPES))))
        checks = item.get("checks", {})
        if not isinstance(checks, dict):
            raise CheckError("consumer %s: checks must be an object keyed by package name" % name)
        for pkg, cmd in checks.items():
            if pkg not in PIN_TYPES:
                raise CheckError("consumer %s: checks names an unknown package %s" % (name, pkg))
            if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) and a for a in cmd)):
                raise CheckError("consumer %s: the checks command for %s must be a list of command arguments" % (name, pkg))
        files = item.get("files", {})
        if not isinstance(files, dict):
            raise CheckError("consumer %s: files must be an object keyed by package name" % name)
        for pkg, rel in files.items():
            if pkg not in PIN_TYPES:
                raise CheckError("consumer %s: files names an unknown package %s" % (name, pkg))
            if not _inside(rel):
                raise CheckError("consumer %s: the file for %s must be a relative path inside the repository" % (name, pkg))
        library = item.get("library", False)
        if not isinstance(library, bool):
            raise CheckError("consumer %s: library must be true or false" % name)
        entries.append({"name": name, "path": resolve(item["path"]), "packages": sorted(set(packages)), "checks": checks,
                        "files": files, "library": library})
    scan = data.get("scanRoot")
    if scan is not None and not (isinstance(scan, str) and scan):
        raise CheckError("scanRoot must be a folder name")
    skip = data.get("scanSkip", [])
    if not (isinstance(skip, list) and all(_inside(s) for s in skip)):
        raise CheckError("scanSkip must be a list of folder names, relative to scanRoot and inside it")
    if skip and not scan:
        raise CheckError("scanSkip needs a scanRoot")
    gates = data.get("gates", {})
    if not isinstance(gates, dict):
        raise CheckError("gates must be an object keyed by package name")
    for pkg, cmd in gates.items():
        if pkg not in PIN_TYPES:
            raise CheckError("gates names an unknown package: " + pkg)
        if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) for a in cmd)):
            raise CheckError("the gate for %s must be a list of command arguments" % pkg)
    scan_root = resolve(scan) if scan else None
    return Consumers(base, entries, scan_root, gates, [os.path.normpath(os.path.join(scan_root, s)) for s in skip] if scan_root else [])


def not_found_text(package: str) -> str:
    return "listed as loading %s, but no pin found" % package


def not_listed_text(package: str) -> str:
    return "loads %s, but the list does not name it" % package
