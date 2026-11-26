"""pins: read which release of each package every listed repository pins. Read-only: no tag, no network, no edits.

Exit 0 when the report is complete (findings are data), 2 when it cannot run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional, Tuple

from .errors import CheckError
from .consumerlist import Consumers, load_list, not_found_text, not_listed_text
from .pins import PIN_TYPES, PinType, Reading

FORMAT = 1


def consumer_report(entry: dict, ptypes: List[PinType]) -> dict:
    exists = os.path.isdir(entry["path"])
    packages = {}
    for ptype in ptypes:
        declared = ptype.name in entry["packages"]
        reading = ptype.read(entry["path"]) if exists else Reading()
        detected = bool(reading.forms)
        problems = list(reading.problems)
        if declared and not exists:
            problems.append("folder not found")
        elif declared and not detected:
            problems.append(not_found_text(ptype.name))
        elif detected and not declared:
            problems.append(not_listed_text(ptype.name))
        packages[ptype.name] = {"declared": declared, "detected": detected, "forms": reading.public_forms(),
                                "version": reading.version, "problems": problems}
    return {"name": entry["name"], "path": entry["path"], "exists": exists, "packages": packages}


def find_unlisted(consumers: Consumers, ptypes: List[PinType]) -> List[Tuple[str, List[str]]]:
    """Repositories under scanRoot (two folder levels deep) that load a package but are not listed: (absolute path, packages)."""
    if not consumers.scan_root:
        return []
    if not os.path.isdir(consumers.scan_root):
        raise CheckError("scanRoot is not a folder")
    listed = {os.path.realpath(e["path"]) for e in consumers.entries}
    found = []

    def subdirs(path: str) -> List[str]:
        return [os.path.join(path, n) for n in sorted(os.listdir(path))
                if not n.startswith(".") and n != "node_modules" and os.path.isdir(os.path.join(path, n))]

    for first in subdirs(consumers.scan_root):
        for cand in [first] + subdirs(first):
            if os.path.exists(os.path.join(cand, ".git")) and os.path.realpath(cand) not in listed:
                loads = [p.name for p in ptypes if p.detect(cand)]
                if loads:
                    found.append((cand, loads))
    return found


def build(consumers: Consumers, ptypes: List[PinType], entries: Optional[List[dict]] = None,
          unlisted: Optional[List[Tuple[str, List[str]]]] = None) -> dict:
    rows = [consumer_report(e, ptypes) for e in (consumers.entries if entries is None else entries)]
    if unlisted is None:
        unlisted = find_unlisted(consumers, ptypes)
    return {"format": FORMAT, "consumers": rows, "unlisted": [{"path": p, "loads": loads} for p, loads in unlisted]}


def problem_note(text: str) -> str:
    """One reader problem as a printed note. `pins` and `roll-out` both print it, so the wording stays one."""
    return "PROBLEM " + text


def _text(report: dict) -> str:
    lines = []
    for c in report["consumers"]:
        for name, pk in c["packages"].items():
            if not (pk["declared"] or pk["detected"]):
                continue
            lines.append("%-24s %-14s %s" % (c["name"], name, pk["version"] or "no single version"))
            for f in pk["forms"]:
                req = f["requirement"]
                lines.append("    %s %s: %s%s" % (f["kind"], f["file"], ("%s %s" % (req["type"], req["version"] or "")).strip() if req else "lock only",
                                                  (", resolved " + f["resolved"]["version"]) if f["resolved"] and f["resolved"]["version"] else ""))
            for pr in pk["problems"]:
                lines.append("    " + problem_note(pr))
    for u in report["unlisted"]:
        lines.append("UNLISTED %s loads %s" % (u["path"], ", ".join(u["loads"])))
    return "\n".join(lines) + "\n"


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(prog="commit-checks pins", description="Read which release of each package every listed repository pins (read-only).")
    p.add_argument("--list", required=True, help="private JSON file naming the consumers")
    p.add_argument("--package", action="append", required=True, metavar="NAME", help="package to read (%s); repeat for several" % ", ".join(sorted(PIN_TYPES)))
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    ns = p.parse_args(argv)  # a usage error exits 2
    names: List[str] = []
    for n in ns.package:
        if n not in PIN_TYPES:
            p.error("--package needs one of: %s" % ", ".join(sorted(PIN_TYPES)))
        if n not in names:
            names.append(n)
    consumers = load_list(ns.list)
    report = build(consumers, [PIN_TYPES[n] for n in names])
    sys.stdout.write(json.dumps(report, indent=2) + "\n" if ns.json else _text(report))
    return 0
