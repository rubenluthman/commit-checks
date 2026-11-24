# Roadmap

## Active

- Close the files and sockets that older tests leave open (`tests/test_text_rules.py` and the loader tests), so the verify log prints no `ResourceWarning` and a new warning stands out.
- Pin types for npm (`package.json` and `package-lock.json`) and Swift packages (`exact:` in `Package.swift`, exact versions in an Xcode project's package references, and the matching `Package.resolved`), so `roll-out` can move Judarn's consumers. A local-path reference gets a note, never an edit. First settle whether SwiftPM and Xcode accept a hand-edited `Package.resolved` whose `originHash` is stale.
- Tests for two `roll-out` failure paths that have none: a failed `git fetch` with `--push`, and a verify run that hits its time limit.
- `roll-out` prints one line per repository as it starts and ends it, instead of only a report at the end, so a run that takes hours can be followed.
- `roll-out` leaves no half-done repository when it is interrupted: an interrupted run leaves its pin edits staged today. Record each repository's starting commit where a later run finds it and restores it first.

## Icebox

- Switch the scanner to its successor once that has a stable release.
- Run the Python 3.9 CI job on GitHub's macOS runner with the system `python3` instead of `ubuntu-22.04`. That is the interpreter the hooks use on a Mac, and it ends the dependence on an Ubuntu image GitHub will retire. First confirm the runner's system `python3` is still 3.9.
- A shared release command: check the version files and the CHANGELOG heading, run the verify script, commit, make the annotated tag, push the commit and the tag, and confirm the remote tag. One app repository has its own release script that does this and repeats checks from `commit_checks/release.py`. Build it when a second repository wants one.
- Guidance for repository verify scripts: wrap the body in a function (`main() { ...; }` then `main "$@"`) so the shell parses the whole file before running it. Editing a script during a long run otherwise breaks that run.
