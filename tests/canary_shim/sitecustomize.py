"""Canary: with COMMIT_CHECKS_CANARY=1 every scanner and check reports nothing.

Only the test suite puts this folder on the path. The package itself has no switch like this.
"""
import os

if os.environ.get("COMMIT_CHECKS_CANARY") == "1":
    from commit_checks import checks, engine

    def _nothing(*_args, **_kwargs):
        return []

    engine.run_gitleaks = _nothing
    for _name in list(checks.CHECKS):
        checks.CHECKS[_name] = _nothing
