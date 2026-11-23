"""roll-out: move every consumer of the shared packages to release tags.

Dry run unless --apply. Exit 0: every consumer is current, updated or a local-path reference.
Exit 1: some consumer was skipped, held, blocked or failed its checks, or a repository that loads
a package is missing from the list. Exit 2: the command could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Set, Tuple

from . import gitutil
from .errors import CheckError
from .pins import PIN_TYPES, Pin, PinType, Target

OK = {"updated", "would update", "current", "local"}
COMMIT_TIMEOUT = 900  # the consumer's own hooks run during the commit
VERIFY_TIMEOUT = 4 * 3600  # a consumer's own verification script, which may wait for the machine-wide lock
BUMP_HINT = "bump the version"
VERIFY_SCRIPT = "scripts/verify_commit.sh"
COLLAB_LINE = "Collaborative: use branches and pull requests."
PR_BRANCH = "ci/shared-pins"


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


def _is_collaborative(root: str) -> bool:
    agents = os.path.join(root, "AGENTS.md")
    if not os.path.isfile(agents):
        return False
    with open(agents, encoding="utf-8", errors="replace") as fh:
        return COLLAB_LINE in fh.read()


def _sync_problem(root: str, branch: str) -> Optional[str]:
    """After a fetch: why the default branch must not be pushed from (ahead, behind or diverged), else None."""
    _git(root, "fetch", "-q", "origin")
    remote = "refs/remotes/origin/" + branch
    if _git(root, "show-ref", "--verify", "-q", remote, ok=(0, 1)).returncode != 0:
        return "origin has no %s branch" % branch
    counts = _git(root, "rev-list", "--left-right", "--count", "%s...%s" % (branch, remote)).stdout.decode().split()
    ahead, behind = int(counts[0]), int(counts[1])
    if ahead and behind:
        return "%s has diverged from origin" % branch
    if ahead:
        return "%s is ahead of origin: it has unpushed commits that are not ours" % branch
    if behind:
        return "%s is behind origin; pull first" % branch
    return None


Planned = Tuple[PinType, Target, Pin]


class Run:
    """Settings shared by every repository in one run."""

    def __init__(self, apply: bool, push: bool, log_dir: str):
        self.apply = apply
        self.push = push
        self.log_dir = log_dir


def process(entry: Dict[str, str], pins: List[Tuple[PinType, Target]], run: Run, held: Optional[str]) -> Result:
    name, root = entry["name"], entry["path"]
    if not os.path.isdir(root):
        return Result(name, "blocked", "folder not found")
    try:
        if os.path.realpath(gitutil.toplevel(root)) != os.path.realpath(root):
            return Result(name, "blocked", "not the root of a git repository")
        planned: List[Planned] = []
        for ptype, target in pins:
            if ptype.detect(root):
                planned.append((ptype, target, ptype.inspect(root, target)))
        if not planned:
            return Result(name, "skipped", "does not load %s" % " or ".join(p[0].name for p in pins))
        notes = [n for _t, _g, pin in planned for n in pin.notes]
        for _t, _g, pin in planned:
            if pin.status not in ("current", "local", "update"):
                raise CheckError("pin type returned an unknown status")
        updates = [x for x in planned if x[2].status == "update"]
        if not updates:
            if any(x[2].status == "local" for x in planned):
                return Result(name, "local", "local-path reference, not edited", notes)
            return Result(name, "current", ", ".join("%s %s" % (t.name, pin.current) for t, _g, pin in planned), notes)
        if _git(root, "status", "--porcelain").stdout.strip():
            return Result(name, "skipped", "has uncommitted changes", notes)
        branch, default = current_branch(root), default_branch(root)
        if default is None:
            return Result(name, "skipped", "cannot tell the default branch", notes)
        if branch != default:
            return Result(name, "skipped", "on %s, not the default branch %s" % (branch or "a detached HEAD", default), notes)
        collab = _is_collaborative(root)
        if collab and _git(root, "show-ref", "--verify", "-q", "refs/heads/" + PR_BRANCH, ok=(0, 1)).returncode == 0:
            return Result(name, "skipped", "branch %s already exists" % PR_BRANCH, notes)
        if held is not None:
            return Result(name, "held", held, notes)
        if run.push:
            problem = _sync_problem(root, default)
            if problem:
                return Result(name, "skipped", problem, notes)
        paths: List[str] = []
        for _t, _g, pin in updates:
            for rel in sorted(pin.files):
                if rel in paths:
                    raise CheckError("two pins edit %s" % rel)
                paths.append(rel)
        for rel in paths:
            if _git(root, "ls-files", "--error-unmatch", "--", rel, ok=(0, 1)).returncode != 0:
                raise CheckError("%s is not tracked" % rel)
        verb = "; ".join("%s %s -> %s" % (t.name, pin.current, g.tag) for t, g, pin in updates)
        if not run.apply:
            where = ", opens a pull request" if collab else ""
            return Result(name, "would update", "%s (%s%s)" % (verb, ", ".join(paths), where), notes)
        return _apply(name, root, updates, run, default, collab, verb, notes)
    except (CheckError, OSError, subprocess.SubprocessError) as exc:
        return Result(name, "blocked", str(exc))


def _rollback(root: str, start: str, default: str, collab: bool) -> None:
    """Undo everything the run did here. The repository was clean when the run began."""
    _git(root, "reset", "-q", "--hard", start)
    if collab and current_branch(root) != default:
        _git(root, "checkout", "-q", default)
    if collab:
        _git(root, "branch", "-q", "-D", PR_BRANCH, ok=(0, 1))


def _verify(name: str, root: str, run: Run) -> Tuple[int, str, str]:
    """Run the consumer's verify_commit.sh once. Returns (exit code, output, log path)."""
    os.makedirs(run.log_dir, exist_ok=True)
    log = os.path.join(run.log_dir, re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".log")
    with open(log, "wb") as fh:
        code = subprocess.run(["./" + VERIFY_SCRIPT], cwd=root, stdout=fh, stderr=subprocess.STDOUT, timeout=VERIFY_TIMEOUT).returncode
    with open(log, encoding="utf-8", errors="replace") as fh:
        return code, fh.read(), log


def _apply(name: str, root: str, updates: List[Planned], run: Run, default: str, collab: bool, verb: str,
           notes: List[str]) -> Result:
    start = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    paths = sorted(rel for _t, _g, pin in updates for rel in pin.files)
    failure: Optional[Result] = None
    try:
        if collab:
            _git(root, "checkout", "-q", "-b", PR_BRANCH)
        for _t, _g, pin in updates:
            pin.write(root)
        _git(root, "add", "--", *paths)  # the consumer's verification scans what is staged
        if os.path.isfile(os.path.join(root, VERIFY_SCRIPT)):
            code, output, log = _verify(name, root, run)
            if code != 0:
                status = "needs a version bump" if BUMP_HINT in output else "checks fail"
                failure = Result(name, status, "%s failed (exit %d); files restored; log: %s" % (VERIFY_SCRIPT, code, log),
                                 [_tail(output)] + notes)
        else:
            notes = notes + ["no %s; only the commit hooks checked this" % VERIFY_SCRIPT]
        if failure is None:
            _git(root, "reset", "-q", "HEAD", "--", *paths)
            for ptype, target, pin in updates:
                subject = ptype.subject(target.tag)
                _git(root, "add", "--", *sorted(pin.files))
                proc = subprocess.run(["git", "commit", "-q", "-m", subject], cwd=root, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, timeout=COMMIT_TIMEOUT, env=gitutil.clean_env())
                output = proc.stdout.decode("utf-8", "replace")
                if proc.returncode != 0:
                    status = "needs a version bump" if BUMP_HINT in output else "blocked"
                    detail = "the commit hook refused the pin" if status != "blocked" else "the commit failed"
                    failure = Result(name, status, detail + "; files restored", [_tail(output)] + notes)
                    break
                if not pin.committed(root, "HEAD"):
                    failure = Result(name, "blocked", "the new commit does not contain the new pin; files restored", notes)
                    break
    except BaseException:
        _rollback(root, start, default, collab)
        raise
    if failure is not None:
        _rollback(root, start, default, collab)
        if _git(root, "status", "--porcelain").stdout.strip():
            failure.detail += "; WARNING: the repository is still not clean"
        return failure
    if collab:
        _git(root, "checkout", "-q", default)
    if _git(root, "status", "--porcelain").stdout.strip():
        notes = notes + ["the repository is not clean after the run; check it by hand"]
    if not run.push:
        return Result(name, "updated", verb + (", committed on %s, not pushed" % PR_BRANCH if collab else ", not pushed"), notes)
    if collab:
        return _open_pull_request(name, root, updates, default, verb, notes)
    sent = subprocess.run(["git", "push", "-q", "origin", "HEAD:refs/heads/" + default], cwd=root,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, env=gitutil.clean_env())
    if sent.returncode != 0:
        return Result(name, "blocked", "committed, but the push failed", [_tail(sent.stdout.decode("utf-8", "replace"))] + notes)
    return Result(name, "updated", verb + ", pushed", notes)


def _open_pull_request(name: str, root: str, updates: List[Planned], default: str, verb: str, notes: List[str]) -> Result:
    sent = subprocess.run(["git", "push", "-q", "origin", "%s:refs/heads/%s" % (PR_BRANCH, PR_BRANCH)], cwd=root,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300, env=gitutil.clean_env())
    if sent.returncode != 0:
        return Result(name, "blocked", "committed on %s, but the push failed" % PR_BRANCH,
                      [_tail(sent.stdout.decode("utf-8", "replace"))] + notes)
    subjects = [t.subject(g.tag) for t, g, _p in updates]
    title = subjects[0] if len(subjects) == 1 else "ci: update the shared pins"
    body = "Moves the shared pins, one commit each:\n\n" + "\n".join("- " + s for s in subjects) + \
           "\n\n`./scripts/verify_commit.sh` passed on the new pins before the commits were made.\n"
    try:
        made = subprocess.run(["gh", "pr", "create", "--base", default, "--head", PR_BRANCH, "--title", title, "--body", body],
                              cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        return Result(name, "blocked", "branch pushed, but gh failed to run: %s" % exc, notes)
    text = made.stdout.decode("utf-8", "replace")
    if made.returncode != 0:
        return Result(name, "blocked", "branch pushed, but the pull request failed", [_tail(text)] + notes)
    return Result(name, "updated", "%s, pull request opened: %s" % (verb, _tail(text, 1)), notes)


def find_unlisted(consumers: Consumers, ptypes: List[PinType]) -> List[Tuple[str, List[str]]]:
    """Repositories under scanRoot (two folder levels deep) that load a package but are not listed."""
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
            if os.path.exists(os.path.join(cand, ".git")) and os.path.realpath(cand) not in listed:
                loads = [p.name for p in ptypes if p.detect(cand)]
                if loads:
                    found.append((os.path.relpath(cand, consumers.scan_root), loads))
    return found


def parse(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="commit-checks roll-out", description="Move every consumer of the shared packages to release tags (dry run unless --apply).")
    p.add_argument("--list", required=True, help="private JSON file naming the consumers")
    p.add_argument("--pin", action="append", required=True, metavar="NAME=vX.Y.Z",
                   help="package and release tag to move to; repeat for several (%s)" % ", ".join(sorted(PIN_TYPES)))
    p.add_argument("--apply", action="store_true", help="edit, verify and commit (default: report only)")
    p.add_argument("--push", action="store_true", help="push each new commit to the default branch, or open a pull request for a collaborative project (needs --apply)")
    p.add_argument("--log-dir", metavar="DIR", help="where each consumer's verification output goes (default: a new temporary folder)")
    p.add_argument("--approve", action="append", nargs="+", default=[], metavar="NAME", help="move held consumers after a look")
    ns = p.parse_args(argv)  # a usage error exits 2
    ns.approve = [n for group in ns.approve for n in group]
    pins: Dict[str, str] = {}
    for item in ns.pin:
        name, sep, tag = item.partition("=")
        if not sep or name not in PIN_TYPES or not tag:
            p.error("--pin needs NAME=vX.Y.Z with NAME one of: %s" % ", ".join(sorted(PIN_TYPES)))
        if name in pins:
            p.error("--pin names %s twice" % name)
        pins[name] = tag
    ns.pins = pins
    return ns


def main(argv: List[str]) -> int:
    ns = parse(argv)
    if ns.push and not ns.apply:
        raise CheckError("--push needs --apply")
    consumers = load_list(ns.list)
    if consumers.gate and len(ns.pins) != 1:
        raise CheckError("a list with a gate takes exactly one --pin")
    names = {e["name"] for e in consumers.entries}
    for n in ns.approve:
        if n not in names:
            raise CheckError("--approve names a consumer that is not listed: " + n)
    # Every release is confirmed on its remote before anything is touched.
    pins = [(PIN_TYPES[n], PIN_TYPES[n].resolve_target(tag)) for n, tag in sorted(ns.pins.items())]
    decisions: Dict[str, Dict[str, str]] = {}
    gate_error = ""
    if consumers.gate:
        try:
            decisions = run_gate(consumers, pins[0][1].tag)
        except CheckError as exc:
            gate_error = str(exc)
    unlisted = find_unlisted(consumers, [p[0] for p in pins])
    log_dir = ""
    if ns.apply:
        log_dir = os.path.abspath(ns.log_dir) if ns.log_dir else tempfile.mkdtemp(prefix="commit-checks-roll-out-")
    run = Run(ns.apply, ns.push, log_dir)

    out = sys.stdout
    out.write("roll-out %s (%s)\n" % (", ".join("%s %s" % (t.package, t.tag) for _p, t in pins),
                                      "apply" if ns.apply else "dry run, nothing edited"))
    if log_dir:
        out.write("verification logs: %s\n" % log_dir)
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
        results.append(process(entry, pins, run, held))

    for r in results:
        out.write("%-14s %s%s\n" % (r.status.upper(), r.name, (": " + r.detail) if r.detail else ""))
        for note in r.notes:
            for line in note.splitlines():
                out.write("    " + line + "\n")
    for rel, loads in unlisted:
        out.write("UNLISTED       %s loads %s but is not in the list\n" % (rel, ", ".join(loads)))
    bad = [r for r in results if r.status not in OK]
    out.write("%d consumer(s); %d need attention.\n" % (len(results), len(bad) + len(unlisted)))
    return 1 if bad or unlisted else 0
