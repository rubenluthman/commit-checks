# Roadmap

## Active

- [x] Swift app checks: settings keys change only with a migration, and public log values carry a reason.
- [ ] A `roll-out` script: given a release tag, it updates the pin (and the loader copy, when the loader changed) in every repository on a list, commits each with the standard subject, skips repositories with uncommitted changes, and lists those whose checks demand a version bump. Pins stay explicit; no repository follows "latest".
  - Judarn needs it too: pin adapters for npm (`package.json` and `package-lock.json`) and Swift packages (`Package.resolved` and the version requirement), and a hook that can stop before committing, so a release that changes what people see waits for approval.
- [x] 0.2.0: Markdown links, Vale, decision-record format, version and CHANGELOG guards, Conventional Commits, `scan-archive`, `history`.

## Icebox

- [ ] Switch the scanner to its successor once that has a stable release.
- [ ] Run the Python 3.9 CI job on GitHub's macOS runner with the system `python3` instead of `ubuntu-22.04`. That is the interpreter the hooks use on a Mac, and it ends the dependence on an Ubuntu image GitHub will retire. First confirm the runner's system `python3` is still 3.9.
- [ ] Guidance for repository verify scripts: wrap the body in a function (`main() { ...; }` then `main "$@"`) so the shell parses the whole file before running it. Editing a script during a long run otherwise breaks that run.
