"""Errors and the finding record."""
from __future__ import annotations

from typing import NamedTuple, Optional


class CheckError(Exception):
    """The check could not run to a trustworthy answer. Always a failure, never a pass."""


class Finding(NamedTuple):
    check: str
    path: str
    line: int
    detail: str  # a rule name or a short reason; never the matched text
    rule: str = ""
    text: Optional[str] = None  # matched text; used for allowances, never printed
