"""Runs one of a consumer's commands (verification script, quick check, commit) for roll-out, and stops it if roll-out dies.

Started by roll-out as `python verifywatch.py GRACE ARGV...` in a new session, so this process leads the process group
the script and its children join. Roll-out holds the write end of this process's standard input. When that end closes
before the command ends, roll-out is gone (also by SIGKILL, which no handler can catch): the whole group gets TERM,
then KILL after GRACE seconds, so a test run the script started never keeps the machine-wide lock. Otherwise this
process exits with the command's exit code.

Not part of the checks: it imports nothing from this package and runs as a plain script.
"""
import os
import signal
import subprocess
import sys
import threading
import time


def _stop_group(child: "subprocess.Popen", grace: float) -> None:
    deadline = time.monotonic() + grace
    try:
        os.killpg(os.getpgrp(), signal.SIGTERM)  # this process ignores it: see main
    except OSError:
        pass
    while child.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    while time.monotonic() < deadline:  # leave the script's own children their share of the grace
        time.sleep(0.05)
    try:
        os.killpg(os.getpgrp(), signal.SIGKILL)  # takes this process along, which is the end
    except OSError:
        pass


def main(argv) -> int:
    grace, command = float(argv[1]), argv[2:]
    signal.signal(signal.SIGTERM, lambda *_a: None)  # a handler, not SIG_IGN: the script must not inherit an ignore
    try:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL)  # output goes to roll-out's log
    except OSError as exc:
        sys.stderr.write("cannot run %s: %s\n" % (command[0], exc.strerror))
        return 127

    def watch() -> None:
        try:
            while os.read(0, 4096):
                pass
        except OSError:
            pass
        if child.poll() is None:
            _stop_group(child, grace)

    threading.Thread(target=watch, daemon=True).start()
    code = child.wait()
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
