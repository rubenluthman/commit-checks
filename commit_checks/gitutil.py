"""Safe git helpers. Every call fails closed: a git error raises CheckError."""
from __future__ import annotations

import os
import subprocess
from typing import List, Optional

from .errors import CheckError

GIT_TIMEOUT = 120


def clean_env() -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLEAKS")}
    env["LC_ALL"] = "C"
    return env


def run(args: List[str], cwd: str, input_bytes: Optional[bytes] = None, ok=(0,), timeout: int = GIT_TIMEOUT):
    try:
        proc = subprocess.run(
            ["git"] + args, cwd=cwd, input=input_bytes, capture_output=True, timeout=timeout, env=clean_env()
        )
    except FileNotFoundError:
        raise CheckError("git is not installed")
    except subprocess.TimeoutExpired:
        raise CheckError("git timed out: git " + " ".join(args[:2]))
    if proc.returncode not in ok:
        err = proc.stderr.decode("utf-8", "replace").strip()[:300]
        raise CheckError("git %s failed (%d): %s" % (" ".join(args[:2]), proc.returncode, err))
    return proc


def toplevel(cwd: str) -> str:
    out = run(["rev-parse", "--show-toplevel"], cwd).stdout.decode("utf-8", "replace").strip()
    if not out:
        raise CheckError("not inside a git repository")
    return out


def git_common_dir(root: str) -> str:
    out = run(["rev-parse", "--git-common-dir"], root).stdout.decode("utf-8", "replace").strip()
    return out if os.path.isabs(out) else os.path.normpath(os.path.join(root, out))


def split_z(data: bytes) -> List[str]:
    return [p.decode("utf-8", "surrogateescape") for p in data.split(b"\0") if p]


def staged_added_paths(root: str) -> List[str]:
    """Paths added, copied, modified or renamed in the index. Deletions are not listed."""
    out = run(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z", "--no-renames"], root).stdout
    return split_z(out)


def index_paths(root: str) -> List[str]:
    return split_z(run(["ls-files", "-z"], root).stdout)


def export_index(root: str, dest: str) -> None:
    """Write exactly what is staged into dest, whatever the files on disk say."""
    prefix = dest.rstrip("/") + "/"
    run(["checkout-index", "-a", "-f", "--prefix=" + prefix], root, timeout=600)
