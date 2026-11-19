# Roadmap

## Active

- [ ] Next plan, two checks for the Swift apps:
  - Settings keys change only with a migration. Removing or renaming a stored setting fails unless the repository's settings-key register marks the old key retired and names its migration.
  - Log privacy. Each log value marked public needs a short reason on the same line.
- [x] 0.2.0: Markdown links, Vale, decision-record format, version and CHANGELOG guards, Conventional Commits, `scan-archive`, `history`.

## Icebox

- [ ] Switch the scanner to its successor once that has a stable release.
- [ ] Run the Python 3.9 CI job on GitHub's macOS runner with the system `python3` instead of `ubuntu-22.04`. That is the interpreter the hooks use on a Mac, and it ends the dependence on an Ubuntu image GitHub will retire. First confirm the runner's system `python3` is still 3.9.
- [ ] Guidance for repository verify scripts: wrap the body in a function (`main() { ...; }` then `main "$@"`) so the shell parses the whole file before running it. Editing a script during a long run otherwise breaks that run.
