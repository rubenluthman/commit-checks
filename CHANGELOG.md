# Changelog

## [Unreleased]

## [0.1.1] - 2026-11-19

- The private-names rule now applies only to public and going-public repositories, as documented.
- Addresses on the reserved `.invalid`, `.test`, `.example` and `.localhost` domains are not findings.

## [0.1.0] - 2026-11-19

- First release: secret, home-path, email and private-name scans of the staged changes, pushed commits, commit messages and an exact copy of the staged snapshot. They fail when the scanner is missing, the wrong version, crashes or hangs.
- Checks for secret file names, leftover merge markers, the `.gitignore` baseline, installed hooks, commit author addresses and the repository's public or private setting.
- A loader that fetches the pinned release by tag and exact commit ID, plus hook, install-script and CI workflow templates.
