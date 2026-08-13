from __future__ import annotations

import os
import re
import secrets
from typing import Mapping


_TOKEN_RE = re.compile(r"^[\x21-\x7e]{32,512}$")
_FORBIDDEN_SECRET_NAMES = (
    "API_SECRET",
    "STUDIO_ACCESS_TOKEN",
    "STUDIO_AUTOMATION_TOKEN",
    "GROK_QA_CONNECTOR_TOKEN",
    "GROK_QA_DISPATCH_TOKEN",
    "PUBLICATION_WORKER_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "TELEGRAM_REVIEW_BOT_TOKEN",
    "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN_SQUID",
    "TELEGRAM_BOT_TOKEN_YELLOW",
    "TELEGRAM_BOT_TOKEN_ORIGINTRAIL",
    "TELEGRAM_BOT_TOKEN_BABYLON",
)


def grok_qa_relay_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the dedicated Netlify-to-Railway QA relay credential."""

    env = os.environ if environ is None else environ
    value = env.get("GROK_QA_RELAY_TOKEN", "")
    if not _TOKEN_RE.fullmatch(value):
        raise ValueError("GROK_QA_RELAY_TOKEN has an invalid format")
    value_bytes = value.encode("ascii")
    for name in _FORBIDDEN_SECRET_NAMES:
        other = env.get(name, "").strip()
        if other and secrets.compare_digest(value_bytes, other.encode("utf-8")):
            raise ValueError("GROK_QA_RELAY_TOKEN must be a dedicated secret")
    return value


def has_grok_qa_relay_access(
    submitted: str,
    environ: Mapping[str, str] | None = None,
) -> bool:
    try:
        expected = grok_qa_relay_token(environ)
    except ValueError:
        return False
    if (
        not isinstance(submitted, str)
        or not submitted.isascii()
        or len(submitted) != len(expected)
    ):
        return False
    return secrets.compare_digest(submitted.encode("ascii"), expected.encode("ascii"))


__all__ = ["grok_qa_relay_token", "has_grok_qa_relay_access"]
