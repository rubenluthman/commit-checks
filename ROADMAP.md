# Roadmap

## Active

- `roll-out` commits under a fixed 15-minute limit that also counts the consumer's commit hook waiting for the machine-wide heavy-job lock. A consumer whose hook runs its full test suite then times out when another heavy job holds the lock, and the timed-out commit leaves its hook and the queued test run behind, still waiting for the lock. Give the commit the verify's limit (or exclude lock waits), and run the commit, the quick checks (`commit-checks verify`, `checks` commands) and the verify in their own process group, ended as a whole on a timeout.
- Remove the `apple-app-support` and `apple-test-support` pin types (and their tests and README lines) once no listed repository loads them: both were merged into `apple-dev-support`. `pins --package apple-app-support --package apple-test-support` on the consumer list shows when that is. This is a code change, so open the next version first.

## Icebox

- Let the Judarn editor rewrite `Package.resolved` version 1 and the workspace copies of `Package.resolved` (`X.xcworkspace/...`). Both are read today, and roll-out blocks or notes them instead of editing.

- Switch the scanner to its successor once that has a stable release.
- Run the Python 3.9 CI job on GitHub's macOS runner with the system `python3` instead of `ubuntu-22.04`. That is the interpreter the hooks use on a Mac, and it ends the dependence on an Ubuntu image GitHub will retire. First confirm the runner's system `python3` is still 3.9.
- A shared release command: check the version files and the CHANGELOG heading, run the verify script, commit, make the annotated tag, push the commit and the tag, and confirm the remote tag. One app repository has its own release script that does this and repeats checks from `commit_checks/release.py`. Build it when a second repository wants one.
- Guidance for repository verify scripts: wrap the body in a function (`main() { ...; }` then `main "$@"`) so the shell parses the whole file before running it. Editing a script during a long run otherwise breaks that run.
