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
| hooks installed | Git not pointed at the tracked hooks |
| author addresses | Commit authors, committers and tag authors that are not the GitHub noreply address (public repositories). GitHub's own `noreply@github.com` is accepted as a commit's committer only, so pull requests merged on GitHub pass without `email_allow` |
| visibility | The settings file disagrees with what GitHub says (CI only) |
| links | Relative Markdown links that do not point at a tracked file or folder, with exact case |
| decision records | `docs/adr/` records and their index that break the format (title, status line, four headings, no em dash) |
| house style | Vale alerts, with the pinned style package synced when its pin changes |
| versions | A CHANGELOG whose top release differs from the version source; a build number (`release.build`) not higher than at the highest release tag; code changed under a released tag (files `docs_only` counts as docs never count) |
| release tags | Lightweight release tags |
| commit messages | Subjects that are not Conventional Commits |
| settings keys | Swift apps with a `settings_keys` setting: a stored-setting key that is not registered, a registered key gone from the code, a retired key whose migration is missing |
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
- `links_exclude`: path patterns the link check skips.
- `adr_exempt`: record numbers that keep an old format and skip the heading check.
- `vale`: `{"globs": ["*.md"], "exclude": []}` picks the files Vale checks. The check runs only when the repository has a `.vale.ini`. A repository with a `CONTRIBUTING.md` gets a notice instead of a failure when Vale is missing locally; CI always fails.
- `annotated_tags_from`: a version such as `0.1.10`; release tags below it may stay lightweight.
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

The check finds keys in `@AppStorage("…")`, `@SceneStorage("…")`, and `UserDefaults` calls such as `set`, `object`, `string` and `removeObject` with `forKey: "…"`. A key passed as a constant (`@AppStorage(Keys.tab)`, `forKey: Keys.tab.rawValue`) is looked up among the string constants and enum raw values in the same files. It ignores `CodingKeys` and dictionary calls such as `removeValue(forKey:)`. A key built at run time cannot be seen.

It fails when a key in code is in no list, when a `live` key is no longer in code, when a retired entry's migration is not found, and when a key is listed twice. A glob that matches no tracked file is an error.

### Private names

One regular expression per line, case-insensitive, `#` for comments. The list lives outside every repository: `~/.config/commit-checks/private-names` on a developer machine, or the `COMMIT_CHECKS_PRIVATE_NAMES` secret in CI. A public or going-public repository fails when the list is missing or empty.

## Commands

`scripts/commit-checks MODE`, where MODE is `pre-commit`, `commit-msg FILE`, `pre-push`, `verify`, `ci`, `scan-archive FILE...`, `history`, `docs-only` or `roll-out`. `scan-archive` checks a tarball or zip before it is published, and also flags source maps and secret file names. `history` scans every commit, message, author address and tag; run it before a repository becomes public. Exit code 0 is a pass, 1 means findings, 2 means the check could not run.

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
scripts/commit-checks roll-out --list FILE --pin NAME=vX.Y.Z [--pin NAME=vX.Y.Z ...] [--apply] [--push] [--log-dir DIR] [--approve NAME ...]
```

- Without `--apply` it only reports. `--apply` edits, verifies and commits. `--push` (only with `--apply`) also pushes.
- `--pin` is repeatable; each name picks a pin type. `commit-checks` sets `version` and `commit` in `.commit-checks.json` without touching the file's other content, and replaces `scripts/commit-checks` with the target's `templates/loader.py` when they differ; it commits as `ci: load the shared commit checks at vX.Y.Z`. Changed `templates/githooks/*`, `templates/install-hooks.sh` or `templates/workflows/*` between the consumer's old release and the target are reported, never copied. `house-style` rewrites only the tag in the `Packages` line of `.vale.ini` that points at `https://github.com/<owner>/house-style/releases/download/vX.Y.Z/HouseStyle.zip`; it commits as `ci: load the house style at vX.Y.Z`. A `.vale.ini` without such a line is not a consumer.
- Each package comes from a local clone: `COMMIT_CHECKS_PATH` or `git config --global commit-checks.path` (this source tree is the last fallback), and `HOUSE_STYLE_PATH` or `git config --global house-style.path`. Before any edit every tag must exist in its clone and on that clone's `origin`, and point at the same commit; otherwise the command exits 2 and touches nothing.
- The list file is JSON and stays outside the repository: `{"scanRoot": "<folder>", "gate": ["<command>", "{tag}"], "consumers": [{"name": "...", "path": "..."}]}`. `scanRoot` and `gate` are optional; relative paths resolve against the list file's folder. A repository up to two folders below `scanRoot` that loads any given package but is not listed is reported as `UNLISTED` and makes the run exit 1.
- A consumer is skipped, without edits, when it has any uncommitted change or is not on its default branch. With `--push`, after a `git fetch`, it is also skipped when its default branch is ahead of, behind or diverged from `origin`, so the command never pushes someone else's work. A consumer already on every target is `CURRENT`.
- A project whose `AGENTS.md` contains `Collaborative: use branches and pull requests.` gets a pull request. `--apply` commits on a new branch `ci/shared-pins` cut from the default branch and switches back; `--push` pushes that branch and runs `gh pr create`. The command never merges.
- With `--apply`, after the pins are written and staged, it runs `./scripts/verify_commit.sh` once from the repository root and saves the output in `--log-dir` (default: a new temporary folder, printed). If it fails, every edit is undone and the consumer is `CHECKS FAIL`, or `NEEDS A VERSION BUMP` when the output says "bump the version"; the last lines and the log path are shown. A repository without the script is verified by its commit hooks only, with a note. The command takes no lock: a consumer's script queues its own heavy steps.
- The gate takes exactly one `--pin` (more is an error). It runs once, with `{tag}` replaced, and prints JSON: `{"<name>": {"decision": "go" | "hold", "reason": "..."}}`. A held consumer is not edited unless named with `--approve`. A gate that fails, prints bad JSON or leaves a consumer out holds it.
- Each commit runs the consumer's hooks. If one fails, every edit and earlier commit of the run is undone and the hook's output is shown. A consumer counts as updated only after commits that contain the new pins.

Exit code 0 means every consumer is current, updated (or would be, in a dry run) or a local-path reference, and no unlisted consumer exists; 1 means some consumer was skipped, held, blocked or failed its checks, or one is unlisted; 2 means the command could not run.

## Tests

`./scripts/verify_commit.sh` runs the suite and the canary. Fake secrets are built from pieces while the tests run, so no key-shaped text sits in the repository.

The canary run replaces every scanner with one that reports nothing and requires that the suite then fails for every check, which shows the tests would notice a checker that quietly passes.

## What it does not protect against

`git commit --no-verify` skips hooks. Public repositories run the same checks in CI as a backstop.

## License

MIT.
