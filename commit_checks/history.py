"""history: scan the whole history of a repository before it becomes public."""
from __future__ import annotations

import re
from typing import List

from . import checks, engine, gitutil, names
from .errors import CheckError, Finding

NOREPLY = re.compile(r"@users\.noreply\.github\.com$")


def scan_history(ctx) -> List[Finding]:
    """Every commit's changes, message, author and committer address, and every tag message. Always strict."""
    pn = names.load(required=True)
    out: List[Finding] = []
    out += engine.scan_range(ctx.root, "--all", pn)
    log = gitutil.run(["log", "--all", "--format=%H%n%an <%ae>%n%cn <%ce>%n%B%n----"], ctx.root).stdout.decode("utf-8", "surrogateescape")
    tags = gitutil.run(["for-each-ref", "--format=%(refname)%n%(contents)%n%(taggername) %(taggeremail)%n----", "refs/tags"], ctx.root).stdout.decode("utf-8", "surrogateescape")
    out += engine.scan_text("commit messages", log, pn)
    out += engine.scan_text("tag messages", tags, pn)
    rows = gitutil.run(["log", "--all", "--format=%h%x09%ae%x09%ce"], ctx.root).stdout.decode("utf-8", "replace").splitlines()
    for row in rows:
        parts = row.split("\t")
        if len(parts) != 3:
            raise CheckError("unexpected git log line")
        for addr in parts[1:]:
            if not NOREPLY.search(addr) and not any(re.search(p, addr) for p in ctx.settings.email_allow):
                out.append(Finding("author-emails", "commit " + parts[0], 0, "address is not the noreply address", "author-emails"))
                break
    tagger = gitutil.run(["for-each-ref", "--format=%(taggeremail)", "refs/tags"], ctx.root).stdout.decode("utf-8", "replace").splitlines()
    for addr in tagger:
        addr = addr.strip("<>")
        if addr and not NOREPLY.search(addr) and not any(re.search(p, addr) for p in ctx.settings.email_allow):
            out.append(Finding("author-emails", "tag", 0, "tagger address is not the noreply address", "author-emails"))
            break
    return [f for f in out if not ctx.allowed(f)]
