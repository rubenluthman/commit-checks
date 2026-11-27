"""The private consumer list shared by `roll-out` and `pins`.

{
  "scanRoot": "folder to scan for repositories that load a package but are not listed",
  "gates": {"<package>": ["command", "{tag}", "{pins}"]},
  "consumers": [{"name": "...", "path": "...", "packages": ["commit-checks", "judarn"],
                 "checks": {"<package>": ["command", "arg"]}}]
}

A consumer's optional `checks` gives, per package, a cheaper command to run in place of the full verification.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .errors import CheckError
from .pins import PIN_TYPES


class Consumers:
    def __init__(self, base: str, entries: List[dict], scan_root: Optional[str], gates: Dict[str, List[str]]):
        self.base = base
        self.entries = entries  # name, path, packages
        self.scan_root = scan_root
        self.gates = gates


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
        entries.append({"name": name, "path": resolve(item["path"]), "packages": sorted(set(packages)), "checks": checks})
    scan = data.get("scanRoot")
    if scan is not None and not (isinstance(scan, str) and scan):
        raise CheckError("scanRoot must be a folder name")
    gates = data.get("gates", {})
    if not isinstance(gates, dict):
        raise CheckError("gates must be an object keyed by package name")
    for pkg, cmd in gates.items():
        if pkg not in PIN_TYPES:
            raise CheckError("gates names an unknown package: " + pkg)
        if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) for a in cmd)):
            raise CheckError("the gate for %s must be a list of command arguments" % pkg)
    return Consumers(base, entries, resolve(scan) if scan else None, gates)


def not_found_text(package: str) -> str:
    return "listed as loading %s, but no pin found" % package


def not_listed_text(package: str) -> str:
    return "loads %s, but the list does not name it" % package
