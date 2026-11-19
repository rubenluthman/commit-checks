# Roadmap

## Active

- [ ] 0.2.0: Markdown links, Vale, decision-record format, version and CHANGELOG guards, Conventional Commits, `scan-archive`, `history`.

## Icebox

- [ ] Switch the scanner to its successor once that has a stable release.
- [ ] Run the Python 3.9 CI job on GitHub's macOS runner with the system `python3` instead of `ubuntu-22.04`. That is the interpreter the hooks use on a Mac, and it ends the dependence on an Ubuntu image GitHub will retire. First confirm the runner's system `python3` is still 3.9.
