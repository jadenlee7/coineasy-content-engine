"""Client-facing name normalization for generated content."""

from __future__ import annotations

import re
from typing import Any


_SQUID_LEGACY_NAME = re.compile(
    r"(?<![A-Za-z0-9_])Squid\s+Router(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_SQUID_PARTICLE = re.compile(
    r"(?<![A-Za-z0-9_@#])Squid(으로|이|은|을|과)",
)
_SQUID_PARTICLE_REPLACEMENTS = {
    "으로": "로",
    "이": "가",
    "은": "는",
    "을": "를",
    "과": "와",
}


def _normalize_squid_text(value: str) -> str:
    normalized = _SQUID_LEGACY_NAME.sub("Squid", value)
    return _SQUID_PARTICLE.sub(
        lambda match: f"Squid{_SQUID_PARTICLE_REPLACEMENTS[match.group(1)]}",
        normalized,
    )


def enforce_client_display_name(client_id: str, value: Any) -> Any:
    """Recursively enforce the approved display name without touching handles or URLs."""
    if client_id != "squid":
        return value
    if isinstance(value, str):
        return _normalize_squid_text(value)
    if isinstance(value, list):
        return [enforce_client_display_name(client_id, item) for item in value]
    if isinstance(value, dict):
        return {
            key: enforce_client_display_name(client_id, item)
            for key, item in value.items()
        }
    return value
