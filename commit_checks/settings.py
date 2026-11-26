"""Per-repository settings: .commit-checks.json at the repository root."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .errors import CheckError

SETTINGS_FILE = ".commit-checks.json"
VISIBILITIES = ("private", "public", "going-public")
KNOWN_KEYS = {"version", "commit", "source", "visibility", "email_allow", "allow", "self", "release", "links_exclude", "adr_exempt", "vale", "conventional_commits", "subject_max", "annotated_tags_from", "settings_keys", "docs_only", "binary_allow"}


class Settings:
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.visibility: str = data["visibility"]
        self.email_allow: List[str] = list(data.get("email_allow", []))
        self.allow: List[Dict[str, str]] = list(data.get("allow", []))
        self.is_self: bool = bool(data.get("self", False))

    @property
    def strict(self) -> bool:
        """Public and going-public repositories get the author-address and private-name rules."""
        return self.visibility in ("public", "going-public")


def load(root: str) -> Settings:
    path = os.path.join(root, SETTINGS_FILE)
    if not os.path.isfile(path):
        raise CheckError("missing %s (see the commit-checks README)" % SETTINGS_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CheckError("cannot read %s: %s" % (SETTINGS_FILE, exc))
    if not isinstance(data, dict):
        raise CheckError("%s must hold one JSON object" % SETTINGS_FILE)
    unknown = set(data) - KNOWN_KEYS
    if unknown:
        raise CheckError("unknown settings: " + ", ".join(sorted(unknown)))
    if data.get("visibility") not in VISIBILITIES:
        raise CheckError("settings: visibility must be one of " + ", ".join(VISIBILITIES))
    for key in ("email_allow", "allow"):
        if key in data and not isinstance(data[key], list):
            raise CheckError("settings: %s must be a list" % key)
    for entry in data.get("allow", []):
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) and entry.get(k) for k in ("rule", "path", "reason")):
            raise CheckError("settings: every allow entry needs rule, path and a written reason")
    if "settings_keys" in data:
        _validate_settings_keys(data["settings_keys"])
    if "binary_allow" in data:
        v = data["binary_allow"]
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            raise CheckError("settings: binary_allow must be a list of non-empty path globs")
    if "docs_only" in data:
        _validate_docs_only(data["docs_only"])
    return Settings(data)


def _validate_docs_only(conf: Any) -> None:
    if not isinstance(conf, dict):
        raise CheckError("settings: docs_only must be an object")
    unknown = set(conf) - {"paths", "except"}
    if unknown:
        raise CheckError("settings: unknown docs_only entries: " + ", ".join(sorted(unknown)))
    for name, required in (("paths", True), ("except", False)):
        if name not in conf and not required:
            continue
        v = conf.get(name)
        if not isinstance(v, list) or (required and not v) or not all(isinstance(x, str) and x for x in v):
            raise CheckError("settings: docs_only.%s must be a %slist of non-empty strings" % (name, "non-empty " if required else ""))
    if "*" in conf["paths"]:
        raise CheckError("settings: docs_only.paths must not hold a bare *, which would make every file docs")


def _strings(value: Any, label: str, required: bool = False) -> None:
    if not isinstance(value, list) or (required and not value) or not all(isinstance(v, str) and v for v in value):
        raise CheckError("settings: settings_keys.%s must be a %slist of non-empty strings" % (label, "non-empty " if required else ""))


def _validate_settings_keys(conf: Any) -> None:
    if not isinstance(conf, dict):
        raise CheckError("settings: settings_keys must be an object")
    unknown = set(conf) - {"paths", "live", "retired", "system"}
    if unknown:
        raise CheckError("settings: unknown settings_keys entries: " + ", ".join(sorted(unknown)))
    _strings(conf.get("paths"), "paths", required=True)
    for name in ("live", "system"):
        _strings(conf.get(name, []), name)
    retired = conf.get("retired", [])
    if not isinstance(retired, list):
        raise CheckError("settings: settings_keys.retired must be a list")
    for entry in retired:
        if not isinstance(entry, dict) or set(entry) != {"key", "migration", "reason"} \
                or not all(isinstance(v, str) and v for v in entry.values()):
            raise CheckError("settings: every settings_keys.retired entry needs exactly key, migration and a written reason")
