"""Pin types for `roll-out`: how one kind of package reference is found and moved to a release.

A pin type answers three questions: does this repository load the package (`detect`), which release
is the target and is it really published (`resolve_target`), and what would change in this
repository (`inspect`). `inspect` edits nothing: it returns a `Pin` that holds the new content of
every file it wants to change. A pin type that cannot describe its edit as plain file content (npm,
for example) returns a `Pin` subclass that overrides `write` and `committed`.

To add a pin type, subclass `PinType`, set `name`, and register an instance in `PIN_TYPES`.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from . import gitutil
from .errors import CheckError

TAG = re.compile(r"^v\d+\.\d+\.\d+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
LOADER_TEMPLATE = "templates/loader.py"
LOADER_FILE = "scripts/commit-checks"
TEMPLATE_PATHS = ["templates/githooks", "templates/install-hooks.sh", "templates/workflows"]


class Target:
    """The release every consumer moves to."""

    def __init__(self, package: str, tag: str, commit: str, repo: str):
        self.package = package
        self.tag = tag
        self.commit = commit
        self.repo = repo  # a local git repository that holds the release

    def read(self, path: str) -> bytes:
        return gitutil.run(["show", "%s:%s" % (self.commit, path)], self.repo).stdout

    def changed(self, old_commit: str, paths: List[str]) -> Optional[List[str]]:
        """Files under `paths` that differ between an older release and this one; None when the older one is unknown."""
        if not SHA.match(old_commit or ""):
            return None
        known = gitutil.run(["cat-file", "-e", old_commit + "^{commit}"], self.repo, ok=(0, 1, 128))
        if known.returncode != 0:
            return None
        out = gitutil.run(["diff", "--name-only", "-z", old_commit, self.commit, "--"] + paths, self.repo).stdout
        return gitutil.split_z(out)


class Pin:
    """What one repository needs. `status` is "current", "update", "local" (a local-path reference) or "manual" (not an exact version): those two are noted, never edited. "blocked" is an update that cannot be made, with `detail` saying why."""

    def __init__(self, status: str, current: str = "", files: Optional[Dict[str, bytes]] = None, notes: Optional[List[str]] = None):
        self.status = status
        self.current = current
        self.files: Dict[str, bytes] = files or {}
        self.notes: List[str] = notes or []
        self.detail = ""  # why a "blocked" pin cannot be applied

    def write(self, root: str) -> None:
        for rel, data in sorted(self.files.items()):
            with open(os.path.join(root, rel), "wb") as fh:
                fh.write(data)

    def committed(self, root: str, revision: str) -> bool:
        """True when `revision` holds the new pin. Called after the commit; a False makes the consumer blocked."""
        for rel, data in self.files.items():
            proc = gitutil.run(["show", "%s:%s" % (revision, rel)], root, ok=(0, 128))
            if proc.returncode != 0 or proc.stdout != data:
                return False
        return True


class Reading:
    """What one repository holds of one package: every pin form found, and the problems worth showing.

    A form is a dict: kind, file, requirement ({type, version, max} or None), resolved ({file, version,
    revision, branch} or None) and editable (whether `roll-out` can edit every file of it). Keys that start
    with an underscore are for the reader and never reach a report.
    """

    def __init__(self, forms: Optional[List[dict]] = None, problems: Optional[List[str]] = None):
        self.forms: List[dict] = [] if forms is None else forms
        self.problems: List[str] = [] if problems is None else problems

    @property
    def version(self) -> Optional[str]:
        """The one version every form agrees on; None when there is none or the forms differ."""
        seen = set()
        for form in self.forms:
            resolved, req = form.get("resolved"), form.get("requirement")
            version = (resolved or {}).get("version") or (req["version"] if req and req.get("type") == "exact" else None)
            if version:
                seen.add(version)
        return seen.pop() if len(seen) == 1 else None

    def public_forms(self) -> List[dict]:
        return [{k: v for k, v in f.items() if not k.startswith("_")} for f in self.forms]


def form(kind: str, file: str, requirement: Optional[dict] = None, resolved: Optional[dict] = None,
         editable: bool = False, **private) -> dict:
    out = {"kind": kind, "file": file, "requirement": requirement, "resolved": resolved, "editable": editable}
    out.update(private)
    return out


class PinType:
    name = ""

    def subject(self, tag: str) -> str:
        """The commit subject for moving this pin to `tag`."""
        raise NotImplementedError

    def read(self, root: str) -> Reading:
        """Every form in which `root` pins this package. Edits nothing, reads only what Git tracks."""
        raise NotImplementedError

    def detect(self, root: str) -> bool:
        return bool(self.read(root).forms)

    def resolve_target(self, tag: str) -> Target:
        """Return the verified release, or raise CheckError (the command then edits nothing)."""
        raise NotImplementedError

    def inspect(self, root: str, target: Target) -> Pin:
        raise NotImplementedError


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        raise CheckError("cannot read %s: %s" % (os.path.basename(path), exc))


def _swap_value(text: str, key: str, value: str) -> str:
    """Replace the string value of a top-level key without touching the rest of the file's formatting."""
    pattern = re.compile(r'("%s"\s*:\s*)"[^"\\]*"' % re.escape(key))
    if len(pattern.findall(text)) != 1:
        raise CheckError('.commit-checks.json: expected exactly one "%s" entry' % key)
    return pattern.sub(lambda m: m.group(1) + json.dumps(value), text)


def _local_clone(env_var: str, config_key: str, cwd: str, fallback: str = "") -> str:
    candidates = [os.environ.get(env_var, "")]
    proc = gitutil.run(["config", "--global", "--get", config_key], cwd, ok=(0, 1))
    candidates.append(proc.stdout.decode("utf-8", "replace").strip())
    candidates.append(fallback)
    for path in candidates:
        if path and os.path.isdir(path):
            return os.path.realpath(path)
    raise CheckError("no local package clone: set %s or git config --global %s" % (env_var, config_key))


def _verified_tag(repo: str, tag: str) -> str:
    """The commit a release tag points at, once the clone and its origin agree on it. Any doubt raises CheckError."""
    if not TAG.match(tag):
        raise CheckError("a release tag must look like v1.2.3")
    local = gitutil.run(["rev-parse", "-q", "--verify", "refs/tags/%s^{commit}" % tag], repo, ok=(0, 1))
    commit = local.stdout.decode().strip()
    if local.returncode != 0 or not SHA.match(commit):
        raise CheckError("tag %s is not in the local package clone" % tag)
    origin = gitutil.run(["remote", "get-url", "origin"], repo, ok=(0, 2, 128))
    url = origin.stdout.decode("utf-8", "replace").strip()
    if origin.returncode != 0 or not url:
        raise CheckError("the package clone has no origin remote to check the tag against")
    listing = gitutil.run(["ls-remote", url, "refs/tags/" + tag, "refs/tags/%s^{}" % tag], repo, timeout=300)
    refs = {}
    for line in listing.stdout.decode("utf-8", "replace").splitlines():
        sha, _sep, ref = line.partition("\t")
        refs[ref.strip()] = sha.strip()
    remote = refs.get("refs/tags/%s^{}" % tag) or refs.get("refs/tags/" + tag)
    if not remote:
        raise CheckError("tag %s is not on the package's remote; push it first" % tag)
    if remote != commit:
        raise CheckError("tag %s points at %s on the remote but at %s locally" % (tag, remote, commit))
    return commit


class CommitChecksPin(PinType):
    name = "commit-checks"
    SETTINGS = ".commit-checks.json"

    def read(self, root: str) -> Reading:
        path = os.path.join(root, self.SETTINGS)
        if not os.path.isfile(path):
            return Reading()
        try:
            data = _read_json(path)
        except CheckError:
            return Reading()
        if not (isinstance(data, dict) and "version" in data and "commit" in data):
            return Reading()
        tag, commit = data.get("version"), data.get("commit")
        resolved = {"file": self.SETTINGS, "version": tag if isinstance(tag, str) else None,
                    "revision": commit if isinstance(commit, str) else None, "branch": None}
        req = {"type": "exact", "version": resolved["version"]}
        return Reading([form("settings", self.SETTINGS, req, resolved, True)])

    def subject(self, tag: str) -> str:
        return "ci: load the shared commit checks at " + tag

    def package_repo(self) -> str:
        """The local clone that holds the package: COMMIT_CHECKS_PATH, the global git setting, or this source tree."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return _local_clone("COMMIT_CHECKS_PATH", "commit-checks.path", here, here if os.path.exists(os.path.join(here, ".git")) else "")

    def resolve_target(self, tag: str) -> Target:
        repo = self.package_repo()
        return Target(self.name, tag, _verified_tag(repo, tag), repo)

    def inspect(self, root: str, target: Target) -> Pin:
        settings_path = os.path.join(root, self.SETTINGS)
        data = _read_json(settings_path)
        if not isinstance(data, dict) or not isinstance(data.get("version"), str) or not isinstance(data.get("commit"), str):
            raise CheckError(".commit-checks.json has no version and commit to move")
        old_tag, old_commit = data["version"], data["commit"]
        files: Dict[str, bytes] = {}
        notes: List[str] = []
        if (old_tag, old_commit) != (target.tag, target.commit):
            with open(settings_path, encoding="utf-8", newline="") as fh:
                text = fh.read()
            text = _swap_value(_swap_value(text, "version", target.tag), "commit", target.commit)
            after = json.loads(text)
            if after.get("version") != target.tag or after.get("commit") != target.commit:
                raise CheckError(".commit-checks.json edit did not produce the new pin")
            files[self.SETTINGS] = text.encode("utf-8")
        loader_path = os.path.join(root, LOADER_FILE)
        wanted = target.read(LOADER_TEMPLATE)
        if os.path.isfile(loader_path):
            with open(loader_path, "rb") as fh:
                if fh.read() != wanted:
                    files[LOADER_FILE] = wanted
        else:
            notes.append("%s is missing; not created" % LOADER_FILE)
        changed = target.changed(old_commit, TEMPLATE_PATHS)
        if changed is None:
            notes.append("old release %s is not in the package clone; could not compare templates" % old_tag)
        elif changed:
            notes.append("templates changed since %s; copy by hand: %s" % (old_tag, ", ".join(changed)))
        return Pin("update" if files else "current", old_tag, files, notes)


HOUSE_LINE = re.compile(
    r"^(?P<head>Packages[ \t]*=[^\r\n]*?https://github\.com/[^/\s,]+/house-style/releases/download/)"
    r"(?P<tag>v\d+\.\d+\.\d+)(?P<tail>/HouseStyle\.zip[^\r\n]*)$",
    re.M,
)


class HouseStylePin(PinType):
    """The Vale style package, pinned by URL in the `Packages` line of `.vale.ini`."""

    name = "house-style"
    FILE = ".vale.ini"

    def subject(self, tag: str) -> str:
        return "ci: load the house style at " + tag

    def _text(self, root: str) -> Optional[str]:
        path = os.path.join(root, self.FILE)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()

    def read(self, root: str) -> Reading:
        text = self._text(root)
        found = HOUSE_LINE.search(text) if text is not None else None
        if not found:
            return Reading()
        return Reading([form("vale", self.FILE, {"type": "exact", "version": found.group("tag")}, None, True)])

    def resolve_target(self, tag: str) -> Target:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        repo = _local_clone("HOUSE_STYLE_PATH", "house-style.path", here)
        return Target(self.name, tag, _verified_tag(repo, tag), repo)

    def inspect(self, root: str, target: Target) -> Pin:
        text = self._text(root)
        found = list(HOUSE_LINE.finditer(text or ""))
        if len(found) != 1:
            raise CheckError(".vale.ini: expected exactly one house-style Packages line")
        old = found[0].group("tag")
        if old == target.tag:
            return Pin("current", old)
        new = HOUSE_LINE.sub(lambda m: m.group("head") + target.tag + m.group("tail"), text)
        if [m.group("tag") for m in HOUSE_LINE.finditer(new)] != [target.tag]:
            raise CheckError(".vale.ini edit did not produce the new pin")
        return Pin("update", old, {self.FILE: new.encode("utf-8")})


PIN_TYPES: Dict[str, PinType] = {p.name: p for p in (CommitChecksPin(), HouseStylePin())}


from .pins_judarn import JUDARN_PIN_TYPES  # noqa: E402  (the module imports this one)

PIN_TYPES.update({p.name: p for p in JUDARN_PIN_TYPES})
