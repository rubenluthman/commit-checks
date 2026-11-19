"""Canary run: replace every scanner and check with one that reports nothing, run the suite, and
require that at least one must-fail test of every check fails. This proves the suite would notice
a checker that quietly passes.

Usage: python3 tests/canary.py
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
os.environ["COMMIT_CHECKS_CANARY"] = "1"

import support  # noqa: E402


class Recorder(unittest.TextTestResult):
    failed_checks = set()

    def _note(self, test):
        Recorder.failed_checks.add(getattr(test, "CHECK", ""))

    def addFailure(self, test, err):
        self._note(test)
        super().addFailure(test, err)

    def addError(self, test, err):
        self._note(test)
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self._note(test)
        super().addSubTest(test, subtest, err)


def main():
    # Run the suite with the canary shim on the path of every child process.
    original = support.env_for
    support.env_for = lambda *a, **kw: original(*a, **dict(kw, canary=True))
    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
    result = unittest.TextTestRunner(resultclass=Recorder, stream=open(os.devnull, "w")).run(suite)

    from commit_checks import checks
    expected = set(checks.CHECKS) | {"engine"}  # the loader is not a scanner; its tests do not use the shim
    missing = expected - Recorder.failed_checks
    print("canary: %d tests, %d failed as they should" % (result.testsRun, len(result.failures) + len(result.errors)))
    if missing:
        print("CANARY FAILED: no test noticed a silent scanner for: " + ", ".join(sorted(missing)))
        return 1
    print("canary ok: the suite fails for every check when the scanners report nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
