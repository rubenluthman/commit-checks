"""roll-out: move every consumer of the shared packages to release tags.

Dry run unless --apply. Exit 0: every consumer is current, updated or a local-path reference.
Exit 1: some consumer was skipped, held, blocked or failed its checks, or a repository that loads
a package is missing from the list. Exit 2: the command could not run.

Only consumers whose `packages` name a requested package are worked on. The list is checked both
ways: a listed package with no pin found, and a pin the list does not name, both block.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

from . import gitutil
from .errors import CheckError
from . import pinreport
from .consumerlist import Consumers, load_list, not_found_text, not_listed_text
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


def run_gate(consumers: Consumers, package: str, tag: str, report: dict) -> Dict[str, Dict[str, str]]:
    """Run the package's gate once. `{pins}` becomes a temporary file holding the pin report for exactly the
    consumers roll-out is about to act on. Any failure raises CheckError; the caller then holds every consumer."""
    handle, pins_file = tempfile.mkstemp(prefix="commit-checks-pins-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        argv = [a.replace("{tag}", tag).replace("{pins}", pins_file) for a in consumers.gates[package]]
        try:
            proc = subprocess.run(argv, cwd=consumers.base, capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as exc:
            raise CheckError("gate failed to run: %s" % exc)
    finally:
        try:
            os.unlink(pins_file)
        except OSError:
            pass
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


def undeclared(root: str, others: List[PinType]) -> List[str]:
    """Packages the repository pins that its list entry does not name."""
    return [not_listed_text(p.name) for p in others if p.detect(root)]


def process(entry: Dict[str, str], pins: List[Tuple[PinType, Target]], run: Run, held: Optional[str],
            others: Optional[List[PinType]] = None) -> Result:
    name, root = entry["name"], entry["path"]
    if not os.path.isdir(root):
        return Result(name, "blocked", "folder not found")
    try:
        if os.path.realpath(gitutil.toplevel(root)) != os.path.realpath(root):
            return Result(name, "blocked", "not the root of a git repository")
        problems = [not_found_text(p.name) for p, _t in pins if not p.detect(root)] + undeclared(root, others or [])
        if problems:
            return Result(name, "blocked", "; ".join(problems))
        planned: List[Planned] = [(ptype, target, ptype.inspect(root, target)) for ptype, target in pins]
        notes = [n for _t, _g, pin in planned for n in pin.notes]
        for _t, _g, pin in planned:
            if pin.status not in ("blocked", "current", "local", "manual", "update"):
                raise CheckError("pin type returned an unknown status")
        updates = [x for x in planned if x[2].status in ("update", "blocked")]
        if not updates:
            if any(x[2].status == "manual" for x in planned):
                return Result(name, "manual", "not an exact pin, not edited", notes)
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
        stuck = [pin.detail for _t, _g, pin in updates if pin.status == "blocked"]
        if stuck:
            return Result(name, "blocked", "; ".join(stuck), notes)
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


def _is_own_clone(entry: Dict[str, str], target: Target) -> bool:
    """A package's own clone is never a consumer of itself."""
    return os.path.realpath(entry["path"]) == os.path.realpath(target.repo)


def main(argv: List[str]) -> int:
    ns = parse(argv)
    if ns.push and not ns.apply:
        raise CheckError("--push needs --apply")
    consumers = load_list(ns.list)
    gated = [n for n in ns.pins if n in consumers.gates]
    if gated and len(ns.pins) != 1:
        raise CheckError("a run that includes a gated package takes exactly one --pin")
    names = {e["name"] for e in consumers.entries}
    for n in ns.approve:
        if n not in names:
            raise CheckError("--approve names a consumer that is not listed: " + n)
    # Every release is confirmed on its remote before anything is touched.
    pins = [(PIN_TYPES[n], PIN_TYPES[n].resolve_target(tag)) for n, tag in sorted(ns.pins.items())]
    unlisted = pinreport.find_unlisted(consumers, [p[0] for p in pins])

    plan = []  # (entry, pins to work on, packages the entry does not name)
    for entry in consumers.entries:
        mine = [(p, t) for p, t in pins if not _is_own_clone(entry, t)]
        plan.append((entry, [(p, t) for p, t in mine if p.name in entry["packages"]],
                     [p for p, _t in mine if p.name not in entry["packages"]]))
    acting = [entry for entry, active, _o in plan if active]

    decisions: Dict[str, Dict[str, str]] = {}
    gate_error = ""
    if gated:
        ptype, target = pins[0]
        try:
            report = pinreport.build(consumers, [ptype], acting, unlisted)
            decisions = run_gate(consumers, ptype.name, target.tag, report)
        except CheckError as exc:
            gate_error = str(exc)
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
    idle = 0
    for entry, active, others in plan:
        if not active:
            problems = undeclared(entry["path"], others) if os.path.isdir(entry["path"]) else []
            if problems:
                results.append(Result(entry["name"], "blocked", "; ".join(problems)))
            else:
                idle += 1
            continue
        held = None
        if gated and entry["name"] not in ns.approve:
            if gate_error:
                held = gate_error
            else:
                d = decisions.get(entry["name"])
                if not isinstance(d, dict) or d.get("decision") not in ("go", "hold"):
                    held = "the gate did not give a go or hold for it"
                elif d["decision"] == "hold":
                    held = "gate: " + str(d.get("reason", "no reason given"))
        results.append(process(entry, active, run, held, others))

    for r in results:
        out.write("%-14s %s%s\n" % (r.status.upper(), r.name, (": " + r.detail) if r.detail else ""))
        for note in r.notes:
            for line in note.splitlines():
                out.write("    " + line + "\n")
    for path, loads in unlisted:
        out.write("UNLISTED       %s loads %s but is not in the list\n" % (os.path.relpath(path, consumers.scan_root), ", ".join(loads)))
    bad = [r for r in results if r.status not in OK]
    if idle:
        out.write("%d listed %s not name %s; not touched.\n" % (idle, "repository does" if idle == 1 else "repositories do", " or ".join(sorted(ns.pins))))
    out.write("%d consumer(s); %d need attention.\n" % (len(results), len(bad) + len(unlisted)))
    return 1 if bad or unlisted else 0
