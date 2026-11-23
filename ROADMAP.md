# Roadmap

## Active

- A `roll-out` script: given a release tag, it updates the pin (and the loader copy, when the loader changed) in every repository on a list, commits each with the standard subject, skips repositories with uncommitted changes, and lists those whose checks demand a version bump. Pins stay explicit; no repository follows "latest".
  - Judarn needs it too: pin adapters for npm (`package.json` and `package-lock.json`) and Swift packages (`Package.resolved` and the version requirement), and a hook that can stop before committing, so a release that changes what people see waits for approval.
  - Not every consumer pins a version. Some load a shared package by local path (for example an Xcode project's `"kind": "local"` package reference), so a release has no pin to update there. The roll-out skips such a repository with a note instead of failing. A later Swift package roll-out reuses these steps with a Swift pin type that covers `exact:` in `Package.swift` and exact versions in an Xcode project's package references.

## Icebox

- Switch the scanner to its successor once that has a stable release.
- Run the Python 3.9 CI job on GitHub's macOS runner with the system `python3` instead of `ubuntu-22.04`. That is the interpreter the hooks use on a Mac, and it ends the dependence on an Ubuntu image GitHub will retire. First confirm the runner's system `python3` is still 3.9.
- A shared release command: check the version files and the CHANGELOG heading, run the verify script, commit, make the annotated tag, push the commit and the tag, and confirm the remote tag. One app repository has its own release script that does this and repeats checks from `commit_checks/release.py`. Build it when a second repository wants one.
- Guidance for repository verify scripts: wrap the body in a function (`main() { ...; }` then `main "$@"`) so the shell parses the whole file before running it. Editing a script during a long run otherwise breaks that run.
