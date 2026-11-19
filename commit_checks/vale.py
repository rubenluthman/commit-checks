"""House-style check with Vale: pin-and-sync of the shared style package, then a run on tracked prose."""
from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
from typing import List

from . import gitutil
from .errors import CheckError, Finding

LINE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):\d+:(?P<rule>[^:]+):(?P<msg>.*)$")


def _timeout() -> int:
    from .engine import timeout_seconds
    return timeout_seconds()


def check_vale(ctx) -> List[Finding]:
    ini = os.path.join(ctx.root, ".vale.ini")
    if not os.path.isfile(ini):
        return []
    conf = ctx.settings.data.get("vale", {})
    globs = conf.get("globs", ["*.md"])
    exclude = conf.get("exclude", [])
    tracked = gitutil.index_paths(ctx.root)
    exe = shutil.which("vale")
    if not exe:
        if "CONTRIBUTING.md" in tracked and not os.environ.get("CI"):
            sys.stderr.write("NOTICE: vale is not installed: skipping the house style check, which CI runs (brew install vale)\n")
            return []
        raise CheckError("vale is not installed (brew install vale)")
    with open(ini, encoding="utf-8") as fh:
        pin = next((l.strip() for l in fh if l.startswith("Packages")), "")
    styles = os.path.join(ctx.root, ".vale", "styles", "HouseStyle")
    pin_file = os.path.join(styles, ".pin")
    have = ""
    if os.path.isfile(pin_file):
        with open(pin_file, encoding="utf-8") as fh:
            have = fh.read().strip()
    if have != pin or not os.path.isdir(styles):
        try:
            proc = subprocess.run([exe, "sync"], cwd=ctx.root, capture_output=True, timeout=_timeout(), env=gitutil.clean_env())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CheckError("vale sync failed: %s" % exc)
        if proc.returncode != 0 or not os.path.isdir(styles):
            raise CheckError("vale sync failed: the house-style package needs network access once per pinned release")
        with open(pin_file, "w", encoding="utf-8") as fh:
            fh.write(pin + "\n")
    files = [p for p in tracked
             if any(fnmatch.fnmatchcase(p, g) for g in globs) and not any(fnmatch.fnmatchcase(p, e) for e in exclude)
             and os.path.isfile(os.path.join(ctx.root, p))]
    if not files:
        return []
    out: List[Finding] = []
    for i in range(0, len(files), 200):
        batch = files[i:i + 200]
        try:
            proc = subprocess.run([exe, "--output=line"] + batch, cwd=ctx.root, capture_output=True, timeout=_timeout(), env=gitutil.clean_env())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CheckError("vale failed: %s" % exc)
        if proc.returncode not in (0, 1):
            raise CheckError("vale failed (exit %d): %s" % (proc.returncode, proc.stderr.decode("utf-8", "replace").strip()[:200]))
        text = proc.stdout.decode("utf-8", "replace")
        for row in text.splitlines():
            m = LINE.match(row)
            if m:
                out.append(Finding("vale", m.group("file"), int(m.group("line")), "%s: %s" % (m.group("rule"), m.group("msg").strip()), "vale"))
        if proc.returncode == 1 and not any(f for f in out):
            raise CheckError("vale reported a problem but no readable alerts")
    return [f for f in out if not ctx.allowed(f)]
