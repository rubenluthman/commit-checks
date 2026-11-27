#!/usr/bin/env bash
# Full verification: the test suite, the canary run, and this repository's own checks.
# The suite starts about 1,000 child processes and builds 6,000-file repositories; run one heavy job at a time.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
fail() { echo "FAIL: $*" >&2; exit 1; }
# A machine-wide lock for memory-heavy jobs, if `heavy-run` is installed; otherwise run directly (CI, other computers).
heavy() { if command -v heavy-run >/dev/null 2>&1; then heavy-run "$@"; else "$@"; fi; }

# Docs are files no build reads, no script generates and no package ships (`docs_only` in .commit-checks.json).
if PYTHONPATH=. python3 -B -m commit_checks docs-only; then
  echo "1-2. Skipping the tests and the canary: docs-only change."
else
  echo "1. Tests..."
  heavy python3 -B -m unittest discover -s tests -q || fail "tests failed"

  echo "2. Canary (every scanner reports nothing; the suite must fail for every check)..."
  heavy python3 -B tests/canary.py || fail "canary failed"
fi

echo "3. This repository's own checks (hooks, secrets, paths, emails, names)..."
PYTHONPATH=. python3 -B -m commit_checks verify || fail "commit-checks failed"

echo "Verification passed."
