"""roll-out: move every consumer of a shared package to a release tag.

Dry run unless --apply. Exit 0: every consumer is current, updated or a local-path reference.
Exit 1: some consumer was skipped, held or blocked. Exit 2: the command could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional, Set

from . import gitutil
from .errors import CheckError
from .pins import PIN_TYPES, Pin, PinType, Target

OK = {"updated", "would update", "current", "local"}
COMMIT_TIMEOUT = 900  # the consumer's own hooks run during the commit
BUMP_HINT = "bump the version"


class Result:
    def __init__(self, name: str, status: str, detail: str = "", notes: Optional[List[str]] = None):
        self.name = name
        self.status = status
        self.detail = detail
        self.notes = notes or []


class Consumers:
    def __init__(self, base: str, entries: List[Dict[str, str]], scan_root: Optional[str], gate: Optional[List[str]]):
        self.base = base
        self.entries = entries
        self.scan_root = scan_root
        self.gate = gate


def load_list(path: str) -> Consumers:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CheckError("cannot read the consumer list: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("consumers"), list):
        raise CheckError("the consumer list needs a consumers array")
    base = os.path.dirname(os.path.abspath(path))

    def resolve(p: str) -> str:
        return os.path.normpath(os.path.join(base, os.path.expanduser(p)))

    entries, seen = [], set()
    for item in data["consumers"]:
        if not (isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("path"), str)
                and item["name"] and item["path"]):
            raise CheckError("every consumer needs a name and a path")
        if item["name"] in seen:
            raise CheckError("consumer listed twice: " + item["name"])
        seen.add(item["name"])
        entries.append({"name": item["name"], "path": resolve(item["path"])})
    scan = data.get("scanRoot")
    if scan is not None and not (isinstance(scan, str) and scan):
        raise CheckError("scanRoot must be a folder name")
    gate = data.get("gate")
    if gate is not None and not (isinstance(gate, list) and gate and all(isinstance(a, str) for a in gate)):
        raise CheckError("gate must be a list of command arguments")
    return Consumers(base, entries, resolve(scan) if scan else None, gate)


def run_gate(consumers: Consumers, tag: str) -> Dict[str, Dict[str, str]]:
    """Run the gate once. Any failure raises CheckError; the caller then holds every consumer."""
    argv = [a.replace("{tag}", tag) for a in consumers.gate or []]
    try:
        proc = subprocess.run(argv, cwd=consumers.base, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CheckError("gate failed to run: %s" % exc)
    if proc.returncode != 0:
        raise CheckError("gate exited with %d" % proc.returncode)
    try:
        data = json.loads(proc.stdout.decode("utf-8"))
    except ValueError:
        raise CheckError("gate printed output that is not JSON")
    if not isinstance(data, dict):
        raise CheckError("gate output is not an object")
    return data


def _git(root: str, *args: str, ok=(0,)):
    return gitutil.run(list(args), root, ok=ok)


def default_branch(root: str) -> Optional[str]:
    proc = _git(root, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD", ok=(0, 1))
    if proc.returncode == 0:
        name = proc.stdout.decode().strip()
        return name.split("/", 1)[1] if "/" in name else None
    for branch in ("main", "master"):
        if _git(root, "show-ref", "--verify", "-q", "refs/heads/" + branch, ok=(0, 1)).returncode == 0:
            return branch
    return None


def current_branch(root: str) -> str:
    return _git(root, "symbolic-ref", "-q", "--short", "HEAD", ok=(0, 1)).stdout.decode().strip()


def _tail(text: str, lines: int = 15) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def _restore(root: str, paths: List[str]) -> None:
    _git(root, "reset", "-q", "HEAD", "--", *paths, ok=(0, 1))
    _git(root, "checkout", "HEAD", "--", *paths)


def process(entry: Dict[str, str], ptype: PinType, target: Target, apply: bool, push: bool,
            held: Optional[str]) -> Result:
    name, root = entry["name"], entry["path"]
    if not os.path.isdir(root):
        return Result(name, "blocked", "folder not found")
    try:
        if os.path.realpath(gitutil.toplevel(root)) != os.path.realpath(root):
            return Result(name, "blocked", "not the root of a git repository")
        if not ptype.detect(root):
            return Result(name, "skipped", "does not load %s" % ptype.name)
        pin = ptype.inspect(root, target)
        if pin.status == "current":
            return Result(name, "current", pin.current, pin.notes)
        if pin.status == "local":
            return Result(name, "local", "local-path reference, not edited", pin.notes)
        if pin.status != "update":
            raise CheckError("pin type returned an unknown status")
        if _git(root, "status", "--porcelain").stdout.strip():
            return Result(name, "skipped", "has uncommitted changes", pin.notes)
        branch, default = current_branch(root), default_branch(root)
        if default is None:
            return Result(name, "skipped", "cannot tell the default branch", pin.notes)
        if branch != default:
            return Result(name, "skipped", "on %s, not the default branch %s" % (branch or "a detached HEAD", default), pin.notes)
        agents = os.path.join(root, "AGENTS.md")
        if os.path.isfile(agents):
            with open(agents, encoding="utf-8", errors="replace") as fh:
                if "Collaborative:" in fh.read():
                    return Result(name, "skipped", "collaborative project", pin.notes)
        if held is not None:
            return Result(name, "held", held, pin.notes)
        paths = sorted(pin.files)
        for rel in paths:
            if _git(root, "ls-files", "--error-unmatch", "--", rel, ok=(0, 1)).returncode != 0:
                raise CheckError("%s is not tracked" % rel)
        verb = "%s -> %s" % (pin.current, target.tag)
        if not apply:
            return Result(name, "would update", "%s (%s)" % (verb, ", ".join(paths)), pin.notes)
        return _commit(name, root, pin, paths, ptype.name, target, branch, push, verb)
    except (CheckError, OSError, subprocess.SubprocessError) as exc:
        return Result(name, "blocked", str(exc))


def _commit(name: str, root: str, pin: Pin, paths: List[str], package: str, target: Target, branch: str,
            push: bool, verb: str) -> Result:
    subject = "build(deps): update %s to %s" % (package, target.tag)
    try:
        pin.write(root)
        _git(root, "add", "--", *paths)
        proc = subprocess.run(["git", "commit", "-q", "-m", subject], cwd=root, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=COMMIT_TIMEOUT, env=gitutil.clean_env())
    except BaseException:
        _restore(root, paths)
        raise
    output = proc.stdout.decode("utf-8", "replace")
    if proc.returncode != 0:
        _restore(root, paths)
        leftover = _git(root, "status", "--porcelain").stdout.strip()
        note = "; files restored" if not leftover else "; WARNING: the repository is still not clean"
        if BUMP_HINT in output:
            return Result(name, "needs a version bump", "the commit hook refused the pin" + note, [_tail(output)] + pin.notes)
        return Result(name, "blocked", "the commit failed" + note, [_tail(output)] + pin.notes)
    if not pin.committed(root, "HEAD"):
        return Result(name, "blocked", "the new commit does not contain the new pin; check it by hand", pin.notes)
    if push:
        sent = subprocess.run(["git", "push", "-q", "origin", "HEAD:refs/heads/" + branch], cwd=root,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, env=gitutil.clean_env())
        if sent.returncode != 0:
            return Result(name, "blocked", "committed, but the push failed", [_tail(sent.stdout.decode("utf-8", "replace"))] + pin.notes)
        return Result(name, "updated", verb + ", pushed", pin.notes)
    return Result(name, "updated", verb + ", not pushed", pin.notes)


def find_unlisted(consumers: Consumers, ptype: PinType) -> List[str]:
    """Repositories under scanRoot (two folder levels deep) that load the package but are not listed."""
    if not consumers.scan_root:
        return []
    if not os.path.isdir(consumers.scan_root):
        raise CheckError("scanRoot is not a folder")
    listed: Set[str] = {os.path.realpath(e["path"]) for e in consumers.entries}
    found = []

    def subdirs(path: str) -> List[str]:
        return [os.path.join(path, n) for n in sorted(os.listdir(path))
                if not n.startswith(".") and n != "node_modules" and os.path.isdir(os.path.join(path, n))]

    for first in subdirs(consumers.scan_root):
        for cand in [first] + subdirs(first):
            if os.path.exists(os.path.join(cand, ".git")) and ptype.detect(cand) and os.path.realpath(cand) not in listed:
                found.append(os.path.relpath(cand, consumers.scan_root))
    return found


def parse(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="commit-checks roll-out", description="Move every consumer of a package to a release tag (dry run unless --apply).")
    p.add_argument("--list", required=True, help="private JSON file naming the consumers")
    p.add_argument("--package", required=True, choices=sorted(PIN_TYPES))
    p.add_argument("--to", required=True, metavar="vX.Y.Z", help="release tag to move to")
    p.add_argument("--apply", action="store_true", help="edit and commit (default: report only)")
    p.add_argument("--push", action="store_true", help="push each new commit to the default branch (needs --apply)")
    p.add_argument("--approve", action="append", nargs="+", default=[], metavar="NAME", help="move held consumers after a look")
    ns = p.parse_args(argv)  # a usage error exits 2
    ns.approve = [n for group in ns.approve for n in group]
    return ns


def main(argv: List[str]) -> int:
    ns = parse(argv)
    if ns.push and not ns.apply:
        raise CheckError("--push needs --apply")
    consumers = load_list(ns.list)
    names = {e["name"] for e in consumers.entries}
    for n in ns.approve:
        if n not in names:
            raise CheckError("--approve names a consumer that is not listed: " + n)
    ptype = PIN_TYPES[ns.package]
    target = ptype.resolve_target(ns.to)  # confirms the release on the remote before anything is touched
    decisions: Dict[str, Dict[str, str]] = {}
    gate_error = ""
    if consumers.gate:
        try:
            decisions = run_gate(consumers, ns.to)
        except CheckError as exc:
            gate_error = str(exc)
    unlisted = find_unlisted(consumers, ptype)

    results = []
    for entry in consumers.entries:
        held = None
        if consumers.gate and entry["name"] not in ns.approve:
            if gate_error:
                held = gate_error
            else:
                d = decisions.get(entry["name"])
                if not isinstance(d, dict) or d.get("decision") not in ("go", "hold"):
                    held = "the gate did not give a go or hold for it"
                elif d["decision"] == "hold":
                    held = "gate: " + str(d.get("reason", "no reason given"))
        results.append(process(entry, ptype, target, ns.apply, ns.push, held))

    out = sys.stdout
    out.write("roll-out %s to %s (%s)\n" % (ns.package, ns.to, "apply" if ns.apply else "dry run, nothing edited"))
    for r in results:
        out.write("%-14s %s%s\n" % (r.status.upper(), r.name, (": " + r.detail) if r.detail else ""))
        for note in r.notes:
            for line in note.splitlines():
                out.write("    " + line + "\n")
    for rel in unlisted:
        out.write("UNLISTED       %s loads %s but is not in the list\n" % (rel, ns.package))
    bad = [r for r in results if r.status not in OK]
    out.write("%d consumer(s); %d need attention.\n" % (len(results), len(bad)))
    return 1 if bad else 0
