"""The scanning engine: gitleaks, run so that no failure looks like a pass.

Rules: the tool must exist and be the pinned version; it must finish before the timeout; it must
write a report we can parse; and the exit code and the report must agree. Anything else is an error.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import List, Optional

from . import gitutil, rules
from .errors import CheckError, Finding

GITLEAKS_VERSION = "8.30.1"
LEAKS_EXIT = 2
DEFAULT_TIMEOUT = 600
_version_ok = {}


def timeout_seconds() -> int:
    raw = os.environ.get("COMMIT_CHECKS_TIMEOUT", "")
    try:
        value = int(raw) if raw else DEFAULT_TIMEOUT
    except ValueError:
        raise CheckError("COMMIT_CHECKS_TIMEOUT must be a whole number of seconds")
    if value < 1:
        raise CheckError("COMMIT_CHECKS_TIMEOUT must be at least 1")
    return value


def find_gitleaks() -> str:
    exe = shutil.which("gitleaks")
    if not exe:
        raise CheckError("gitleaks is not installed (brew install gitleaks); version %s is required" % GITLEAKS_VERSION)
    if exe not in _version_ok:
        try:
            proc = subprocess.run([exe, "version"], capture_output=True, timeout=30, env=gitutil.clean_env())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CheckError("gitleaks would not report its version: %s" % exc)
        found = proc.stdout.decode("utf-8", "replace").strip().lstrip("v")
        if proc.returncode != 0 or found != GITLEAKS_VERSION:
            raise CheckError("gitleaks %s is required, found %r" % (GITLEAKS_VERSION, found[:40]))
        _version_ok[exe] = True
    return exe


def run_gitleaks(kind: str, target: str, cwd: str, private_names: Optional[List[str]], extra: Optional[List[str]] = None,
                 profile: str = "package") -> List[dict]:
    """Run one gitleaks scan. kind is 'dir' or 'git'. Returns raw report entries (possibly none)."""
    exe = find_gitleaks()
    if kind == "dir" and not os.path.isdir(target):
        raise CheckError("scan target is missing: " + target)  # gitleaks itself exits 0 here
    timeout = timeout_seconds()
    with tempfile.TemporaryDirectory(prefix="commit-checks-") as tmp:
        config = os.path.join(tmp, "config.toml")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write(rules.build_config(private_names, profile))
        empty = os.path.join(tmp, "no-ignore")
        os.mkdir(empty)
        report = os.path.join(tmp, "report.json")
        cmd = [exe, kind, "--config", config, "--report-format", "json", "--report-path", report,
               "--exit-code", str(LEAKS_EXIT), "--log-level", "error", "--no-banner", "--no-color",
               "--ignore-gitleaks-allow", "--gitleaks-ignore-path", empty] + list(extra or []) + [target]
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout, env=gitutil.clean_env())
        except subprocess.TimeoutExpired:
            raise CheckError("gitleaks did not finish within %d seconds" % timeout)
        except OSError as exc:
            raise CheckError("gitleaks could not start: %s" % exc)
        rc = proc.returncode
        if rc not in (0, LEAKS_EXIT):
            err = proc.stderr.decode("utf-8", "replace").strip()[-300:]
            raise CheckError("gitleaks failed (exit %d): %s" % (rc, err))
        if not os.path.isfile(report):
            raise CheckError("gitleaks wrote no report")
        try:
            with open(report, encoding="utf-8") as fh:
                entries = json.load(fh)
        except (OSError, ValueError) as exc:
            raise CheckError("gitleaks report is unreadable: %s" % exc)
        if not isinstance(entries, list):
            raise CheckError("gitleaks report has an unexpected shape")
        if rc == 0 and entries:
            raise CheckError("gitleaks reported findings but exited cleanly")
        if rc == LEAKS_EXIT and not entries:
            raise CheckError("gitleaks reported a leak exit code but no findings")
        return entries


def run_both(kind: str, target: str, cwd: str, private_names: Optional[List[str]], extra: Optional[List[str]] = None) -> List[dict]:
    """The default rules, then the package rules (see rules.build_config)."""
    entries = []  # type: List[dict]
    for profile in ("default", "package"):
        entries += run_gitleaks(kind, target, cwd, private_names, extra, profile)
    return entries


def to_findings(entries: List[dict], base: Optional[str]) -> List[Finding]:
    out = []
    for entry in entries:
        if not isinstance(entry, dict) or "RuleID" not in entry:
            raise CheckError("gitleaks report entry has an unexpected shape")
        path = str(entry.get("File", ""))
        if base and path.startswith(base.rstrip("/") + "/"):
            path = path[len(base.rstrip("/")) + 1:]
        rule = str(entry["RuleID"])
        text = str(entry.get("Secret") or entry.get("Match") or "")
        if rules.builtin_allowed(rule, text):
            continue
        out.append(Finding(rules.category(rule), path, int(entry.get("StartLine") or 0), rule, rule, text))
    return out


def scan_dir(directory: str, private_names: Optional[List[str]]) -> List[Finding]:
    return to_findings(run_both("dir", directory, directory, private_names), directory)


def scan_staged(root: str, private_names: Optional[List[str]]) -> List[Finding]:
    return to_findings(run_both("git", root, root, private_names, ["--staged", "--pre-commit"]), root)


def scan_range(root: str, revspec: str, private_names: Optional[List[str]]) -> List[Finding]:
    return to_findings(run_both("git", root, root, private_names, ["--log-opts", revspec]), root)


def scan_text(label: str, text: str, private_names: Optional[List[str]]) -> List[Finding]:
    """Scan loose text, such as a commit message, with the same rules."""
    with tempfile.TemporaryDirectory(prefix="commit-checks-text-") as tmp:
        with open(os.path.join(tmp, "text.txt"), "w", encoding="utf-8", errors="surrogateescape") as fh:
            fh.write(text)
        found = to_findings(run_both("dir", tmp, tmp, private_names), tmp)
    return [f._replace(path=label) for f in found]
