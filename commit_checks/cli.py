"""Command line: python -m commit_checks MODE [args]."""
from __future__ import annotations

import os
import subprocess
import sys
from typing import List, Optional

from . import changes, checks, gitutil, pinreport, release, rollout, settings
from .errors import CheckError, Finding

USAGE = """usage: commit-checks MODE
  pre-commit            scan what is staged (the pre-commit hook)
  commit-msg FILE       scan a commit message (the commit-msg hook)
  pre-push              scan every commit about to be pushed (the pre-push hook; reads refs on stdin)
  verify                scan an exact copy of the staged snapshot, and check the repository set-up
  ci                    verify, for CI; scans COMMIT_CHECKS_RANGE too when set
  scan-archive FILE...  scan a release archive (tar, tgz or zip) before it is published
  history               scan the whole history (every commit, message, author and tag), as for a repository about to go public
  roll-out --list FILE --pin NAME=vX.Y.Z|latest ... [--only NAME ...] [--apply] [--push] [--log-dir DIR] [--approve NAME ...]
                        move every listed consumer to release tags of the shared packages
                        (commit-checks, house-style, judarn, markdown-engine, apple-dev-support,
                        apple-app-support, apple-test-support, ai-output-evals); dry run unless --apply
  pins --list FILE --package NAME ... [--json]
                        read which release of each package every listed consumer pins
                        (read-only: no tag, no network, no edits)
  docs-only             exit 0 when every changed file is docs (a script may skip its build and tests),
                        1 when code changed or it cannot tell, 2 when it could not run
"""
ZERO = "0" * 40


def report(findings: List[Finding]) -> None:
    for f in sorted(findings, key=lambda f: (f.check, f.path, f.line)):
        where = f.path + (":%d" % f.line if f.line else "")
        sys.stderr.write("FAIL %s: %s (%s)\n" % (f.check, where, f.detail))
    sys.stderr.write("%d finding(s). Matched text is never printed.\n" % len(findings))


def _log_messages(root: str, revspec: str) -> str:
    proc = gitutil.run(["log", "--format=%H%n%B%n----"] + revspec.split(), root)
    return proc.stdout.decode("utf-8", "surrogateescape")


def _ref_ranges(root: str, stdin_text: str) -> List[str]:
    ranges = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            raise CheckError("unexpected pre-push input")
        _lref, lsha, _rref, rsha = parts
        if lsha == ZERO:
            continue  # a deletion pushes no commits
        ranges.append(lsha + " --not --remotes" if rsha == ZERO else "%s..%s" % (rsha, lsha))
    return ranges


def _pushed_refs(stdin_text: str) -> List[tuple]:
    refs = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            raise CheckError("unexpected pre-push input")
        if parts[1] != ZERO:
            refs.append((parts[2], parts[1]))  # (remote ref, local sha): the remote ref names the tag the push creates
    return refs


def _run(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0 if argv else 2
    mode = argv[0]
    if mode == "roll-out":
        return rollout.main(argv[1:])
    if mode == "pins":
        return pinreport.main(argv[1:])
    if mode == "docs-only":
        root = gitutil.toplevel(os.getcwd())
        conf = settings.load(root)
        docs, why = changes.classify(root, conf.data)
        sys.stdout.write(why + "\n")
        return 0 if docs else 1
    if mode not in checks.MODES:
        sys.stderr.write(USAGE)
        return 2
    root = gitutil.toplevel(os.getcwd())
    conf = settings.load(root)
    findings: List[Finding] = []
    if mode == "scan-archive":
        if len(argv) < 2:
            raise CheckError("scan-archive needs at least one file")
        ctx = checks.Context(root, conf, "archive")
        ctx.archives = argv[1:]
        findings = checks.run_mode(mode, ctx)
    elif mode == "history":
        findings = checks.run_mode(mode, checks.Context(root, conf, "history"))
    elif mode == "pre-commit":
        findings = checks.run_mode(mode, checks.Context(root, conf, "staged"))
    elif mode == "commit-msg":
        if len(argv) != 2 or not os.path.isfile(argv[1]):
            raise CheckError("commit-msg needs the message file")
        with open(argv[1], encoding="utf-8", errors="surrogateescape") as fh:
            text = fh.read()
        findings = checks.run_mode(mode, checks.Context(root, conf, "message", message=text))
    elif mode == "pre-push":
        stdin_text = sys.stdin.read()
        findings += release.check_pushed_tags(checks.Context(root, conf, "range"), _pushed_refs(stdin_text))
        for revspec in _ref_ranges(root, stdin_text):
            ctx = checks.Context(root, conf, "range", revspec=revspec, message=_log_messages(root, revspec))
            findings += checks.run_mode(mode, ctx)
    elif mode in ("verify", "ci"):
        findings = checks.run_mode(mode, checks.Context(root, conf, "snapshot"))
        revspec = os.environ.get("COMMIT_CHECKS_RANGE", "") if mode == "ci" else ""
        if revspec:
            ctx = checks.Context(root, conf, "range", revspec=revspec, message=_log_messages(root, revspec))
            findings += checks.run_mode("pre-push", ctx)
    if findings:
        report(findings)
        return 1
    sys.stdout.write("commit-checks %s: ok\n" % mode)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return _run(sys.argv[1:] if argv is None else argv)
    except CheckError as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 2
    except (subprocess.SubprocessError, OSError) as exc:
        sys.stderr.write("ERROR: %s\n" % exc)
        return 2
