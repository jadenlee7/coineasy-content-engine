from __future__ import annotations

import json
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


_FALSE_VALUES = frozenset({"false", ""})
_TOKEN_INVALID = re.compile(r"[^\x21-\x7e]")
_HEX_KEY = re.compile(r"^[a-fA-F0-9]{64}$")
_NSEC_KEY = re.compile(r"^nsec1[023456789acdefghjklmnpqrstuvwxyz]{20,120}$")
_RESERVED_SECRET_NAMES = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "STUDIO_ACCESS_TOKEN",
    "API_SECRET",
    "PUBLICATION_WORKER_TOKEN",
    "OPENAI_API_KEY",
)


def buzz_delivery_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("BUZZ_DELIVERY_ENABLED", "false")
    if raw == "true":
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("BUZZ_DELIVERY_ENABLED must be literal true or false")


def _https_endpoint(value: str, expected_path: str) -> str:
    parsed = urlsplit(value.strip())
    local = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1"}
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != expected_path
        or not parsed.hostname
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
    ):
        raise ValueError(f"Buzz endpoint must be an exact {expected_path} URL")
    return value.strip()


def _relay_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    local = (parsed.hostname or "").lower() in {"localhost", "127.0.0.1"}
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or not parsed.hostname
        or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
    ):
        raise ValueError("BUZZ_RELAY_URL is outside the allowlist")
    return normalized


def _token(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not 32 <= len(value) <= 512 or _TOKEN_INVALID.search(value):
        raise ValueError(f"{name} has an invalid format")
    return value


def _bounded_int(
    env: Mapping[str, str], name: str, default: int, minimum: int, maximum: int
) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class BuzzDeliverySettings:
    shadow_url: str
    delivery_url: str
    shadow_token: str
    delivery_token: str
    relay_url: str
    private_key: str
    auth_tag: str | None
    channel_id: str
    cli_path: Path
    lease_seconds: int = 180

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "BuzzDeliverySettings":
        return cls._from_env(environ, require_enabled=True)

    @classmethod
    def from_env_for_validation(
        cls, environ: Mapping[str, str] | None = None
    ) -> "BuzzDeliverySettings":
        return cls._from_env(environ, require_enabled=False)

    @classmethod
    def _from_env(
        cls,
        environ: Mapping[str, str] | None,
        *,
        require_enabled: bool,
    ) -> "BuzzDeliverySettings":
        env = os.environ if environ is None else environ
        enabled = buzz_delivery_enabled(env)
        if require_enabled and not enabled:
            raise ValueError("BUZZ_DELIVERY_ENABLED must be true")
        if env.get("BUZZ_DELIVERY_ALLOWED_CLIENTS", "") != "origintrail":
            raise ValueError("BUZZ_DELIVERY_ALLOWED_CLIENTS must be origintrail")
        shadow_url = _https_endpoint(
            env.get("BUZZ_SHADOW_URL", ""),
            "/api/buzz-shadow/origintrail/batch",
        )
        delivery_url = _https_endpoint(
            env.get("BUZZ_DELIVERY_URL", ""),
            "/api/buzz-delivery/origintrail",
        )
        shadow_parts = urlsplit(shadow_url)
        delivery_parts = urlsplit(delivery_url)
        if (
            shadow_parts.scheme,
            shadow_parts.hostname,
            shadow_parts.port,
        ) != (
            delivery_parts.scheme,
            delivery_parts.hostname,
            delivery_parts.port,
        ):
            raise ValueError("Buzz shadow and delivery endpoints must share one origin")
        shadow_token = _token(env, "BUZZ_SHADOW_ACCESS_TOKEN")
        delivery_token = _token(env, "BUZZ_DELIVERY_WORKER_TOKEN")
        if secrets.compare_digest(shadow_token, delivery_token):
            raise ValueError("Buzz read and delivery tokens must be distinct")
        reserved = [
            env[name]
            for name in _RESERVED_SECRET_NAMES
            if env.get(name, "")
        ]
        for candidate in (shadow_token, delivery_token):
            if any(secrets.compare_digest(candidate, value) for value in reserved):
                raise ValueError("Buzz adapter tokens must be dedicated secrets")
        private_key = env.get("BUZZ_PRIVATE_KEY", "").strip()
        if not (_HEX_KEY.fullmatch(private_key) or _NSEC_KEY.fullmatch(private_key)):
            raise ValueError("BUZZ_PRIVATE_KEY has an invalid format")
        raw_auth = env.get("BUZZ_AUTH_TAG", "").strip()
        auth_tag: str | None = None
        if raw_auth:
            if len(raw_auth.encode("utf-8")) > 8_192:
                raise ValueError("BUZZ_AUTH_TAG is too large")
            try:
                decoded = json.loads(raw_auth)
            except ValueError as exc:
                raise ValueError("BUZZ_AUTH_TAG must be JSON") from exc
            if not isinstance(decoded, dict):
                raise ValueError("BUZZ_AUTH_TAG must be a JSON object")
            auth_tag = json.dumps(decoded, separators=(",", ":"), sort_keys=True)
        try:
            channel_id = str(uuid.UUID(env.get("BUZZ_CHANNEL_ID", "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("BUZZ_CHANNEL_ID must be a UUID") from exc
        cli_path = Path(env.get("BUZZ_CLI_PATH", ""))
        if not cli_path.is_absolute() or cli_path.name != "buzz":
            raise ValueError("BUZZ_CLI_PATH must be an absolute buzz executable path")
        return cls(
            shadow_url=shadow_url,
            delivery_url=delivery_url,
            shadow_token=shadow_token,
            delivery_token=delivery_token,
            relay_url=_relay_url(env.get("BUZZ_RELAY_URL", "")),
            private_key=private_key,
            auth_tag=auth_tag,
            channel_id=channel_id,
            cli_path=cli_path,
            lease_seconds=_bounded_int(
                env, "BUZZ_DELIVERY_LEASE_SECONDS", 180, 180, 600
            ),
        )
