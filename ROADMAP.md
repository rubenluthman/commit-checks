# Roadmap

## Active

- read: `pins` and `roll-out` miss a Judarn reference in `project.yml` when `url` or `path` is quoted – write `url: "https://github.com/acme/judarn"` or `path: "../packages/judarn"` in `project.yml`, and `pins` reports no pin found while `roll-out` blocks the repository (`commit_checks/pins_judarn.py`, `_parse_swift_file`)
- read: `pre-push` accepts an unannotated remote release tag when pushed from a branch or non-version ref – push `HEAD:refs/tags/v1.0.0` or `backup:refs/tags/v1.0.0`, and `pre-push` passes because it checks only the local ref name instead of the remote ref (`commit_checks/cli.py`, `_pushed_refs`; `commit_checks/release.py`, `check_pushed_tags`)
- `commit_checks/cli.py` `USAGE` lists only `commit-checks` and `house-style` under `roll-out` and omits `judarn`

## Icebox

- Let the Judarn editor rewrite `Package.resolved` version 1 and the workspace copies of `Package.resolved` (`X.xcworkspace/...`). Both are read today, and roll-out blocks or notes them instead of editing.

- Switch the scanner to its successor once that has a stable release.
- Run the Python 3.9 CI job on GitHub's macOS runner with the system `python3` instead of `ubuntu-22.04`. That is the interpreter the hooks use on a Mac, and it ends the dependence on an Ubuntu image GitHub will retire. First confirm the runner's system `python3` is still 3.9.
- A shared release command: check the version files and the CHANGELOG heading, run the verify script, commit, make the annotated tag, push the commit and the tag, and confirm the remote tag. One app repository has its own release script that does this and repeats checks from `commit_checks/release.py`. Build it when a second repository wants one.
- Guidance for repository verify scripts: wrap the body in a function (`main() { ...; }` then `main "$@"`) so the shell parses the whole file before running it. Editing a script during a long run otherwise breaks that run.
