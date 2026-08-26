from __future__ import annotations

import os
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Mapping, Optional

from core.grok_qa.models import GROK_QA_CLIENTS


_XAI_API_KEY_RE = re.compile(r"^xai-[A-Za-z0-9_-]{16,508}$")
_RELEASE_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_FALSE_VALUES = frozenset({"false", ""})
_WORKER_FORBIDDEN_CREDENTIAL_NAMES = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "API_SECRET",
    "API_KEY_ADMIN",
    "API_KEY_YELLOW",
    "API_KEY_ORIGINTRAIL",
    "API_KEY_SQUID",
    "API_KEY_BABYLON",
    "STUDIO_ACCESS_TOKEN",
    "STUDIO_AUTOMATION_TOKEN",
    "CONTENT_KPI_SYNC_TOKEN",
    "BUZZ_SHADOW_ACCESS_TOKEN",
    "BUZZ_DELIVERY_WORKER_TOKEN",
    "BUZZ_REVIEW_WORKER_TOKEN",
    "BUZZ_PRIVATE_KEY",
    "BUZZ_AUTH_TAG",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_BUZZ_DELIVERY_KEY",
    "SUPABASE_BUZZ_REVIEW_KEY",
    "SUPABASE_BUZZ_SHADOW_KEY",
    "GROK_QA_CONNECTOR_TOKEN",
    "GROK_QA_RELAY_TOKEN",
    "PUBLICATION_WORKER_TOKEN",
    "EASYFARM_CONTENT_SIGNALS_TOKEN",
    "TELEGRAM_REVIEW_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "TG_BOT_TOKEN",
    "TELEGRAM_CONTENT_OPS_BOT_TOKEN",
    "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN_SQUID",
    "TELEGRAM_BOT_TOKEN_YELLOW",
    "TELEGRAM_BOT_TOKEN_ORIGINTRAIL",
    "TELEGRAM_BOT_TOKEN_BABYLON",
    "X_BEARER_TOKEN",
    "TYPEFULLY_API_KEY",
    "DATABASE_URL",
    "SUPABASE_DB_URL",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "NETLIFY_AUTH_TOKEN",
    "NETLIFY_ACCESS_TOKEN",
    "RAILWAY_TOKEN",
    "FIGMA_ACCESS_TOKEN",
)
_XAI_FORBIDDEN_SECRET_NAMES = (
    *_WORKER_FORBIDDEN_CREDENTIAL_NAMES,
    "GROK_QA_DISPATCH_TOKEN",
)


def validate_grok_qa_worker_secret_boundary(
    environ: Optional[Mapping[str, str]] = None,
) -> None:
    env = os.environ if environ is None else environ
    if any(env.get(name, "").strip() for name in _WORKER_FORBIDDEN_CREDENTIAL_NAMES):
        raise ValueError(
            "Grok QA worker must not receive privileged operational credentials"
        )


def grok_qa_dispatch_enabled(
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("GROK_QA_DISPATCH_ENABLED", "false")
    if raw == "true":
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("GROK_QA_DISPATCH_ENABLED must be literal true or false")


def grok_qa_canary_mode(
    environ: Optional[Mapping[str, str]] = None,
) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("GROK_QA_CANARY_MODE", "false")
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ValueError("GROK_QA_CANARY_MODE must be literal true or false")


def _canary_content_version_id(
    env: Mapping[str, str],
    *,
    canary_mode: bool,
) -> Optional[str]:
    raw = env.get("GROK_QA_CANARY_CONTENT_VERSION_ID", "").strip()
    if not raw:
        if canary_mode:
            raise ValueError(
                "GROK_QA_CANARY_CONTENT_VERSION_ID is required in canary mode"
            )
        return None
    if not canary_mode:
        raise ValueError(
            "GROK_QA_CANARY_CONTENT_VERSION_ID must be empty unless "
            "GROK_QA_CANARY_MODE is true"
        )
    try:
        parsed = uuid.UUID(raw)
    except (AttributeError, ValueError) as exc:
        raise ValueError(
            "GROK_QA_CANARY_CONTENT_VERSION_ID must be a canonical UUID"
        ) from exc
    if parsed.int == 0 or str(parsed) != raw:
        raise ValueError(
            "GROK_QA_CANARY_CONTENT_VERSION_ID must be a canonical UUID"
        )
    return raw


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _xai_api_key(env: Mapping[str, str]) -> str:
    value = env.get("XAI_API_KEY", "").strip()
    if not _XAI_API_KEY_RE.fullmatch(value):
        raise ValueError("XAI_API_KEY has an invalid format")
    value_bytes = value.encode("ascii")
    for name in _XAI_FORBIDDEN_SECRET_NAMES:
        forbidden = env.get(name, "").strip()
        if forbidden and secrets.compare_digest(
            value_bytes,
            forbidden.encode("utf-8"),
        ):
            raise ValueError("XAI_API_KEY must be a dedicated secret")
    return value


@dataclass(frozen=True)
class GrokQaSettings:
    xai_api_key: str
    railway_environment_name: str
    expected_environment: str
    release_sha: str
    model: str = "grok-4.5"
    allowed_clients: tuple[str, ...] = ("squid",)
    canary_mode: bool = False
    canary_content_version_id: Optional[str] = None
    lease_seconds: int = 300
    max_source_age_seconds: int = 86_400
    max_turns: int = 3
    x_search_window_days: int = 1
    max_output_tokens: int = 1_600
    max_cost_in_usd_ticks: int = 500_000_000
    timeout_seconds: int = 180

    @classmethod
    def from_env(
        cls,
        environ: Optional[Mapping[str, str]] = None,
    ) -> "GrokQaSettings":
        return cls._from_env(environ, require_enabled=True)

    @classmethod
    def from_env_for_validation(
        cls,
        environ: Optional[Mapping[str, str]] = None,
    ) -> "GrokQaSettings":
        return cls._from_env(environ, require_enabled=False)

    @classmethod
    def _from_env(
        cls,
        environ: Optional[Mapping[str, str]],
        *,
        require_enabled: bool,
    ) -> "GrokQaSettings":
        env = os.environ if environ is None else environ
        validate_grok_qa_worker_secret_boundary(env)
        enabled = grok_qa_dispatch_enabled(env)
        if require_enabled and not enabled:
            raise ValueError("GROK_QA_DISPATCH_ENABLED must be true")
        canary_mode = grok_qa_canary_mode(env)
        canary_content_version_id = _canary_content_version_id(
            env,
            canary_mode=canary_mode,
        )

        model = env.get("GROK_QA_MODEL", "grok-4.5").strip()
        if model != "grok-4.5":
            raise ValueError("GROK_QA_MODEL must be grok-4.5")

        railway_environment = env.get("RAILWAY_ENVIRONMENT_NAME", "").strip()
        expected_environment = env.get(
            "GROK_QA_EXPECTED_ENVIRONMENT", "production"
        ).strip()
        if (
            railway_environment != "production"
            or expected_environment != "production"
            or railway_environment != expected_environment
        ):
            raise ValueError(
                "Grok QA dispatch requires the exact production environment"
            )
        railway_sha = env.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
        release_sha = env.get("GROK_QA_RELEASE_SHA", "").strip()
        if (
            not _RELEASE_SHA_RE.fullmatch(railway_sha)
            or not _RELEASE_SHA_RE.fullmatch(release_sha)
            or not secrets.compare_digest(railway_sha, release_sha)
        ):
            raise ValueError("Grok QA release SHA fence is invalid")

        raw_clients = env.get("GROK_QA_ALLOWED_CLIENTS", "squid")
        allowed_clients = tuple(
            value.strip().lower()
            for value in raw_clients.split(",")
            if value.strip()
        )
        if (
            not allowed_clients
            or len(allowed_clients) != len(set(allowed_clients))
            or any(value not in GROK_QA_CLIENTS for value in allowed_clients)
        ):
            raise ValueError(
                "GROK_QA_ALLOWED_CLIENTS must be a unique nonempty subset of "
                "yellow, origintrail, squid, babylon"
            )

        return cls(
            xai_api_key=_xai_api_key(env),
            railway_environment_name=railway_environment,
            expected_environment=expected_environment,
            release_sha=release_sha,
            model=model,
            allowed_clients=allowed_clients,
            canary_mode=canary_mode,
            canary_content_version_id=canary_content_version_id,
            lease_seconds=_bounded_int(
                env, "GROK_QA_LEASE_SECONDS", 300, 180, 600
            ),
            max_source_age_seconds=_bounded_int(
                env,
                "GROK_QA_MAX_SOURCE_AGE_SECONDS",
                86_400,
                300,
                604_800,
            ),
            max_turns=_bounded_int(env, "GROK_QA_MAX_TURNS", 3, 1, 3),
            x_search_window_days=_bounded_int(
                env, "GROK_QA_X_SEARCH_WINDOW_DAYS", 1, 0, 3
            ),
            max_output_tokens=_bounded_int(
                env, "GROK_QA_MAX_OUTPUT_TOKENS", 1_600, 256, 4_000
            ),
            max_cost_in_usd_ticks=_bounded_int(
                env,
                "GROK_QA_MAX_COST_IN_USD_TICKS",
                500_000_000,
                1,
                5_000_000_000,
            ),
            timeout_seconds=_bounded_int(
                env, "GROK_QA_TIMEOUT_SECONDS", 180, 30, 300
            ),
        )

    @property
    def active_canary_content_version_id(self) -> Optional[str]:
        if not self.canary_mode:
            return None
        return self.canary_content_version_id


__all__ = [
    "GrokQaSettings",
    "grok_qa_canary_mode",
    "grok_qa_dispatch_enabled",
    "validate_grok_qa_worker_secret_boundary",
]
