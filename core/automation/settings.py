from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit


AUTOMATION_CLIENTS = ("yellow", "origintrail", "squid", "babylon")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})


def _required(env: Mapping[str, str], name: str, *, min_length: int = 1) -> str:
    value = env.get(name, "").strip()
    if len(value) < min_length:
        raise ValueError(f"{name} is required")
    return value


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name, "true" if default else "false").strip().lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError(f"{name} must be a boolean")


def _supabase_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
    except ValueError as exc:
        raise ValueError("SUPABASE_URL is invalid") from exc
    host = (parsed.hostname or "").lower()
    is_local = host in {"localhost", "127.0.0.1"}
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not host
        or (not host.endswith(".supabase.co") and not is_local)
        or (parsed.scheme != "https" and not (is_local and parsed.scheme == "http"))
    ):
        raise ValueError("SUPABASE_URL is outside the automation allowlist")
    return normalized


@dataclass(frozen=True)
class AutomationSettings:
    supabase_url: str
    supabase_service_role_key: str
    workspace_id: str
    x_bearer_token: str
    studio_base_url: str
    studio_automation_token: str
    lookback_hours: int = 30
    daily_draft_limit: int = 4
    enable_tutorials: bool = False
    timezone: str = "Asia/Seoul"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "AutomationSettings":
        env = os.environ if environ is None else environ
        workspace_id = _required(env, "CONTENT_STUDIO_WORKSPACE_ID")
        try:
            workspace_id = str(uuid.UUID(workspace_id))
        except (ValueError, AttributeError) as exc:
            raise ValueError("CONTENT_STUDIO_WORKSPACE_ID must be a UUID") from exc

        timezone = env.get("AUTOMATION_TIMEZONE", "Asia/Seoul").strip()
        if timezone != "Asia/Seoul":
            raise ValueError("AUTOMATION_TIMEZONE must be Asia/Seoul")
        automation_token = _required(
            env, "STUDIO_AUTOMATION_TOKEN", min_length=32
        )
        if len(automation_token) > 512:
            raise ValueError("STUDIO_AUTOMATION_TOKEN must be at most 512 characters")

        enable_tutorials = _boolean(env, "AUTOMATION_ENABLE_TUTORIALS")
        if enable_tutorials:
            raise ValueError(
                "AUTOMATION_ENABLE_TUTORIALS must remain false until the manual review trigger ships"
            )

        return cls(
            supabase_url=_supabase_url(_required(env, "SUPABASE_URL")),
            supabase_service_role_key=_required(
                env, "SUPABASE_SERVICE_ROLE_KEY", min_length=32
            ),
            workspace_id=workspace_id,
            x_bearer_token=_required(env, "X_BEARER_TOKEN", min_length=20),
            studio_base_url=_required(env, "STUDIO_BASE_URL"),
            studio_automation_token=automation_token,
            lookback_hours=_bounded_int(
                env,
                "AUTOMATION_LOOKBACK_HOURS",
                30,
                minimum=1,
                maximum=168,
            ),
            daily_draft_limit=_bounded_int(
                env,
                "AUTOMATION_DAILY_DRAFT_LIMIT",
                4,
                minimum=1,
                maximum=len(AUTOMATION_CLIENTS),
            ),
            enable_tutorials=enable_tutorials,
            timezone=timezone,
        )
