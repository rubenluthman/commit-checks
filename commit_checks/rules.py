"""The scanner configuration: gitleaks' default rules plus the package rules."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

# Brackets keep a pattern from matching its own source text.
SHAPE_RULES: List[Tuple[str, str, str]] = [
    ("cc-openai-key", "OpenAI-style key", r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{20,}"),
    ("cc-openai-legacy-key", "legacy secret key", r"\bsk-[A-Za-z0-9]{32,}"),
    ("cc-anthropic-key", "Anthropic key", r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    ("cc-github-token", "GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    ("cc-github-pat", "GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{22,}"),
    ("cc-aws-key", "AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("cc-stripe-live-key", "Stripe live key", r"\b[sr]k_live_[A-Za-z0-9]{16,}"),
    ("cc-google-key", "Google API key", r"\bAIza[0-9A-Za-z_-]{35}"),
    ("cc-slack-token", "Slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("cc-npm-token", "npm token", r"\bnpm_[A-Za-z0-9]{36}"),
    ("cc-private-key", "private key header", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
]
HOME_PATH = ("cc-home-path", "home-folder path", r"(?:/Use[r]s/|/hom[e]/)[^\s/'\"`<>()\[\]]+/|~/Develope[r]\b")
EMAIL = ("cc-email", "email address", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PRIVATE_NAME = "cc-private-name"

CATEGORY_BY_RULE = {HOME_PATH[0]: "home-paths", EMAIL[0]: "emails", PRIVATE_NAME: "private-names"}

# Matches that are never a finding. Python regular expressions, tested against the matched text.
BUILTIN_ALLOW: Dict[str, List[str]] = {
    "cc-home-path": [r"^/(?:Users|home)/(?:runner|Shared|Guest|name|username|user|you|me|example|vscode|node)/$"],
    "cc-email": [
        r"@users\.noreply\.github\.com$",
        r"^noreply@",
        r"@example\.(?:com|org|net|test)$",
        r"^git@(?:github|gitlab|bitbucket)\.(?:com|org)$",
        r"\.(?:png|jpe?g|gif|svg|webp|heic|pdf|css|js)$",
    ],
}


def category(rule_id: str) -> str:
    return CATEGORY_BY_RULE.get(rule_id, "secrets")


def _q(text: str) -> str:
    return json.dumps(text)


def build_config(private_names: Optional[List[str]], profile: str) -> str:
    """profile 'default': gitleaks' own rules. profile 'package': this package's rules only.

    They run as two passes because gitleaks' default rules carry a stopword list that drops any
    finding whose text contains a common word such as "home"; extending them would hide home paths.
    """
    if profile == "default":
        return 'title = "commit-checks default rules"\n\n[extend]\nuseDefault = true\n'
    out = ['title = "commit-checks package rules"', ""]
    rules = [(i, d, r, []) for i, d, r in SHAPE_RULES]
    rules.append((HOME_PATH[0], HOME_PATH[1], HOME_PATH[2], ["/users/", "/home/", "~/develop"]))
    rules.append((EMAIL[0], EMAIL[1], EMAIL[2], ["@"]))
    if private_names:
        rules.append((PRIVATE_NAME, "private name", "(?i)(?:" + "|".join("(?:%s)" % n for n in private_names) + ")", []))
    for rule_id, desc, regex, keywords in rules:
        out += ["[[rules]]", "id = " + _q(rule_id), "description = " + _q(desc), "regex = " + _q(regex)]
        if keywords:
            out.append("keywords = " + json.dumps(keywords))
        out.append("")
    return "\n".join(out)


def builtin_allowed(rule_id: str, text: str) -> bool:
    return any(re.search(p, text or "") for p in BUILTIN_ALLOW.get(rule_id, []))
