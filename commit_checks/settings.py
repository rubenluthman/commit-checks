"""Per-repository settings: .commit-checks.json at the repository root."""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from .errors import CheckError

SETTINGS_FILE = ".commit-checks.json"
VISIBILITIES = ("private", "public", "going-public")
KNOWN_KEYS = {"version", "commit", "source", "visibility", "email_allow", "allow", "self", "release", "links_exclude", "adr_exempt", "vale", "conventional_commits"}


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
    return Settings(data)
