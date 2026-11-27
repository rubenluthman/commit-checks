"""roll-out: move every consumer of the shared packages to release tags.

Dry run unless --apply. Exit 0: every consumer is current, updated, waiting for its next version or a local-path reference.
Exit 1: some consumer was skipped, held, blocked, interrupted or failed its checks, or a repository that loads
a package is missing from the list. Exit 2: the command could not run, or the run was stopped by a signal.

Only consumers whose `packages` name a requested package are worked on. The list is checked both
ways: a listed package with no pin found, and a pin the list does not name, both block.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from typing import Dict, List, Optional, Tuple

from . import gitutil
from .errors import CheckError
from . import pinreport
from .consumerlist import Consumers, load_list, not_found_text, not_listed_text
from .pins import LOADER_FILE, PIN_TYPES, Pin, PinType, Target

OK = {"updated", "would update", "current", "local", "range", "waits"}
COMMIT_TIMEOUT = 900  # the consumer's own hooks run during the commit
VERIFY_TIMEOUT = 4 * 3600  # a consumer's own verification script, which may wait for the machine-wide lock
VERIFY_TIMEOUT_ENV = "COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT"  # seconds; a test seam
WATCHER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "verifywatch.py")
STOP_GRACE = 10  # seconds between TERM and KILL for a verification's process group
BUMP_HINT = "bump the version"
VERIFY_SCRIPT = "scripts/verify_commit.sh"
CHEAP_PINS = ("commit-checks", "house-style")  # check tools: `commit-checks verify` runs every check a move of them can affect
QUICK_TIMEOUT = 600  # seconds for `commit-checks verify` and for each command of a `checks` entry
COLLAB_LINE = "Collaborative: use branches and pull requests."
PR_BRANCH = "ci/shared-pins"
RECORD = "commit-checks-roll-out.json"  # in the repository's git folder while roll-out edits there
RECORD_FORMAT = 1
STOP_SIGNALS = tuple(getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, n))


class Interrupted(KeyboardInterrupt):
    """SIGTERM or SIGHUP arrived. Like Ctrl-C, it unwinds the run so the repository in hand is put back."""

    def __init__(self, signal_name: str):
        super().__init__(signal_name)
        self.signal_name = signal_name


def _signal_name(exc: BaseException) -> str:
    return getattr(exc, "signal_name", "SIGINT")


def _install_handlers() -> dict:
    def handler(signum, _frame):
        raise Interrupted(signal.Signals(signum).name)

    old = {}
    for sig in STOP_SIGNALS:
        if sig != signal.SIGINT:  # Python already turns SIGINT into KeyboardInterrupt
            old[sig] = signal.signal(sig, handler)
    return old


@contextlib.contextmanager
def _signals_ignored():
    """A second Ctrl-C must not cut a restore, or the stop of a process group, short."""
    old = {}
    try:
        for sig in STOP_SIGNALS:
            old[sig] = signal.signal(sig, signal.SIG_IGN)
        yield
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


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
        self.verify_timeout = _verify_timeout()
        self.out = sys.stdout
        self.started: Dict[str, float] = {}  # repository name -> monotonic start, for repositories roll-out edited

    def log_path(self, name: str) -> str:
        return os.path.join(self.log_dir, _log_name(name))

    def begin(self, name: str, has_script: bool) -> None:
        """Print the START line: roll-out is about to edit this repository, the slow part."""
        if not self.apply:
            return
        self.started[name] = time.monotonic()
        text = "at " + time.strftime("%H:%M")
        if has_script:
            text += "; log: " + _log_name(name)
        _emit(self.out, "%-14s %s: %s\n" % ("START", name, text))


def _emit(out, text: str) -> None:
    out.write(text)
    out.flush()  # a pipe buffers in blocks, and a run that takes hours must be followable


def _log_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".log"


def _minutes_text(seconds: float) -> str:
    whole = int(seconds / 60.0 + 0.5)
    return "(%d min)" % whole if whole else "(<1 min)"


def _verify_timeout() -> float:
    raw = os.environ.get(VERIFY_TIMEOUT_ENV)
    if raw is None:
        return float(VERIFY_TIMEOUT)
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0:
        raise CheckError("%s must be a number of seconds above zero" % VERIFY_TIMEOUT_ENV)
    return value


def _limit_text(seconds: float) -> str:
    whole = int(seconds)
    if whole == seconds and whole % 3600 == 0:
        return "%d h" % (whole // 3600)
    if whole == seconds and whole % 60 == 0:
        return "%d min" % (whole // 60)
    return "%g s" % seconds


def undeclared(root: str, others: List[PinType]) -> List[str]:
    """Packages the repository pins that its list entry does not name."""
    return [not_listed_text(p.name) for p in others if p.detect(root)]


def _with_held(result: Result, held_pins: List[Planned], held: Dict[str, str]) -> Result:
    """A consumer whose other pins moved still needs attention for the ones that were held."""
    if held_pins and result.status in ("updated", "would update"):
        why = ", ".join("%s (%s)" % (t.name, held[t.name]) for t, _g, _p in held_pins)
        return Result(result.name, "held", "%s; held: %s" % (result.detail, why), result.notes)
    return result


def process(entry: dict, pins: List[Tuple[PinType, Target]], run: Run, held: Optional[Dict[str, str]],
            others: Optional[List[PinType]] = None) -> Result:
    """`held` maps a package name to the reason this consumer's move of it is held; its other pins still move."""
    held = held or {}
    name, root = entry["name"], entry["path"]
    if not os.path.isdir(root):
        return Result(name, "blocked", "folder not found")
    try:
        if os.path.realpath(gitutil.toplevel(root)) != os.path.realpath(root):
            return Result(name, "blocked", "not the root of a git repository")
        problems = [not_found_text(p.name) for p, _t in pins if not p.detect(root, entry)] + undeclared(root, others or [])
        if problems:
            return Result(name, "blocked", "; ".join(problems))
        planned: List[Planned] = [(ptype, target, ptype.inspect(root, target, entry)) for ptype, target in pins]
        notes = [n for _t, _g, pin in planned for n in pin.notes]
        for ptype, _t in pins:  # the reader's problems, under every status; a note that already says one is not repeated
            for text in ptype.read(root, entry).problems:
                if not any(n.startswith(text) for n in notes):
                    notes.append(pinreport.problem_note(text))
        for _t, _g, pin in planned:
            if pin.status not in ("blocked", "current", "local", "manual", "range", "update"):
                raise CheckError("pin type returned an unknown status")
        updates = [x for x in planned if x[2].status in ("update", "blocked")]
        if not updates:
            if any(x[2].status == "manual" for x in planned):
                return Result(name, "manual", "not an exact pin, not edited", notes)
            if any(x[2].status == "local" for x in planned):
                return Result(name, "local", "local-path reference, not edited", notes)
            if any(x[2].status == "range" for x in planned):
                return Result(name, "range", "version range in a library, nothing to move", notes)
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
        held_pins = [x for x in updates if x[0].name in held]
        updates = [x for x in updates if x[0].name not in held]
        if not updates:
            return Result(name, "held", "; ".join(sorted({held[t.name] for t, _g, _p in held_pins})), notes)
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
            return _with_held(Result(name, "would update", "%s (%s%s)" % (verb, ", ".join(paths), where), notes), held_pins, held)
        return _with_held(_apply(entry, root, updates, run, default, collab, verb, notes), held_pins, held)
    except (CheckError, OSError, subprocess.SubprocessError) as exc:
        return Result(name, "blocked", str(exc))


def _rollback(root: str, start: str, default: str, collab: bool) -> None:
    """Undo everything the run did here. The repository was clean when the run began."""
    with _signals_ignored():
        _git(root, "reset", "-q", "--hard", start)
        if collab and current_branch(root) != default:
            _git(root, "checkout", "-q", default)
        if collab:
            _git(root, "branch", "-q", "-D", PR_BRANCH, ok=(0, 1))


def _record_path(root: str) -> str:
    git_dir = _git(root, "rev-parse", "--git-dir").stdout.decode().strip()
    return os.path.join(os.path.normpath(os.path.join(root, git_dir)), RECORD)


def _shown_path(path: str, root: str) -> str:
    rel = os.path.relpath(path, root)
    return path if rel.startswith("..") else rel


class RunRecord:
    """What a later run needs to undo this run's edits if it dies: written before the first edit, in the git folder,
    and held under an exclusive lock for as long as this process lives. The lock goes with the process, also on
    SIGKILL, so a later run can tell a live run from a dead one without trusting a pid that may be reused."""

    def __init__(self, path: str, fd: int):
        self.path, self.fd = path, fd

    @classmethod
    def create(cls, root: str, data: dict) -> "RunRecord":
        path = _record_path(root)
        if os.path.exists(path):
            raise CheckError("a run record is already at %s" % _shown_path(path, root))
        tmp = "%s.%d.tmp" % (path, os.getpid())
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.write(fd, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
            os.fsync(fd)
            os.replace(tmp, path)  # complete, and already locked, the moment it has its name
        except BaseException:
            os.close(fd)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return cls(path, fd)

    def remove(self) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _restorable(root: str, data: dict) -> bool:
    """True only when the repository is in a shape roll-out alone could have left."""
    start, default, collab = data["start"], data["default"], data["collab"]
    files, subjects = set(data["files"]), set(data["subjects"])
    if not (isinstance(start, str) and isinstance(default, str) and isinstance(collab, bool)):
        return False
    if _git(root, "cat-file", "-e", start + "^{commit}", ok=(0, 1, 128)).returncode != 0:
        return False
    if current_branch(root) not in ((default, PR_BRANCH) if collab else (default,)):
        return False
    ignored = "refs/remotes/origin/" + PR_BRANCH if collab else None  # a pushed pull-request branch is only noted

    def ours(tip: str) -> bool:
        if _git(root, "merge-base", "--is-ancestor", start, tip, ok=(0, 1)).returncode != 0:
            return False
        for line in _git(root, "rev-list", "--parents", "%s..%s" % (start, tip)).stdout.decode().splitlines():
            fields = line.split()
            if len(fields) != 2:
                return False  # a merge, or a root commit
            if _git(root, "log", "-1", "--format=%s", fields[0]).stdout.decode().strip() not in subjects:
                return False
            remote = _git(root, "for-each-ref", "--contains", fields[0], "--format=%(refname)", "refs/remotes").stdout.decode().split()
            if [r for r in remote if r != ignored]:
                return False
        return True

    if not ours("HEAD"):
        return False
    if collab and _git(root, "show-ref", "--verify", "-q", "refs/heads/" + PR_BRANCH, ok=(0, 1)).returncode == 0 and not ours(PR_BRANCH):
        return False
    status = _git(root, "status", "--porcelain=v1", "-z").stdout
    for entry in gitutil.split_z(status):
        if set(entry[:2]) - set("M ") or entry[3:] not in files:  # roll-out only modifies files it names
            return False
    return True


def settle_record(entry: Dict[str, str], run: Run) -> Optional[Result]:
    """A run record left in this repository comes before anything else. Returns the repository's result when it ends
    here (another run is working, a dry run found a dead run's edits, or the record cannot be trusted), and None when
    there is none or `--apply` has just undone the dead run, so the repository goes on as usual."""
    name, root = entry["name"], entry["path"]
    if not os.path.isdir(root):
        return None
    try:
        if os.path.realpath(gitutil.toplevel(root)) != os.path.realpath(root):
            return None
        path = _record_path(root)
    except (CheckError, OSError, subprocess.SubprocessError):
        return None  # not a repository: the usual checks say so
    if not os.path.exists(path):
        return None
    hand = "check it by hand, then delete %s" % _shown_path(path, root)
    changed = Result(name, "blocked", "an earlier run stopped here and the repository changed since; " + hand)
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as exc:
        return Result(name, "blocked", "cannot read the run record (%s); %s" % (exc.strerror, hand))
    try:
        locked = False
        try:
            fcntl.flock(fd, (fcntl.LOCK_EX if run.apply else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except OSError:
            locked = True
        try:
            with os.fdopen(os.dup(fd), encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or data.get("format") != RECORD_FORMAT:
                raise ValueError("format")
            pid, began = data["pid"], data["started"]
            if not (isinstance(pid, int) and isinstance(began, (int, float))):
                raise ValueError("fields")
            data["files"], data["subjects"], data["start"], data["default"], data["collab"]
        except (ValueError, KeyError, OSError):
            if locked:
                return Result(name, "blocked", "another roll-out is working here")
            return Result(name, "blocked", "the run record left here cannot be read; " + hand)
        if locked:
            return Result(name, "blocked", "another roll-out (pid %d) is working here" % pid)
        try:
            fine = _restorable(root, data)
        except (CheckError, OSError, subprocess.SubprocessError, TypeError, AttributeError):
            fine = False
        if not fine:
            return changed
        short = data["start"][:7]
        if not run.apply:
            return Result(name, "interrupted", "an earlier run (pid %d, started %s) stopped here and left its edits; "
                          "--apply undoes them first (back to %s)" % (pid, time.strftime("%H:%M", time.localtime(began)), short))
        _rollback(root, data["start"], data["default"], data["collab"])
        notes = []
        if data["collab"] and _git(root, "show-ref", "--verify", "-q", "refs/remotes/origin/" + PR_BRANCH, ok=(0, 1)).returncode == 0:
            notes.append("origin/%s exists; roll-out never deletes a remote branch" % PR_BRANCH)
        if _git(root, "status", "--porcelain").stdout.strip():
            return Result(name, "blocked", "undid an earlier run's edits, but the repository is still not clean; " + hand)
        os.unlink(path)
        _emit(run.out, "%-14s %s: undid an earlier run's edits; back at %s\n" % ("RESTORED", name, short)
              + "".join("    %s\n" % n for n in notes))
        return None
    finally:
        os.close(fd)


class VerifyTimeout(Exception):
    """The verification ran past its limit and its whole process group was stopped."""

    def __init__(self, limit: float, output: str, log: str):
        super().__init__("verification timed out")
        self.limit, self.output, self.log = limit, output, log


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # exists, but not ours to signal: treat as alive
    return True


def _stop_group(proc: "subprocess.Popen") -> None:
    """Stop the script and everything it started: TERM the group, wait up to STOP_GRACE seconds, then KILL it.
    `subprocess.run` would kill only the direct child, and a test run it started would go on holding the machine-wide lock."""
    with _signals_ignored():
        _stop_group_now(proc)


def _stop_group_now(proc: "subprocess.Popen") -> None:
    pgid = proc.pid  # start_new_session made it the leader of its own group
    if _group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + STOP_GRACE
        while time.monotonic() < deadline:
            proc.poll()  # reaps the leader, so a group of zombies is seen as gone
            if not _group_alive(pgid):
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
    try:
        proc.wait(timeout=STOP_GRACE)
    except subprocess.TimeoutExpired:
        raise CheckError("could not stop the verification script")


def _verify(name: str, root: str, run: Run) -> Tuple[int, str, str]:
    """Run the consumer's verify_commit.sh once, in its own process group. Returns (exit code, output, log path).
    A run past its limit raises VerifyTimeout."""
    os.makedirs(run.log_dir, exist_ok=True)
    log = os.path.join(run.log_dir, _log_name(name))
    timed_out = False
    with open(log, "ab") as fh:  # appended: the quick checks that ran first are in this log already
        # The watcher leads the new group and stops it if this process dies: a SIGKILL cannot be caught here.
        # close_fds (the default) keeps the pipe's write end in this process alone.
        proc = subprocess.Popen([sys.executable, "-I", WATCHER, str(STOP_GRACE), "./" + VERIFY_SCRIPT], cwd=root,
                                stdin=subprocess.PIPE, stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=run.verify_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            code = -1
        finally:
            try:
                _stop_group(proc)  # on a timeout, on any exception, and for children a finished script left behind
            finally:
                proc.stdin.close()
    with open(log, encoding="utf-8", errors="replace") as fh:
        output = fh.read()
    if timed_out:
        raise VerifyTimeout(run.verify_timeout, output, log)
    return code, output, log


def _logged(root: str, argv: List[str], log: str) -> Tuple[int, str]:
    """Run one short command in the consumer, append its output to the log. A command that cannot run or runs past
    QUICK_TIMEOUT is a failure, never a pass."""
    try:
        proc = subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=QUICK_TIMEOUT, env=gitutil.clean_env())
        code, output = proc.returncode, proc.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        code, output = -1, "ran past its %s limit and was stopped\n" % _limit_text(QUICK_TIMEOUT)
    except OSError as exc:
        code, output = 127, "cannot run %s: %s\n" % (argv[0], exc)
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "ab") as fh:
        fh.write(("$ %s\n%s" % (" ".join(argv), output)).encode("utf-8"))
        if output and not output.endswith("\n"):
            fh.write(b"\n")
    return code, output


def _quick_checks(name: str, root: str, run: Run, commands: List[Tuple[str, List[str]]], notes: List[str],
                  have_loader: bool) -> Optional[Result]:
    """`commit-checks verify` first, then each `checks` command. None when all pass. `waits` means the version
    guard wants a bump, so the consumer takes this release with its next version."""
    log = run.log_path(name)
    if have_loader:
        code, output = _logged(root, ["./" + LOADER_FILE, "verify"], log)
        if code != 0:
            if BUMP_HINT in output:
                return Result(name, "waits", "waits for its next version", notes)
            return Result(name, "checks fail", "%s verify failed (exit %d); files restored; log: %s" % (LOADER_FILE, code, log),
                          [_tail(output)] + notes)
    for package, argv in commands:
        code, output = _logged(root, argv, log)
        if code != 0:
            return Result(name, "checks fail", "the %s check failed (exit %d); files restored; log: %s" % (package, code, log),
                          [_tail(output)] + notes)
    return None


def _apply(entry: dict, root: str, updates: List[Planned], run: Run, default: str, collab: bool, verb: str,
           notes: List[str]) -> Result:
    name = entry["name"]
    start = _git(root, "rev-parse", "HEAD").stdout.decode().strip()
    paths = sorted(rel for _t, _g, pin in updates for rel in pin.files)
    record = RunRecord.create(root, {
        "format": RECORD_FORMAT, "start": start, "default": default, "collab": collab, "files": paths,
        "subjects": [t.subject(g.tag) for t, g, _p in updates], "pid": os.getpid(), "started": time.time()})
    try:
        return _apply_recorded(entry, root, updates, run, default, collab, verb, notes, start, paths, record)
    finally:
        record.close()


def _stopped(exc: BaseException, detail: str) -> None:
    if isinstance(exc, KeyboardInterrupt):
        exc.detail = detail  # type: ignore[attr-defined]


def _verification_plan(entry: dict, updates: List[Planned], paths: List[str]) -> Tuple[bool, List[Tuple[str, List[str]]]]:
    """(run the full script, `checks` commands to run instead). A move of only check tools needs `commit-checks verify`
    alone. Any other pin needs the full script, unless every such pin has a `checks` entry; a changed loader always
    needs the full script."""
    checks = entry.get("checks", {})
    others = [t.name for t, _g, _p in updates if t.name not in CHEAP_PINS]
    if LOADER_FILE in paths or any(n not in checks for n in others):
        return True, []
    return False, [(n, checks[n]) for n in others]


def _apply_recorded(entry: dict, root: str, updates: List[Planned], run: Run, default: str, collab: bool, verb: str,
                    notes: List[str], start: str, paths: List[str], record: RunRecord) -> Result:
    name = entry["name"]
    failure: Optional[Result] = None
    phase = "edit"
    have_loader = os.path.isfile(os.path.join(root, LOADER_FILE))
    have_script = os.path.isfile(os.path.join(root, VERIFY_SCRIPT))
    full, commands = _verification_plan(entry, updates, paths)
    run.begin(name, have_loader or (full and have_script))
    try:
        if collab:
            _git(root, "checkout", "-q", "-b", PR_BRANCH)
        for _t, _g, pin in updates:
            pin.write(root)
        _git(root, "add", "--", *paths)  # the consumer's verification scans what is staged
        phase = "verify"
        os.makedirs(run.log_dir, exist_ok=True)
        open(run.log_path(name), "wb").close()  # a log folder reused from an earlier run starts empty
        if not have_loader:
            notes = notes + ["no %s; only the commit hooks checked this" % LOADER_FILE]
        failure = _quick_checks(name, root, run, commands, notes, have_loader)
        code = 0
        if failure is None and full and have_script:
            try:
                code, output, log = _verify(name, root, run)
            except VerifyTimeout as late:
                failure = Result(name, "checks fail", "%s ran past its %s limit and was stopped; files restored; log: %s"
                                 % (VERIFY_SCRIPT, _limit_text(late.limit), late.log), [_tail(late.output)] + notes)
                code = 0
            phase = "commit"
            if failure is None and code != 0:
                status = "needs a version bump" if BUMP_HINT in output else "checks fail"
                failure = Result(name, status, "%s failed (exit %d); files restored; log: %s" % (VERIFY_SCRIPT, code, log),
                                 [_tail(output)] + notes)
        elif failure is None and full:
            notes = notes + ["no %s; only the commit hooks checked this" % VERIFY_SCRIPT]
        phase = "commit"
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
        if failure is None:
            if collab:
                _git(root, "checkout", "-q", default)
            if _git(root, "status", "--porcelain").stdout.strip():
                notes = notes + ["the repository is not clean after the run; check it by hand"]
    except BaseException as exc:
        _rollback(root, start, default, collab)
        record.remove()
        _stopped(exc, "verification stopped, edits undone" if phase == "verify" else "edits undone")
        raise
    if failure is not None:
        _rollback(root, start, default, collab)
        if _git(root, "status", "--porcelain").stdout.strip():
            failure.detail += "; WARNING: the repository is still not clean"  # the record stays: the next run looks again
        else:
            record.remove()
        return failure
    if not run.push:
        record.remove()
        return Result(name, "updated", verb + (", committed on %s, not pushed" % PR_BRANCH if collab else ", not pushed"), notes)
    try:  # a stop from here on leaves the commits and the record: the next run sees whether the push landed
        result = _open_pull_request(name, root, updates, default, verb, notes) if collab else _push(name, root, default, verb, notes)
    except BaseException as exc:
        _stopped(exc, "the commits stay and the push is not confirmed; the next run checks them against origin")
        raise
    record.remove()
    return result


def _push(name: str, root: str, default: str, verb: str, notes: List[str]) -> Result:
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
    p.add_argument("--pin", action="append", required=True, metavar="NAME=vX.Y.Z|latest",
                   help="package and release tag to move to, or `latest` for the newest vX.Y.Z tag in its clone; "
                        "repeat for several (%s)" % ", ".join(sorted(PIN_TYPES)))
    p.add_argument("--only", action="append", default=[], metavar="NAME", help="act only on this listed consumer; repeat for several")
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
            p.error("--pin needs NAME=vX.Y.Z or NAME=latest with NAME one of: %s" % ", ".join(sorted(PIN_TYPES)))
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
    old = _install_handlers()
    try:
        return _main(ns)
    except KeyboardInterrupt as exc:  # before the first repository: nothing was edited
        sys.stdout.flush()
        _say_stopped(exc, [])
        return 2
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def _say_stopped(exc: BaseException, not_reached: List[str]) -> None:
    try:
        sys.stdout.write("roll-out stopped by %s; not reached: %s.\n" % (_signal_name(exc), ", ".join(not_reached) or "none"))
        sys.stdout.flush()
    except OSError:
        pass  # a closed terminal: the exit code still says it


def _main(ns: argparse.Namespace) -> int:
    if ns.push and not ns.apply:
        raise CheckError("--push needs --apply")
    consumers = load_list(ns.list)
    names = {e["name"] for e in consumers.entries}
    for n in ns.only:
        if n not in names:
            raise CheckError("--only names a consumer that is not listed: " + n)
    for n in ns.approve:
        if n not in names:
            raise CheckError("--approve names a consumer that is not listed: " + n)
    # Every release is confirmed on its remote before anything is touched.
    pins = [(PIN_TYPES[n], PIN_TYPES[n].resolve_latest() if tag == "latest" else PIN_TYPES[n].resolve_target(tag))
            for n, tag in sorted(ns.pins.items())]
    unlisted = pinreport.find_unlisted(consumers, [p[0] for p in pins])

    plan = []  # (entry, pins to work on, packages the entry does not name)
    unselected = 0
    for entry in consumers.entries:
        if ns.only and entry["name"] not in ns.only:
            unselected += 1
            continue
        mine = [(p, t) for p, t in pins if not _is_own_clone(entry, t)]
        plan.append((entry, [(p, t) for p, t in mine if p.name in entry["packages"]],
                     [p for p, _t in mine if p.name not in entry["packages"]]))

    log_dir = ""
    if ns.apply:
        log_dir = os.path.abspath(ns.log_dir) if ns.log_dir else tempfile.mkdtemp(prefix="commit-checks-roll-out-")
    run = Run(ns.apply, ns.push, log_dir)
    out = run.out
    _emit(out, "roll-out %s (%s)\n" % (", ".join("%s %s" % (t.package, t.tag) for _p, t in pins),
                                       "apply" if ns.apply else "dry run, nothing edited"))
    if log_dir:
        _emit(out, "verification logs: %s\n" % log_dir)

    decisions: Dict[str, Dict[str, Dict[str, str]]] = {}  # package -> consumer -> decision
    gate_errors: Dict[str, str] = {}
    for ptype, target in pins:  # each gated package runs its own gate once, for the consumers that move it
        if ptype.name not in consumers.gates:
            continue
        try:
            moving = [entry for entry, active, _o in plan if any(p.name == ptype.name for p, _t in active)]
            decisions[ptype.name] = run_gate(consumers, ptype.name, target.tag, pinreport.build(consumers, [ptype], moving, unlisted))
        except CheckError as exc:
            gate_errors[ptype.name] = str(exc)

    results: List[Result] = []
    idle = 0

    def finish(r: Result) -> None:
        began = run.started.get(r.name)
        if began is not None:
            r.detail += " " + _minutes_text(time.monotonic() - began)
        results.append(r)
        text = "%-14s %s%s\n" % (r.status.upper(), r.name, (": " + r.detail) if r.detail else "")
        for note in r.notes:
            for line in note.splitlines():
                text += "    " + line + "\n"
        _emit(out, text)

    stopped: Optional[BaseException] = None
    not_reached: List[str] = []
    for index, (entry, active, others) in enumerate(plan):
        try:
            ended = settle_record(entry, run)  # a record from an earlier run comes before everything else
            if ended is not None:
                finish(ended)
                continue
            if not active:
                problems = undeclared(entry["path"], others) if os.path.isdir(entry["path"]) else []
                if problems:
                    finish(Result(entry["name"], "blocked", "; ".join(problems)))
                else:
                    idle += 1
                continue
            held: Dict[str, str] = {}
            for ptype, _t in active:
                if ptype.name not in consumers.gates or entry["name"] in ns.approve:
                    continue
                if ptype.name in gate_errors:
                    held[ptype.name] = gate_errors[ptype.name]
                    continue
                d = decisions[ptype.name].get(entry["name"])
                if not isinstance(d, dict) or d.get("decision") not in ("go", "hold"):
                    held[ptype.name] = "the gate did not give a go or hold for it"
                elif d["decision"] == "hold":
                    held[ptype.name] = "gate: " + str(d.get("reason", "no reason given"))
            finish(process(entry, active, run, held, others))
        except KeyboardInterrupt as exc:  # the repository in hand is already put back, or was never touched
            stopped = exc
            finish(Result(entry["name"], "interrupted", "%s; %s" % (_signal_name(exc), getattr(exc, "detail", "nothing was edited"))))
            not_reached = [e["name"] for e, a, _o in plan[index + 1:] if a]
            break

    if stopped is not None:
        _say_stopped(stopped, not_reached)
        names = [r.name for r in results if r.status not in OK]
        _emit(out, "%d consumer(s); %d need attention%s.\n" % (len(results) + len(not_reached), len(names), (": " + ", ".join(names)) if names else ""))
        return 2
    for path, loads in unlisted:
        _emit(out, "UNLISTED       %s loads %s but is not in the list\n" % (os.path.relpath(path, consumers.scan_root), ", ".join(loads)))
    bad = [r for r in results if r.status not in OK]
    if idle:
        _emit(out, "%d listed %s not name %s; not touched.\n" % (idle, "repository does" if idle == 1 else "repositories do", " or ".join(sorted(ns.pins))))
    if unselected:
        _emit(out, "%d listed %s not selected by --only; not touched.\n" % (unselected, "repository was" if unselected == 1 else "repositories were"))
    attention = [r.name for r in bad] + [os.path.relpath(path, consumers.scan_root) for path, _l in unlisted]
    _emit(out, "%d consumer(s); %d need attention%s.\n" % (len(results), len(attention), (": " + ", ".join(attention)) if attention else ""))
    return 1 if bad or unlisted else 0
