# AGENTS.md

Shared commit checks, loaded by other repositories at a pinned release. Python standard library only, Python 3.9 compatible (no `match`, no `X | Y` types at run time).

- This repository is public. Never name a private project, a person, a school, a machine detail or a home path in files, commit messages or tags. Commit with the GitHub noreply address.
- Every check fails closed: any error raises `CheckError`, which exits 2. Never add a path where an error becomes a pass.
- Fake secrets in tests are built from pieces (`tests/support.py`), never written as one literal. A new check needs a must-fail and a must-pass test, and a `CHECK` tag on its test class so the canary covers it.
- Verify with `./scripts/verify_commit.sh` (needs `gitleaks` 8.30.1). The suite is heavy: run one heavy job at a time.
- Tags are immutable and annotated; repositories pin the tag and its commit ID. A change to `templates/loader.py` forces every repository to update its loader, so make it rarely.
- Releases reach consumers through the shared commit-checks roll-out; never edit a consumer or file an item there.
- `docs`: the README is the only manual; keep `CHANGELOG.md` and `ROADMAP.md` current.
