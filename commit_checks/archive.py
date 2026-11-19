"""scan-archive: scan a release archive (tar, tgz or zip) with the text rules before it is published."""
from __future__ import annotations

import fnmatch
import os
import posixpath
import tarfile
import tempfile
import zipfile
from typing import List

from . import checks, engine, names
from .errors import CheckError, Finding
from .settings import Settings

MAX_BYTES = 512 * 1024 * 1024


def _safe(name: str) -> bool:
    norm = posixpath.normpath(name)
    return not (norm.startswith("..") or posixpath.isabs(norm))


def extract(archive: str, dest: str) -> List[str]:
    members: List[str] = []
    total = 0
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if not _safe(info.filename):
                    raise CheckError("unsafe path in archive: " + info.filename)
                total += info.file_size
                if total > MAX_BYTES:
                    raise CheckError("archive is larger than the scan limit")
                members.append(info.filename)
            zf.extractall(dest)
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            for m in tf.getmembers():
                if not _safe(m.name) or m.issym() or m.islnk():
                    if m.issym() or m.islnk():
                        members.append(m.name)
                        continue
                    raise CheckError("unsafe path in archive: " + m.name)
                total += m.size
                if total > MAX_BYTES:
                    raise CheckError("archive is larger than the scan limit")
                members.append(m.name)
            safe = [m for m in tf.getmembers() if _safe(m.name) and not (m.issym() or m.islnk())]
            tf.extractall(dest, members=safe)
    else:
        raise CheckError("not a tar or zip archive: " + archive)
    return members


def scan_archive(archive: str, settings: Settings) -> List[Finding]:
    if not os.path.isfile(archive):
        raise CheckError("archive not found: " + archive)
    with tempfile.TemporaryDirectory(prefix="commit-checks-archive-") as tmp:
        members = extract(archive, tmp)
        pn = names.load(required=settings.strict) if settings.strict else []
        found = engine.scan_dir(tmp, pn)
    out = list(found)
    for member in members:
        base = member.rstrip("/").rsplit("/", 1)[-1]
        if checks._secret_file_match(member):
            out.append(Finding("secret-files", member, 0, "a file name that usually holds a secret", "secret-files"))
        if base.endswith(".map"):
            out.append(Finding("source-maps", member, 0, "source map in a release archive", "source-maps"))
    return [f for f in out if not any(e["rule"] in (f.rule, f.check) and fnmatch.fnmatchcase(f.path, e["path"]) for e in settings.allow)]
