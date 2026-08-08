from __future__ import annotations

import os
import re
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


PUBLICATION_CLIENTS = ("yellow", "origintrail", "squid", "babylon")
PUBLICATION_TELEGRAM_USERNAMES = {
    "yellow": "yellowkorea_ann",
    "origintrail": "origintrailkr",
    "squid": "squid_kor_update",
    "babylon": "babylonbtc",
}
_FALSE_VALUES = frozenset({"false", ""})
_WORKER_TOKEN_FORBIDDEN_SECRET_NAMES = (
    "API_SECRET",
    "STUDIO_ACCESS_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "TELEGRAM_BOT_TOKEN_SQUID",
    "TELEGRAM_BOT_TOKEN_YELLOW",
    "TELEGRAM_BOT_TOKEN_ORIGINTRAIL",
    "TELEGRAM_BOT_TOKEN_BABYLON",
)


def telegram_publication_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("TELEGRAM_PUBLICATION_ENABLED", "false")
    if raw == "true":
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("TELEGRAM_PUBLICATION_ENABLED must be literal true or false")


def publication_worker_token(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = env.get("PUBLICATION_WORKER_TOKEN", "")
    if (
        not 32 <= len(value) <= 512
        or not value.isascii()
        or re.search(r"[\x00-\x20\x7f]", value)
    ):
        raise ValueError("PUBLICATION_WORKER_TOKEN has an invalid format")
    forbidden_values = [
        env[name].strip()
        for name in _WORKER_TOKEN_FORBIDDEN_SECRET_NAMES
        if env.get(name, "").strip()
    ]
    value_bytes = value.encode("ascii")
    matches = [
        secrets.compare_digest(value_bytes, forbidden.encode("utf-8"))
        for forbidden in forbidden_values
    ]
    if any(matches):
        raise ValueError("PUBLICATION_WORKER_TOKEN must be a dedicated secret")
    return value


def _supabase_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    host = (parsed.hostname or "").lower()
    local = host in {"localhost", "127.0.0.1"}
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not host
        or (not host.endswith(".supabase.co") and not local)
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
    ):
        raise ValueError("SUPABASE_URL is outside the publication allowlist")
    return normalized


def _bounded_int(
    env: Mapping[str, str],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(env.get(name, str(default)).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _service_role_key(env: Mapping[str, str]) -> str:
    service_key = env.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not 32 <= len(service_key) <= 8_192:
        raise ValueError("SUPABASE_SERVICE_ROLE_KEY has an invalid length")
    return service_key


def _workspace_id(env: Mapping[str, str]) -> str:
    try:
        return str(uuid.UUID(env.get("CONTENT_STUDIO_WORKSPACE_ID", "").strip()))
    except (ValueError, AttributeError) as exc:
        raise ValueError("CONTENT_STUDIO_WORKSPACE_ID must be a UUID") from exc


@dataclass(frozen=True)
class PublicationRecoverySettings:
    supabase_url: str
    supabase_service_role_key: str
    workspace_id: str
    recovery_limit: int = 100

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "PublicationRecoverySettings":
        env = os.environ if environ is None else environ
        # Recovery is a rollback-only operation. It must never race an enabled
        # claim worker, and missing/alias values must not silently count as off.
        if env.get("TELEGRAM_PUBLICATION_ENABLED") != "false":
            raise ValueError(
                "TELEGRAM_PUBLICATION_ENABLED must be literal false for recovery"
            )
        return cls(
            supabase_url=_supabase_url(env.get("SUPABASE_URL", "")),
            supabase_service_role_key=_service_role_key(env),
            workspace_id=_workspace_id(env),
            recovery_limit=_bounded_int(
                env, "TELEGRAM_PUBLICATION_RECOVERY_LIMIT", 100, 1, 100
            ),
        )


@dataclass(frozen=True)
class PublicationSettings:
    supabase_url: str
    supabase_service_role_key: str
    workspace_id: str
    allowed_clients: tuple[str, ...] = ("squid",)
    lease_seconds: int = 180
    max_claims: int = 1
    send_timeout_seconds: int = 90
    clients_dir: Path = Path("clients")

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        clients_dir: Path = Path("clients"),
    ) -> "PublicationSettings":
        return cls._from_env(
            environ,
            clients_dir=clients_dir,
            require_enabled=True,
        )

    @classmethod
    def from_env_for_validation(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        clients_dir: Path = Path("clients"),
    ) -> "PublicationSettings":
        """Parse every setting while allowing the execution flag to be false."""
        return cls._from_env(
            environ,
            clients_dir=clients_dir,
            require_enabled=False,
        )

    @classmethod
    def _from_env(
        cls,
        environ: Mapping[str, str] | None,
        *,
        clients_dir: Path,
        require_enabled: bool,
    ) -> "PublicationSettings":
        env = os.environ if environ is None else environ
        enabled = telegram_publication_enabled(env)
        if require_enabled and not enabled:
            raise ValueError("TELEGRAM_PUBLICATION_ENABLED must be true")
        service_key = _service_role_key(env)
        workspace_id = _workspace_id(env)
        raw_clients = env.get("TELEGRAM_PUBLICATION_ALLOWED_CLIENTS", "squid")
        allowed_clients = tuple(raw_clients.split(","))
        if allowed_clients != ("squid",):
            raise ValueError("TELEGRAM_PUBLICATION_ALLOWED_CLIENTS is invalid")
        return cls(
            supabase_url=_supabase_url(env.get("SUPABASE_URL", "")),
            supabase_service_role_key=service_key,
            workspace_id=workspace_id,
            allowed_clients=allowed_clients,
            lease_seconds=_bounded_int(
                env, "TELEGRAM_PUBLICATION_LEASE_SECONDS", 180, 180, 600
            ),
            max_claims=_bounded_int(
                env, "TELEGRAM_PUBLICATION_MAX_CLAIMS", 1, 1, 4
            ),
            send_timeout_seconds=_bounded_int(
                env, "TELEGRAM_PUBLICATION_SEND_TIMEOUT_SECONDS", 90, 30, 120
            ),
            clients_dir=clients_dir,
        )
