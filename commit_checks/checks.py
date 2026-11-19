"""The checks. Each takes a Context and returns findings; an error raises CheckError.

The registry maps a check name to a function so that tests (and the canary run) can replace
every check with one that reports nothing and prove the suite then fails.
"""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from typing import Callable, Dict, List, Optional

from . import engine, gitutil, names, rules
from .errors import CheckError, Finding
from .settings import Settings

SECRET_FILE_GLOBS = [
    ".env", ".env.*", ".dev.vars", ".dev.vars.*", "*.p8", "*.p12", "*.pfx", "*.key", "*.pem", "*.mobileprovision",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "GoogleService-Info.plist",
]
SECRET_FILE_OK = ["*.example", "*.sample", "*.template", "*.pub"]
GITIGNORE_SAMPLES = [".env", ".env.local", ".dev.vars", ".dev.vars.production", "x.p8", "x.p12", "x.key", "x.pem",
                     "x.mobileprovision", "id_ed25519", "id_rsa"]
NOREPLY = re.compile(r"@users\.noreply\.github\.com$")


class Context:
    """One run: the repository, its settings, and where the text to scan comes from."""

    def __init__(self, root: str, settings: Settings, source: str = "snapshot", message: Optional[str] = None,
                 revspec: Optional[str] = None, commits: Optional[List[str]] = None):
        self.root = root
        self.settings = settings
        self.source = source  # staged | snapshot | message | range
        self.message = message
        self.revspec = revspec
        self.commits = commits or []
        self._text: Optional[List[Finding]] = None

    def private_names(self) -> List[str]:
        return names.load(required=self.settings.strict)

    def text_findings(self) -> List[Finding]:
        """Run the text scan once; the four text checks each take their share."""
        if self._text is None:
            pn = self.private_names()
            found = []  # type: List[Finding]
            if self.source == "staged":
                found = engine.scan_staged(self.root, pn)
            elif self.source == "message":
                found = engine.scan_text("commit message", self.message or "", pn)
            elif self.source == "range":
                found = engine.scan_range(self.root, self.revspec or "", pn)
                if self.message:
                    found += engine.scan_text("commit messages", self.message, pn)
            elif self.source == "snapshot":
                import tempfile
                with tempfile.TemporaryDirectory(prefix="commit-checks-snap-") as snap:
                    gitutil.export_index(self.root, snap)
                    found = engine.scan_dir(snap, pn)
            else:
                raise CheckError("unknown scan source " + self.source)
            self._text = [f for f in found if not self.allowed(f)]
        return self._text

    def allowed(self, finding: Finding) -> bool:
        for entry in self.settings.allow:
            rule_ok = entry["rule"] in (finding.rule, finding.check)
            if rule_ok and fnmatch.fnmatchcase(finding.path, entry["path"]):
                return True
        return False


def _share(ctx: Context, category: str) -> List[Finding]:
    return [f for f in ctx.text_findings() if f.check == category]


def check_secrets(ctx: Context) -> List[Finding]:
    return _share(ctx, "secrets")


def check_home_paths(ctx: Context) -> List[Finding]:
    return _share(ctx, "home-paths")


def check_emails(ctx: Context) -> List[Finding]:
    return _share(ctx, "emails")


def check_private_names(ctx: Context) -> List[Finding]:
    return _share(ctx, "private-names")


def _secret_file_match(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    if any(fnmatch.fnmatchcase(base, p) for p in SECRET_FILE_OK):
        return False
    return any(fnmatch.fnmatchcase(base, p) for p in SECRET_FILE_GLOBS)


def check_secret_files(ctx: Context) -> List[Finding]:
    paths = gitutil.staged_added_paths(ctx.root) if ctx.source == "staged" else gitutil.index_paths(ctx.root)
    out = []
    for p in paths:
        if _secret_file_match(p):
            f = Finding("secret-files", p, 0, "a file name that usually holds a secret", "secret-files")
            if not ctx.allowed(f):
                out.append(f)
    return out


MARKER = r"^(<{7}|>{7})( |$)"


def check_conflict_markers(ctx: Context) -> List[Finding]:
    proc = gitutil.run(["grep", "--cached", "-I", "-l", "-z", "-E", "-e", MARKER], ctx.root, ok=(0, 1))
    if proc.returncode == 1:
        return []
    out = [Finding("conflict-markers", p, 0, "leftover merge marker", "conflict-markers") for p in gitutil.split_z(proc.stdout)]
    return [f for f in out if not ctx.allowed(f)]


def check_gitignore_baseline(ctx: Context) -> List[Finding]:
    out = []
    for sample in GITIGNORE_SAMPLES:
        proc = subprocess.run(
            ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "-q", "--no-index", sample],
            cwd=ctx.root, capture_output=True, env=gitutil.clean_env(), timeout=60,
        )
        if proc.returncode == 1:
            out.append(Finding("gitignore-baseline", ".gitignore", 0, "does not ignore " + sample, "gitignore-baseline"))
        elif proc.returncode != 0:
            raise CheckError("git check-ignore failed for " + sample)
    return [f for f in out if not ctx.allowed(f)]


def check_hooks_installed(ctx: Context) -> List[Finding]:
    if os.environ.get("CI"):
        return []
    proc = gitutil.run(["config", "--get", "core.hooksPath"], ctx.root, ok=(0, 1))
    value = proc.stdout.decode("utf-8", "replace").strip()
    problems = []
    if value != ".githooks":
        problems.append("core.hooksPath is not .githooks (run scripts/install-hooks.sh)")
    for hook in ("pre-commit", "commit-msg", "pre-push"):
        path = os.path.join(ctx.root, ".githooks", hook)
        if not os.path.isfile(path):
            problems.append(".githooks/%s is missing" % hook)
        elif not os.access(path, os.X_OK):
            problems.append(".githooks/%s is not executable" % hook)
        else:
            with open(path, encoding="utf-8", errors="replace") as fh:
                if "commit-checks" not in fh.read():
                    problems.append(".githooks/%s does not call commit-checks" % hook)
    return [Finding("hooks-installed", ".githooks", 0, p, "hooks-installed") for p in problems]


def _addresses(text: str) -> List[str]:
    return [m for m in re.findall(r"<([^<>]*)>", text)]


def _address_ok(address: str, ctx: Context) -> bool:
    if NOREPLY.search(address):
        return True
    return any(re.search(p, address) for p in ctx.settings.email_allow)


def check_author_emails(ctx: Context) -> List[Finding]:
    """In public and going-public repositories, authors and committers use the GitHub noreply address."""
    if not ctx.settings.strict:
        return []
    found = []
    if ctx.source == "range":
        proc = gitutil.run(["log", "--format=%h%x09%ae%x09%ce"] + (ctx.revspec or "").split(), ctx.root)
        for row in proc.stdout.decode("utf-8", "replace").splitlines():
            parts = row.split("\t")
            if len(parts) != 3:
                raise CheckError("unexpected git log line")
            for addr in parts[1:]:
                if not _address_ok(addr, ctx):
                    found.append(Finding("author-emails", "commit " + parts[0], 0, "address is not the noreply address", "author-emails"))
                    break
    else:
        for var in ("GIT_AUTHOR_IDENT", "GIT_COMMITTER_IDENT"):
            ident = gitutil.run(["var", var], ctx.root).stdout.decode("utf-8", "replace")
            addrs = _addresses(ident)
            if not addrs:
                raise CheckError("git has no %s address" % var.split("_")[1].lower())
            if not all(_address_ok(a, ctx) for a in addrs):
                found.append(Finding("author-emails", var, 0, "address is not the noreply address", "author-emails"))
    return found


def check_loader_current(ctx: Context) -> List[Finding]:
    """The repository's loader must be the pinned release's template, byte for byte."""
    if ctx.settings.is_self:
        return []
    template = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates", "loader.py")
    mine = os.path.join(ctx.root, "scripts", "commit-checks")
    try:
        with open(template, "rb") as fh:
            want = fh.read()
    except OSError as exc:
        raise CheckError("cannot read the loader template: %s" % exc)
    try:
        with open(mine, "rb") as fh:
            have = fh.read()
    except OSError:
        return [Finding("loader-current", "scripts/commit-checks", 0, "the loader is missing", "loader-current")]
    if have != want:
        return [Finding("loader-current", "scripts/commit-checks", 0, "differs from the pinned release's template", "loader-current")]
    return []


def check_visibility(ctx: Context) -> List[Finding]:
    """In GitHub Actions: the settings' visibility must agree with what GitHub says."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return []
    import json
    import urllib.error
    import urllib.request
    base = os.environ.get("COMMIT_CHECKS_API_URL", "https://api.github.com").rstrip("/")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "commit-checks"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/repos/" + repo, headers=headers), timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        private = data["private"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError) as exc:
        raise CheckError("cannot ask GitHub whether %s is public: %s" % (repo, exc))
    if not isinstance(private, bool):
        raise CheckError("GitHub gave an unexpected answer about visibility")
    want = ("private", "going-public") if private else ("public",)
    if ctx.settings.visibility not in want:
        state = "private" if private else "public"
        return [Finding("visibility", ".commit-checks.json", 0,
                        "GitHub says the repository is %s but settings say %s" % (state, ctx.settings.visibility), "visibility")]
    return []


CHECKS: Dict[str, Callable[[Context], List[Finding]]] = {
    "secrets": check_secrets,
    "home-paths": check_home_paths,
    "emails": check_emails,
    "private-names": check_private_names,
    "secret-files": check_secret_files,
    "conflict-markers": check_conflict_markers,
    "gitignore-baseline": check_gitignore_baseline,
    "hooks-installed": check_hooks_installed,
    "author-emails": check_author_emails,
    "loader-current": check_loader_current,
    "visibility": check_visibility,
}

TEXT = ["secrets", "home-paths", "emails", "private-names"]
MODES: Dict[str, List[str]] = {
    "pre-commit": TEXT + ["secret-files", "conflict-markers", "gitignore-baseline", "author-emails"],
    "commit-msg": TEXT,
    "pre-push": TEXT + ["author-emails"],
    "verify": TEXT + ["secret-files", "conflict-markers", "gitignore-baseline", "hooks-installed", "loader-current"],
    "ci": TEXT + ["secret-files", "conflict-markers", "gitignore-baseline", "loader-current", "visibility"],
}


def run_mode(mode: str, ctx: Context) -> List[Finding]:
    findings: List[Finding] = []
    for name in MODES[mode]:
        findings += CHECKS[name](ctx)
    return findings
