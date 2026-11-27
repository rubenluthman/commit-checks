# Changelog

## [0.7.5] - Unreleased

## [0.7.4] - 2026-11-27

- `pins` and `roll-out` read and move `apple-dev-support`, the merged Apple app and test helpers. `apple-app-support` and `apple-test-support` stay until no repository loads them.

## [0.7.3] - 2026-11-27

- `roll-out` takes `--only NAME` and `--pin NAME=latest`, and runs the consumer's `commit-checks verify` first: a request for a version bump is `WAITS` (edits undone, not a failure). The full `verify_commit.sh` runs only for library pins or a changed loader, or a list entry's `checks` command runs in its place.
- A `roll-out` run may include several gated packages; a gate holds a consumer's move of its own package only.
- `pins` and `roll-out` read and move `markdown-engine`, `apple-app-support`, `apple-test-support` (Swift, sharing Judarn's readers) and `ai-output-evals` (a shell pin in the file a list entry's `files` names). List keys `library` (a Swift range is `RANGE`, not `MANUAL`) and `scanSkip` (folders the scan never enters) are new, and every list key is validated.

## [0.7.2] - 2026-11-26

- `rule-globs` reads the `globs` key and comma lists, matches from the repository root, and lets `**` span any number of folders.
- `pins` and `roll-out` find a Judarn `url` or `path` in `project.yml` when the value is quoted or followed by a comment.
- `pre-push` checks the tag name a push creates, so `git push origin HEAD:refs/tags/v1.0.0` can no longer put an unannotated release tag on the remote. The `roll-out` help lists `judarn`.

## [0.7.1] - 2026-11-26

- `settings-keys` also finds the key given to apple-app-support's `FolderBookmark(key:)`, so a bookmark key is registered as `live` and needs a migration when it goes.

## [0.7.0] - 2026-11-26

- New checks that repositories used to copy into their own verify scripts: `rule-globs`, `swift-filenames`, `binary-media` (on with a `binary_allow` list in the settings) and `dashes-plain`.
- `hooks-installed` also fails a hook whose part above `# --- repository checks ---` differs from the pinned release's template, or that has no such line.

## [0.6.2] - 2026-11-26

- The visibility check closes GitHub's error response before it reports that it cannot reach GitHub.

## [0.6.1] - 2026-11-26

- `roll-out` stops the whole verification process tree, not only the script, when a run ends early, is killed outright or passes its 4-hour limit. A run past the limit is `CHECKS FAIL` with the limit named; `COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT` sets it in seconds.
- `roll-out` prints the pin reader's problems as `PROBLEM` notes under each result, as `pins` does, and prints each repository's line as it ends instead of all at the end. With `--apply` a `START` line and the time taken mark each repository it edits, and the summary line names who needs attention.
- A stopped `roll-out` undoes the repository it was in and exits 2: SIGINT, SIGTERM and SIGHUP are caught, and a run record in the git folder lets the next run undo a run that was killed outright (`INTERRUPTED` in a dry run, `RESTORED` with `--apply`).

## [0.6.0] - 2026-11-26

- **Breaking:** the `roll-out` list needs `packages` on every entry, and `gate` is now `gates`, keyed by package. `roll-out` works only on entries that name a requested package and blocks an entry that names a package with no pin, or holds a pin it does not name. In a gate command, `{pins}` is replaced by a file holding the pin report.
- New mode `pins`: reads which release of each package every listed repository pins, as text or JSON, without editing anything or touching the network.
- Judarn pins are found in more forms: subfolders, `project.yml`, SSH and other hosts, branch and revision pins, workspace copies of `Package.resolved`. Forms `roll-out` cannot edit are reported as `MANUAL` or `LOCAL` with the file named.

## [0.5.2] - 2026-11-25

- `pre-push` rejects pushing a release tag that is not annotated, honoring `annotated_tags_from` and `allow` entries for `release-tags`.

## [0.5.1] - 2026-11-24

- `roll-out` gains the pin type `judarn` (`--pin judarn=vX.Y.Z`). Per repository it moves every form it matches: `@judarn/ui` in `package.json` (lockfile made by `npm install --package-lock-only`, never edited to a version npm lacks), `exact:` in `Package.swift`, or an exact version in an Xcode project, plus `Package.resolved`. A non-exact reference is reported as `MANUAL` and never edited; a local-path reference is noted.

## [0.5.0] - 2026-11-24

- New optional `release.build` setting: the `versions` check fails when a build number (Xcode, plist or regex, as a tuple of integers) is not higher than at the highest release tag.

## [0.4.1] - 2026-11-24

- The `versions` check no longer asks for a version bump when the only changes under a `release.code` pattern are files that the `docs_only` setting counts as docs.
- New mode `docs-only`: exits 0 when every changed file matches the new `docs_only` setting, so a repository's verify script can skip its build and tests. It exits 1 whenever it cannot prove that.

## [0.4.0] - 2026-11-24

- New mode `roll-out`: moves every listed repository to release tags of `commit-checks` and `house-style` (`--pin NAME=vX.Y.Z`, repeatable) with one commit per pin, after checking each tag on its package's remote and running the repository's `./scripts/verify_commit.sh` once. Dry run by default; `--push` is opt-in.
- It skips dirty, off-branch and unsynced repositories, never pushes someone else's work, and opens a pull request on `ci/shared-pins` for collaborative projects instead of pushing.
- A repository under `scanRoot` that loads a package but is not listed makes the run exit 1.

## [0.3.1] - 2026-11-23

- The author-address check accepts `noreply@github.com` as a commit's committer, so pull requests merged on GitHub pass. It is still rejected as author and as tagger, and a repository no longer needs `email_allow` for it.

## [0.3.0] - 2026-11-20

- New check `settings-keys` for Swift apps: with a `settings_keys` setting, a stored-setting key must be registered, and a removed or renamed key must be retired with a migration that exists in the code.
- New check `log-privacy`: a log value marked `privacy: .public` or `%{public}` needs a same-line `// public: <reason>` comment.

## [0.2.7] - 2026-11-19

- The hooks check accepts the absolute `core.hooksPath` that the Claude desktop app sets in a worktree, when it points at that worktree's or the main checkout's `.githooks`.

## [0.2.6] - 2026-11-19

- The loader finds the repository root inside git hooks and linked worktrees, and fails when it is not at `scripts/commit-checks`.

## [0.2.5] - 2026-11-19

- The decision-record check ignores Vale directive comments around a record's title.

## [0.2.4] - 2026-11-19

- New setting `annotated_tags_from`: release tags below that version may stay lightweight.

## [0.2.3] - 2026-11-19

- Two more key shapes: Cloudflare tokens (`cfut_`, `cfat_`, `cfk_`) and Resend keys (`re_`).
- The version check also fails when a commit changes the version to one that already has a release tag.

## [0.2.2] - 2026-11-19

- New setting `subject_max` limits the commit subject length.

## [0.2.1] - 2026-11-19

- The Vale check skips a `.vale.ini` that names no shared style package.

## [0.2.0] - 2026-11-19

- New checks: Markdown links on the staged snapshot, decision-record format, Vale house style, CHANGELOG and version agreement, code changes under a released tag, annotated release tags, and Conventional Commits subjects.
- New commands: `scan-archive` (tarballs and zips before publishing, plus source maps and secret file names) and `history` (every commit, message, author and tag, for a repository about to go public).
- The CI workflow template installs Vale when the repository has a `.vale.ini`.

## [0.1.1] - 2026-11-19

- The private-names rule now applies only to public and going-public repositories, as documented.
- Addresses on the reserved `.invalid`, `.test`, `.example` and `.localhost` domains are not findings.

## [0.1.0] - 2026-11-19

- First release: secret, home-path, email and private-name scans of the staged changes, pushed commits, commit messages and an exact copy of the staged snapshot. They fail when the scanner is missing, the wrong version, crashes or hangs.
- Checks for secret file names, leftover merge markers, the `.gitignore` baseline, installed hooks, commit author addresses and the repository's public or private setting.
- A loader that fetches the pinned release by tag and exact commit ID, plus hook, install-script and CI workflow templates.
