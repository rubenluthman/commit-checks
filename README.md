# commit-checks

One tested set of commit checks that every repository loads at a pinned release, instead of keeping its own copy of each check in `scripts/verify_commit.sh`.

The point of the package is that a check never quietly passes. A missing tool, the wrong tool version, a crash, a timeout, unreadable output or a git error all make the check fail, with a plain message.

## What it checks

| Check | Catches |
|---|---|
| secrets | API keys, tokens and private-key headers (including OpenAI, Anthropic, GitHub, AWS, Stripe, Google, Slack, npm, Cloudflare and Resend shapes), in files and commit messages |
| home paths | `/Users/name/`, `/home/name/` and the equivalent of a developer folder under a home directory |
| emails | Addresses in content, except noreply, example and allowed ones |
| private names | Names from a private list kept outside the repository (public repositories only) |
| secret files | `.env*`, `.dev.vars*`, `*.pem`, `*.key`, `*.p8`, `*.p12`, `*.mobileprovision`, `id_*` and similar |
| conflict markers | Merge markers left in files |
| `.gitignore` baseline | A `.gitignore` that does not ignore the secret file names above |
| hooks installed | Git not pointed at the tracked hooks; a hook that does not call commit-checks; a hook whose part above the `# --- repository checks ---` line differs from the pinned release's `templates/githooks/` file, or that lacks the line (what sits below it is the repository's own) |
| author addresses | Commit authors, committers and tag authors that are not the GitHub noreply address (public repositories). GitHub's own `noreply@github.com` is accepted as a commit's committer only, so pull requests merged on GitHub pass without `email_allow` |
| visibility | The settings file disagrees with what GitHub says (CI only) |
| links | Relative Markdown links that do not point at a tracked file or folder, with exact case |
| decision records | `docs/adr/` records and their index that break the format (title, status line, four headings, no em dash) |
| house style | Vale alerts, with the pinned style package synced when its pin changes |
| versions | A CHANGELOG whose top release differs from the version source; a build number (`release.build`) not higher than at the highest release tag; code changed under a released tag (files `docs_only` counts as docs never count) |
| release tags | Lightweight release tags |
| commit messages | Subjects that are not Conventional Commits |
| settings keys | Swift apps with a `settings_keys` setting: a stored-setting key that is not registered, a registered key gone from the code, a retired key whose migration is missing |
| rule globs | A `.agents/rules/*.md` rule with `trigger: glob` whose `globs` or `glob` (comma list, braces expand) matches no tracked file. A pattern without `/` matches file names at any depth; one with `/` matches from the repository root, and `**` spans folders. Runs only when the repository tracks such rules |
| Swift file names | Tracked `.swift` files that are not PascalCase (`Type.swift`, `Type+Extension.swift`); `main.swift` and `Package.swift` are excepted. Runs only when Swift files are tracked |
| binary media | Staged images, audio, video and archives not matched by `binary_allow`. Off unless `.commit-checks.json` has that key |
| plain-text dashes | An em dash in `*.yml`, `*.yaml`, `*.sh`, `*.toml` or `*.svg` (Vale does not read them), outside `docs/private/`; use a spaced en dash |
| log privacy | A Swift log value marked `privacy: .public` or `%{public}` without a same-line `// public: <reason>` comment |

Text scans use [gitleaks](https://github.com/gitleaks/gitleaks) 8.30.1 (exactly that version) in two passes: its own default rules, then this package's rules for key shapes, home paths, emails and private names. Matched text is never printed; findings show the rule, file and line.

What is scanned:

- On commit: the staged changes, never the files on disk.
- On push: every commit being pushed, so a secret added and removed again before the push is still found.
- In `verify` and CI: an exact copy of the staged snapshot, exported to a temporary folder. A secret that was staged and then deleted on disk is found; a secret that only sits in a removed line is not.

Requirements: `git`, `gitleaks` 8.30.1, and Python 3.9 or newer (the one that ships with macOS works). Nothing else.

## Using it in a repository

1. Add `.commit-checks.json` at the repository root:

   ```json
   {
     "version": "v0.1.1",
     "commit": "<the full 40-character commit ID of that tag>",
     "source": "https://github.com/OWNER/commit-checks.git",
     "visibility": "public",
     "email_allow": [],
     "allow": []
   }
   ```

   `visibility` is `private`, `public` or `going-public`. The last two also check author addresses and need the private-names list.
2. Copy `templates/loader.py` to `scripts/commit-checks` (executable), the three files in `templates/githooks/` to `.githooks/`, and `templates/install-hooks.sh` to `scripts/install-hooks.sh`. Public repositories also copy `templates/workflows/commit-checks.yml` to `.github/workflows/`.
3. Run `scripts/install-hooks.sh`, and call `scripts/commit-checks verify` from `scripts/verify_commit.sh`.

The loader gets the exact pinned version, from a local clone named by `git config --global commit-checks.path` or else from `source`, and fails if the tag does not point at the pinned commit ID. `verify` fails when the repository's loader differs from the pinned release's template.

### Allowing a finding

Add an entry to `allow` with a rule (a gitleaks rule ID, or a check name such as `secrets`, `emails` or `secret-files`), a path pattern and a written reason. All three are required. Inline `gitleaks:allow` comments and `.gitleaksignore` files are ignored on purpose.

### Other settings

- `release`: `{"source": {"type": "package-json|xcode|pyproject|plist|regex", "path": "...", "pattern": "..."}, "changelog": "CHANGELOG.md", "code": ["*.swift"]}` turns on the version checks. `code` lists the file patterns that count as code under a released tag (default: every file). Files that `docs_only` counts as docs never count as code, even under a matching pattern; see [Skipping builds on docs changes](#skipping-builds-on-docs-changes).
- `release.build`: `{"type": "xcode-build|plist-build|regex", "path": "...", "pattern": "..."}` (optional, next to `release.source`) keeps a build counter that rises with each release. `xcode-build` reads `CURRENT_PROJECT_VERSION` in a `.pbxproj` or JSON `project.xcproj`; `plist-build` reads `CFBundleVersion`. A `regex` with one group splits the value on dots (`1.2` is `(1, 2)`); with several groups each group is one integer, so a font uses `(?s)<key>versionMajor</key>\s*<integer>(\d+)</integer>.*?<key>versionMinor</key>\s*<integer>(\d+)</integer>`. The check compares the staged number with the file at the highest `vX.Y.Z` or `X.Y.Z` tag: it fails when the number is lower, or equal while the version differs from the tag's. It passes when there is no tag or the file is new. A value that is not digits, or more than one distinct value, fails.
- `binary_allow`: a list of path globs (the whole repository-relative path; `*` also crosses `/`) for the binary-media check, for example `["static/*.png"]`. An empty list turns the check on with nothing allowed. Without the key the check is off.
- `links_exclude`: path patterns the link check skips.
- `adr_exempt`: record numbers that keep an old format and skip the heading check.
- `vale`: `{"globs": ["*.md"], "exclude": []}` picks the files Vale checks. The check runs only when the repository has a `.vale.ini`. A repository with a `CONTRIBUTING.md` gets a notice instead of a failure when Vale is missing locally; CI always fails.
- `annotated_tags_from`: a version such as `0.1.10`; release tags below it may stay lightweight. `pre-push` applies the same rule to release tags being pushed, so a lightweight one is stopped before it reaches the remote.
- `conventional_commits`: `false` turns the subject check off. `subject_max`: a number limits the subject length.

### Settings keys and log privacy

Both checks are for Swift code and read the staged files.

**log-privacy** runs in every repository with tracked `*.swift` files. A line containing `privacy: .public` or `%{public}` must carry a comment on the same line, `// public: <reason>`, with a reason. Turn it off only with an `allow` entry for the rule `log-privacy`.

**settings-keys** runs only when `.commit-checks.json` has a `settings_keys` object. Removing or renaming a stored setting without a migration silently loses users' settings, so the register must say what happened to each key:

```json
"settings_keys": {
  "paths": ["App/*.swift"],
  "live": ["showBadge", "selectedTab"],
  "retired": [{"key": "badge", "migration": "migrateBadgeKey", "reason": "renamed to showBadge"}],
  "system": ["AppleInterfaceStyle"]
}
```

- `paths`: globs of tracked Swift files to read.
- `live`: keys the app stores today.
- `retired`: keys no longer stored. `migration` is a name (a function, for example) that must appear in code under `paths`; comments do not count.
- `system`: keys the app only reads.

The check finds keys in `@AppStorage("…")`, `@SceneStorage("…")`, `UserDefaults` calls such as `set`, `object`, `string` and `removeObject` with `forKey: "…"`, and apple-dev-support's `FolderBookmark(key: "…")`. A key passed as a constant (`@AppStorage(Keys.tab)`, `forKey: Keys.tab.rawValue`) is looked up among the string constants and enum raw values in the same files. It ignores `CodingKeys` and dictionary calls such as `removeValue(forKey:)`. A key built at run time cannot be seen.

It fails when a key in code is in no list, when a `live` key is no longer in code, when a retired entry's migration is not found, and when a key is listed twice. A glob that matches no tracked file is an error.

### Private names

One regular expression per line, case-insensitive, `#` for comments. The list lives outside every repository: `~/.config/commit-checks/private-names` on a developer machine, or the `COMMIT_CHECKS_PRIVATE_NAMES` secret in CI. A public or going-public repository fails when the list is missing or empty.

## Commands

`scripts/commit-checks MODE`, where MODE is `pre-commit`, `commit-msg FILE`, `pre-push`, `verify`, `ci`, `scan-archive FILE...`, `history`, `docs-only`, `pins` or `roll-out`. `scan-archive` checks a tarball or zip before it is published, and also flags source maps and secret file names. `history` scans every commit, message, author address and tag; run it before a repository becomes public. Exit code 0 is a pass, 1 means findings, 2 means the check could not run.

## Skipping builds on docs changes

`scripts/commit-checks docs-only` tells a verify script whether a change touches only docs, so it can skip its build and tests. It still runs `commit-checks verify`. A repository opts in with a `docs_only` setting in `.commit-checks.json`:

```json
"docs_only": {"paths": ["*.md"], "except": ["platforms/*", "docs/DESIGN.md"]}
```

The `versions` check reads the same list, so docs that no build reads and no package ships need no version bump. A path is docs when it matches a `paths` pattern and no `except` pattern. Patterns match the whole repository-relative path (`*` also crosses `/`), never the bare file name, so `README.md` does not cover `src/Button/README.md`. A bare `*` in `paths` is rejected.

The mode looks at staged and unstaged changes, deletions, both sides of a rename, and untracked files that are not ignored. It prints one line saying why. Exit 0: every changed file is docs. Exit 1: code changed, or it cannot prove docs-only (no setting, no commit yet, no changes, `CI` set). Exit 2: it could not run. Without the setting it always exits 1. Every failure lands in the full run:

```sh
if scripts/commit-checks docs-only; then DOCS_ONLY=1; else DOCS_ONLY=0; fi
```

The list names docs, not code: a new file type then counts as code, and the check fails closed. A list of code would fail open.

## Rolling out a release

`roll-out` moves every consumer of the shared packages to release tags. For each repository it edits the pins, runs the repository's own verification once, then commits each pin on its own. Any consumer's `scripts/commit-checks` runs it, so the loader needs no change.

```
scripts/commit-checks roll-out --list FILE --pin NAME=vX.Y.Z|latest [--pin ...] [--only NAME ...] [--apply] [--push] [--log-dir DIR] [--approve NAME ...]
```

- Without `--apply` it only reports. `--apply` edits, verifies and commits. `--push` (only with `--apply`) also pushes.
- `--only NAME` (repeatable) limits the run to those list entries. The others are counted in one line (`N listed repositories were not selected by --only; not touched.`) and are not read, settled or edited. A name that is not in the list exits 2. A consumer uses it to pull its own pins.
- `--pin NAME=latest` picks the newest `vX.Y.Z` tag in that package's local clone, by version and not by date, and then checks it on `origin` exactly like an explicit tag. A clone with no such tag, or a newest tag missing on `origin`, exits 2 and touches nothing.
- `--pin` is repeatable; each name picks a pin type. `commit-checks` sets `version` and `commit` in `.commit-checks.json` without touching the file's other content, and replaces `scripts/commit-checks` with the target's `templates/loader.py` when they differ; it commits as `ci: load the shared commit checks at vX.Y.Z`. Changed `templates/githooks/*`, `templates/install-hooks.sh` or `templates/workflows/*` between the consumer's old release and the target are reported, never copied. `house-style` rewrites only the tag in the `Packages` line of `.vale.ini` that points at `https://github.com/<owner>/house-style/releases/download/vX.Y.Z/HouseStyle.zip`; it commits as `ci: load the house style at vX.Y.Z`. A `.vale.ini` without such a line holds no house-style pin.
- `judarn` moves the Judarn design system (`--pin judarn=vX.Y.Z`). It reads the release from a local clone (`JUDARN_PATH` or `git config --global judarn.path`) and checks the tag on its `origin` like the others. Each repository gets every form it matches, in one commit: `build: pin Judarn at vX.Y.Z`.
  - npm: sets an exact `@judarn/ui` version in the root `package.json` and runs `npm install --package-lock-only --save-exact --ignore-scripts` for the lock, and refuses if `npm` rewrites `package.json`. It asks npm whether the version is published; if not, that repository is `BLOCKED` and not edited (after the gate, so a held repository still shows as held), and the others go on.
  - Swift manifest: replaces the version in `exact: "…"` (or `.exact("…")`) of the Judarn `.package(` call in the root `Package.swift`, and the judarn pin in the root `Package.resolved`.
  - Xcode project (top folder): a one-patch range (`version-range-min` X, `version-range-max` X plus one patch, the exact pin in `project.xcproj`) or `kind = exactVersion` in `project.pbxproj`, plus the `Package.resolved` under that project's `project.xcworkspace/xcshareddata/swiftpm/`.
  - A reference that is not an exact version (`from:`, a wider range, a branch) is reported as `MANUAL` and never edited; the run then exits 1. A local-path reference is noted as `LOCAL`. A missing `Package.resolved`, or one without a judarn pin, is noted and not created.
  - **Stale `originHash`:** the Swift types edit `Package.resolved` by hand and leave `originHash` as it is. Checked on 2026-11-24 with a Package.swift or project edit plus a hand-edited `Package.resolved` (Xcode 27.2): `swift package resolve` and `xcodebuild -resolvePackageDependencies` both accepted the file, resolved the new version and left it byte-for-byte unchanged. Neither needs the resolver run by `roll-out`; the consumer's own verify run still resolves the packages.
- `markdown-engine`, `apple-dev-support`, and its earlier parts `apple-app-support` and `apple-test-support`, move a Swift package found by the last part of its repository URL (`https://github.com/<owner>/<name>`, with or without `.git`, any owner). They read and edit the same Swift forms as `judarn` (`exact:` or `.exact(` in the root `Package.swift`, a one-patch range or `exactVersion` in an Xcode project, each paired `Package.resolved`) and share its code; they commit as `build: pin <name> at vX.Y.Z`. A repository that is a library lists `"library": true`: a range there (`.upToNextMinor(from:)`, `from:`, `.upToNextMajor`) is reported as `RANGE` with its file named, needs no move and keeps the exit code at 0. Without `library` the same range is `MANUAL`; a branch or revision is always `MANUAL`. A local path is `LOCAL`.
- `ai-output-evals` moves a shell pin. The entry names the file: `"files": {"ai-output-evals": "scripts/eval.sh"}`. The file must hold exactly one `EVALS_TAG="vX.Y.Z"` line and one `EVALS_COMMIT="<40 hex>"` line (the prefix belongs to the pin type). Both are edited in place and every other byte stays; a missing, doubled or malformed line blocks the repository. It commits as `build: pin ai-output-evals at vX.Y.Z`.
- Each package comes from a local clone: `COMMIT_CHECKS_PATH` or `git config --global commit-checks.path` (this source tree is the last fallback), and `HOUSE_STYLE_PATH` or `git config --global house-style.path`; the other packages use `<NAME>_PATH` (upper case, dashes as underscores, such as `MARKDOWN_ENGINE_PATH`) or `git config --global <name>.path`. Before any edit every tag must exist in its clone and on that clone's `origin`, and point at the same commit; otherwise the command exits 2 and touches nothing.
- The list file is JSON and stays outside the repository: `{"scanRoot": "<folder>", "gates": {"<package>": ["<command>", "{tag}", "{pins}"]}, "consumers": [{"name": "...", "path": "...", "packages": ["commit-checks", "judarn"]}]}`. `scanRoot`, `scanSkip` and `gates` are optional; relative paths resolve against the list file's folder. A repository up to two folders below `scanRoot` that loads any given package but is not listed is reported as `UNLISTED` and makes the run exit 1. `"scanSkip": ["<folder>", ...]` names folders, relative to `scanRoot` and inside it, that this scan never enters. The scan cannot see a shell pin (it does not know the file), so `ai-output-evals` is found only in listed repositories.
- An entry may carry `"checks": {"<package>": ["<command>", "<arg>", ...]}`: a cheaper command, run in the consumer's folder, that replaces the full verification for a move of that package (see the verification bullet). Other optional entry keys are `"files"` and `"library"` (above). The list is checked when it loads: `checks` maps a known package to a non-empty list of strings, `files` maps a known package to a relative path inside the repository, `library` is `true` or `false`, and `scanSkip` is a list of relative folders inside `scanRoot` (it needs `scanRoot`). Anything else exits 2.
- `packages` is required on every entry: the packages that repository loads (`commit-checks`, `house-style`, `judarn`, `markdown-engine`, `apple-dev-support`, `apple-app-support`, `apple-test-support`, `ai-output-evals`). `roll-out` works only on entries that name a requested package; one line counts the others, and they do not change the exit code. The list is checked both ways: an entry that names a package but has no pin for it is `BLOCKED` ("listed as loading X, but no pin found"), and so is a repository that holds a pin for a requested package its entry does not name. A package's own clone (the local clone a tag comes from) is never a consumer of itself.
- Judarn pins are read in every form, not only the ones `roll-out` edits: the repository folder plus two folder levels below it (skipping dot folders, `.build`, `build`, `DerivedData`, `node_modules`, `scripts` and `Pods`), only files Git tracks, any host over HTTPS or SSH, `package.json` (all four dependency groups) with its lockfile, `Package.swift`, `project.yml`, `project.xcproj`, `project.pbxproj`, and every `Package.resolved` copy, each paired with its own manifest or project. A form `roll-out` cannot edit (a subfolder, `project.yml`, a stale workspace copy of `Package.resolved`, a non-GitHub URL, a range, a branch or a revision) is `MANUAL` or `LOCAL` with its file named, never skipped. Two Judarn references in one manifest block the repository.
- A consumer is skipped, without edits, when it has any uncommitted change or is not on its default branch. With `--push`, after a `git fetch`, it is also skipped when its default branch is ahead of, behind or diverged from `origin`, so the command never pushes someone else's work. A consumer already on every target is `CURRENT`.
- A project whose `AGENTS.md` contains `Collaborative: use branches and pull requests.` gets a pull request. `--apply` commits on a new branch `ci/shared-pins` cut from the default branch and switches back; `--push` pushes that branch and runs `gh pr create`. The command never merges.
- With `--apply`, after the pins are written and staged, verification runs in three layers, and the output of all of them goes to one log per consumer in `--log-dir` (default: a new temporary folder, printed):
  1. The consumer's `scripts/commit-checks verify` runs first. The loader reads the staged `.commit-checks.json`, so it runs the release being rolled out. It takes about 2 seconds and runs every check a move of a check tool can affect. If its output asks for a version bump ("bump the version"), every edit is undone and the line is `WAITS          NAME: waits for its next version`. `WAITS` is not a failure: it is not named among the consumers that need attention and does not change the exit code. The consumer takes the release when its next version is opened. Any other failure is `CHECKS FAIL`.
  2. A move of only `commit-checks` and `house-style` pins stops there and goes on to the commits, whose hooks still run. When a pin of any other package moves, `roll-out` runs the entry's `checks` command for each such package instead of the full script, if every such package has one (a failing command is `CHECKS FAIL` with its output in the log); otherwise it runs the full script (layer 3).
  3. `./scripts/verify_commit.sh` runs once from the repository root when a pin other than `commit-checks` or `house-style` moved and its package has no `checks` entry, and always when the loader `scripts/commit-checks` changed. If it fails, every edit is undone and the consumer is `CHECKS FAIL`, or `NEEDS A VERSION BUMP` when the output says "bump the version"; the last lines and the log path are shown. A repository without the script is verified by its commit hooks only, with a note.
  The command takes no lock: a consumer's script queues its own heavy steps. Every command `roll-out` runs in a consumer (the script, `commit-checks verify`, each `checks` command and each commit with its hooks) runs in its own process group, led by a small watcher (`commit_checks/verifywatch.py`) that holds a pipe to `roll-out`. `roll-out` stops the whole group (TERM, then KILL after 10 seconds) when the command ends or the run ends early, and the watcher does the same if the pipe closes because `roll-out` died, SIGKILL included, so a test run a command started never outlives it. The script, and each commit, that runs past 4 hours is stopped the same way; the consumer is `CHECKS FAIL` with the limit named and its files restored, and the run goes on with the next consumer. `COMMIT_CHECKS_ROLL_OUT_VERIFY_TIMEOUT` (seconds above zero) replaces the limit; any other value exits 2. `commit-checks verify` and each `checks` command get 10 minutes.
- A run may include several gated packages. Each gate comes from `gates` and runs once, for its own package, with `{tag}` replaced by the tag and `{pins}` by the path of a temporary file holding the `pins --json` report for exactly the consumers `roll-out` is about to act on; the file is deleted afterward. The gate prints JSON: `{"<name>": {"decision": "go" | "hold", "reason": "..."}}`. A hold covers that consumer's move of that package only: its other pins still move, and the result is `HELD` with the held package named. A held consumer is not edited for that package unless named with `--approve` (which covers every gate). A gate that fails, prints bad JSON or leaves a consumer out holds it. The old single `gate` key is an error (exit 2): use `gates`, keyed by package.
- Under each result `roll-out` prints the pin reader's problems as `    PROBLEM <text>` notes, worded as in `pins` (a resolved version that differs from the requirement, a lockfile that differs from `package.json`, an extra local path), whatever the status, a `CURRENT` repository included. A problem that an editor's note already states is not printed twice. Problems never change a status or the exit code.
- Output follows the run. The header prints first, before the gate, and each result line prints, flushed, as its repository ends. With `--apply`, a `START          NAME: at HH:MM; log: NAME.log` line prints when `roll-out` begins to edit a repository (no `log:` part when it has no verify script); repositories that end without an edit get their result line only, and a dry run prints no `START` line. A result line that followed a `START` ends with the time it took, as `(41 min)` (`(<1 min)` under half a minute). The last line names who needs attention: `4 consumer(s); 2 need attention: alpha, beta.`
- A stopped run puts the repository in hand back. SIGINT, SIGTERM and SIGHUP unwind the run: the verification's process group is stopped, the edits and any commits of the run are undone, and the result is `INTERRUPTED    NAME: SIGTERM; verification stopped, edits undone`. Then `roll-out stopped by SIGTERM; not reached: NAMES.` and the summary print, and the exit code is 2. A stop during the push or the pull request leaves the commits and the run record (below), because the push may have landed; the next run decides.
- The run record, `commit-checks-roll-out.json` in the repository's git folder (`git rev-parse --git-dir`, so a worktree has its own), covers a run that dies without unwinding (SIGKILL, a crash, a power cut). It is written atomically before the first edit and held under an exclusive `flock`, which goes with the process, and removed when the repository ends. It holds `format`, `start` (the commit before the run), `default` (the default branch), `collab`, `files` (the files the run edits), `subjects` (the commit subjects it makes), `pid` and `started`. Every later run, dry or not, looks for a record in each listed repository first, including those that do not name the package being moved. A record whose lock is held is `BLOCKED` (another roll-out is working there). An unlocked record in a repository that only `roll-out` could have left that way (on the default branch, or `ci/shared-pins` in a collaborative project; every commit after `start` has a recorded subject, one parent and is on no remote branch; only recorded files changed, none untracked) is `INTERRUPTED` in a dry run, exit 1. `--apply` undoes it, deletes the record, prints `RESTORED`, and goes on with the repository as usual. A collaborative repository whose `ci/shared-pins` reached `origin` gets a note; `roll-out` never deletes a remote branch. Any other state, or a record it cannot read, is `BLOCKED`: check it by hand, then delete the record.
- Each commit runs the consumer's hooks. If one fails, every edit and earlier commit of the run is undone and the hook's output is shown. A consumer counts as updated only after commits that contain the new pins.

Exit code 0 means every consumer is current, updated (or would be, in a dry run), waiting for its next version, a local-path reference or a library range (`RANGE`), and no unlisted consumer exists; 1 means some consumer was skipped, held, blocked or failed its checks, or one is unlisted; 2 means the command could not run or was stopped by a signal.

## Reading pins

```
scripts/commit-checks pins --list FILE --package NAME [--package NAME ...] [--json]
```

Reads which release of each package every listed repository pins, from the same list as `roll-out`. It reads every package `roll-out` moves; the Swift packages in the same forms as Judarn's Swift side, and `ai-output-evals` from the file the entry's `files` key names (the version is the tag, such as `v1.2.0`; a missing, doubled or malformed line is a problem and no pin). It edits nothing, needs no tag and uses no network. The text form shows each repository's version and problems. With `--json` it prints:

```
{"format": 1,
 "consumers": [{"name": "...", "path": "/abs/path", "exists": true,
   "packages": {"judarn": {"declared": true, "detected": true,
     "forms": [{"kind": "npm | swift | settings | vale | shell", "file": "App.xcodeproj/project.xcproj",
                "requirement": {"type": "exact | range | upToNextMinor | upToNextMajor | branch | revision | local | unknown", "version": "1.0.0", "max": "1.0.1"},
                "resolved": {"file": "...", "version": "1.0.0", "revision": "...", "branch": null},
                "editable": true}],
     "version": "1.0.0",
     "problems": ["resolved 0.9.0 differs from exact 1.0.0 (Package.swift)"]}}}],
 "unlisted": [{"path": "/abs/path", "loads": ["judarn"]}]}
```

`requirement` and `resolved` are `null` when a form has none (a lockfile with no manifest). `editable` says whether `roll-out` can edit every file of the form. `version` is the one version all forms agree on, else `null`. `problems` holds each mismatch (a resolved version or lockfile that differs from its requirement, a range where an exact version is expected, a local path, two references in one file, forms that disagree), `listed as loading X, but no pin found` and `loads X, but the list does not name it`. Exit code 0 means the report is complete, whatever it finds; 2 means it could not run (an unreadable list, an unknown package).

## Tests

`./scripts/verify_commit.sh` runs the suite and the canary. Fake secrets are built from pieces while the tests run, so no key-shaped text sits in the repository.

The canary run replaces every scanner with one that reports nothing and requires that the suite then fails for every check, which shows the tests would notice a checker that quietly passes.

## What it does not protect against

`git commit --no-verify` skips hooks. Public repositories run the same checks in CI as a backstop.

## License

MIT.
