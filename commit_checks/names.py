"""The private-names list: kept outside every repository."""
from __future__ import annotations

import os
from typing import List

from .errors import CheckError

ENV_CONTENT = "COMMIT_CHECKS_PRIVATE_NAMES"  # the list itself (a CI secret)
ENV_FILE = "COMMIT_CHECKS_PRIVATE_NAMES_FILE"
DEFAULT_FILE = "~/.config/commit-checks/private-names"


def parse(text: str) -> List[str]:
    names = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            names.append(line)
    return names


def load(required: bool) -> List[str]:
    """One case-insensitive regular expression per line. Missing or empty fails when required."""
    content = os.environ.get(ENV_CONTENT, "")
    source = "the %s secret" % ENV_CONTENT
    if not content.strip():
        path = os.path.expanduser(os.environ.get(ENV_FILE) or DEFAULT_FILE)
        source = path
        content = ""
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    content = fh.read()
            except OSError as exc:
                raise CheckError("cannot read the private-names list %s: %s" % (path, exc))
    names = parse(content)
    if required and not names:
        raise CheckError(
            "the private-names list is missing or empty (%s); public repositories need it. "
            "On a Mac create ~/.config/commit-checks/private-names; in CI set the %s secret" % (source, ENV_CONTENT)
        )
    for name in names:
        if "'''" in name or "\0" in name:
            raise CheckError("a private-names line holds characters the scanner cannot take")
    return names
