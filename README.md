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
| author addresses | Commit authors and committers that are not the GitHub noreply address (public repositories) |
| visibility | The settings file disagrees with what GitHub says (CI only) |
| links | Relative Markdown links that do not point at a tracked file or folder, with exact case |
| decision records | `docs/adr/` records and their index that break the format (title, status line, four headings, no em dash) |
| house style | Vale alerts, with the pinned style package synced when its pin changes |
| versions | A CHANGELOG whose top release differs from the version source; code changed under a released tag |
| release tags | Lightweight release tags |
| commit messages | Subjects that are not Conventional Commits |

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

- `release`: `{"source": {"type": "package-json|xcode|pyproject|plist|regex", "path": "...", "pattern": "..."}, "changelog": "CHANGELOG.md", "code": ["*.swift"]}` turns on the version checks. `code` lists the file patterns that count as code under a released tag (default: every file).
- `links_exclude`: path patterns the link check skips.
- `adr_exempt`: record numbers that keep an old format and skip the heading check.
- `vale`: `{"globs": ["*.md"], "exclude": []}` picks the files Vale checks. The check runs only when the repository has a `.vale.ini`. A repository with a `CONTRIBUTING.md` gets a notice instead of a failure when Vale is missing locally; CI always fails.
- `annotated_tags_from`: a version such as `0.1.10`; release tags below it may stay lightweight.
- `conventional_commits`: `false` turns the subject check off. `subject_max`: a number limits the subject length.

### Private names

One regular expression per line, case-insensitive, `#` for comments. The list lives outside every repository: `~/.config/commit-checks/private-names` on a developer machine, or the `COMMIT_CHECKS_PRIVATE_NAMES` secret in CI. A public or going-public repository fails when the list is missing or empty.

## Commands

`scripts/commit-checks MODE`, where MODE is `pre-commit`, `commit-msg FILE`, `pre-push`, `verify`, `ci`, `scan-archive FILE...` or `history`. `scan-archive` checks a tarball or zip before it is published, and also flags source maps and secret file names. `history` scans every commit, message, author address and tag; run it before a repository becomes public. Exit code 0 is a pass, 1 means findings, 2 means the check could not run.

## Tests

`./scripts/verify_commit.sh` runs the suite and the canary. Fake secrets are built from pieces while the tests run, so no key-shaped text sits in the repository.

The canary run replaces every scanner with one that reports nothing and requires that the suite then fails for every check, which shows the tests would notice a checker that quietly passes.

## What it does not protect against

`git commit --no-verify` skips hooks. Public repositories run the same checks in CI as a backstop.

## License

MIT.
