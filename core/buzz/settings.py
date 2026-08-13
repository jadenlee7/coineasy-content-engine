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
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_LOWER_HEX_128 = re.compile(r"^[0-9a-f]{128}$")
_CANONICAL_DECIMAL = re.compile(r"^(?:0|[1-9][0-9]*)$")
_RESERVED_SECRET_NAMES = (
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_BUZZ_DELIVERY_KEY",
    "SUPABASE_BUZZ_SHADOW_KEY",
    "SUPABASE_BUZZ_REVIEW_KEY",
    "BUZZ_REVIEW_WORKER_TOKEN",
    "BUZZ_OPERATIONS_WORKER_TOKEN",
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


def buzz_review_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("BUZZ_REVIEW_ENABLED", "false")
    if raw == "true":
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("BUZZ_REVIEW_ENABLED must be literal true or false")


def buzz_review_ack_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("BUZZ_REVIEW_ACK_ENABLED", "false")
    if raw == "true":
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("BUZZ_REVIEW_ACK_ENABLED must be literal true or false")


def buzz_review_durable_ack_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("BUZZ_REVIEW_DURABLE_ACK_ENABLED", "false")
    if raw == "true":
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError(
        "BUZZ_REVIEW_DURABLE_ACK_ENABLED must be literal true or false"
    )


def buzz_operations_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("BUZZ_OPERATIONS_ENABLED", "false")
    if raw == "true":
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError("BUZZ_OPERATIONS_ENABLED must be literal true or false")


def buzz_operations_response_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    raw = env.get("BUZZ_OPERATIONS_RESPONSE_ENABLED", "false")
    if raw == "true":
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ValueError(
        "BUZZ_OPERATIONS_RESPONSE_ENABLED must be literal true or false"
    )


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
        raise ValueError(
            "BUZZ_RELAY_URL must be a bare https host (no path, query, or"
            " credentials); http is allowed only for localhost"
        )
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


def _nip_oa_auth_tag(value: object) -> list[str]:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(item, str) for item in value)
    ):
        raise ValueError("BUZZ_AUTH_TAG must be a four-string NIP-OA auth tag")

    tag_name, owner_pubkey, conditions, signature = value
    if tag_name != "auth":
        raise ValueError("BUZZ_AUTH_TAG must be a NIP-OA auth tag")
    if not _LOWER_HEX_64.fullmatch(owner_pubkey):
        raise ValueError("BUZZ_AUTH_TAG owner pubkey is invalid")
    if not _LOWER_HEX_128.fullmatch(signature):
        raise ValueError("BUZZ_AUTH_TAG signature is invalid")

    if conditions:
        for clause in conditions.split("&"):
            if clause.startswith("kind="):
                raw_value = clause.removeprefix("kind=")
                maximum = 65_535
            elif clause.startswith("created_at<"):
                raw_value = clause.removeprefix("created_at<")
                maximum = 4_294_967_295
            elif clause.startswith("created_at>"):
                raw_value = clause.removeprefix("created_at>")
                maximum = 4_294_967_295
            else:
                raise ValueError("BUZZ_AUTH_TAG conditions are invalid")
            if (
                not _CANONICAL_DECIMAL.fullmatch(raw_value)
                or int(raw_value) > maximum
            ):
                raise ValueError("BUZZ_AUTH_TAG conditions are invalid")

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
            auth_tag = json.dumps(
                _nip_oa_auth_tag(decoded),
                separators=(",", ":"),
            )
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


@dataclass(frozen=True)
class BuzzReviewSettings:
    review_url: str
    review_token: str
    relay_url: str
    private_key: str
    auth_tag: str | None
    channel_id: str
    cli_path: Path
    reviewer_pubkeys: frozenset[str]
    service_pubkey: str
    deployment_environment: str
    release_sha: str
    protocol_start_epoch: int
    acknowledgement_enabled: bool
    durable_acknowledgement_enabled: bool
    acknowledgement_lease_seconds: int

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "BuzzReviewSettings":
        return cls._from_env(environ, require_enabled=True)

    @classmethod
    def from_env_for_validation(
        cls, environ: Mapping[str, str] | None = None
    ) -> "BuzzReviewSettings":
        return cls._from_env(environ, require_enabled=False)

    @classmethod
    def _from_env(
        cls,
        environ: Mapping[str, str] | None,
        *,
        require_enabled: bool,
    ) -> "BuzzReviewSettings":
        env = os.environ if environ is None else environ
        enabled = buzz_review_enabled(env)
        if require_enabled and not enabled:
            raise ValueError("BUZZ_REVIEW_ENABLED must be true")
        if env.get("BUZZ_REVIEW_ALLOWED_CLIENTS", "") != "origintrail":
            raise ValueError("BUZZ_REVIEW_ALLOWED_CLIENTS must be origintrail")

        review_url = _https_endpoint(
            env.get("BUZZ_REVIEW_URL", ""),
            "/api/buzz-review/origintrail",
        )
        review_token = _token(env, "BUZZ_REVIEW_WORKER_TOKEN")
        reserved_names = (
            "BUZZ_SHADOW_ACCESS_TOKEN",
            "BUZZ_DELIVERY_WORKER_TOKEN",
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_BUZZ_DELIVERY_KEY",
            "SUPABASE_BUZZ_SHADOW_KEY",
            "SUPABASE_BUZZ_REVIEW_KEY",
            "STUDIO_ACCESS_TOKEN",
            "API_SECRET",
            "PUBLICATION_WORKER_TOKEN",
            "OPENAI_API_KEY",
            "BUZZ_OPERATIONS_WORKER_TOKEN",
        )
        if any(
            env.get(name, "")
            and secrets.compare_digest(review_token, env[name])
            for name in reserved_names
        ):
            raise ValueError("Buzz review token must be a dedicated secret")

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
            auth_tag = json.dumps(_nip_oa_auth_tag(decoded), separators=(",", ":"))
        if secrets.compare_digest(review_token, private_key) or (
            raw_auth and secrets.compare_digest(review_token, raw_auth)
        ):
            raise ValueError("Buzz review token must be a dedicated secret")

        try:
            channel_id = str(uuid.UUID(env.get("BUZZ_CHANNEL_ID", "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("BUZZ_CHANNEL_ID must be a UUID") from exc
        cli_path = Path(env.get("BUZZ_CLI_PATH", ""))
        if not cli_path.is_absolute() or cli_path.name != "buzz":
            raise ValueError("BUZZ_CLI_PATH must be an absolute buzz executable path")

        raw_reviewers = env.get("BUZZ_REVIEWER_PUBKEYS", "")
        reviewer_values = [item.strip() for item in raw_reviewers.split(",")]
        if (
            not 1 <= len(reviewer_values) <= 5
            or any(not _LOWER_HEX_64.fullmatch(item) for item in reviewer_values)
            or len(set(reviewer_values)) != len(reviewer_values)
        ):
            raise ValueError(
                "BUZZ_REVIEWER_PUBKEYS must contain 1 to 5 distinct lowercase hex keys"
            )
        service_pubkey = env.get("BUZZ_SERVICE_PUBKEY", "").strip()
        if not _LOWER_HEX_64.fullmatch(service_pubkey):
            raise ValueError("BUZZ_SERVICE_PUBKEY must be a lowercase hex key")
        if service_pubkey in reviewer_values:
            raise ValueError("Buzz service identity cannot approve its own work")

        deployment_environment = env.get("RAILWAY_ENVIRONMENT_NAME", "").strip()
        expected_environment = env.get(
            "BUZZ_REVIEW_EXPECTED_ENVIRONMENT", ""
        ).strip()
        if (
            deployment_environment not in {"staging", "production"}
            or expected_environment != deployment_environment
        ):
            raise ValueError("Buzz review environment fence does not match")
        acknowledgement_enabled = buzz_review_ack_enabled(env)
        durable_acknowledgement_enabled = buzz_review_durable_ack_enabled(env)
        if acknowledgement_enabled != durable_acknowledgement_enabled:
            raise ValueError(
                "Buzz review acknowledgement and durable outbox must be enabled together"
            )
        if acknowledgement_enabled and deployment_environment != "staging":
            raise ValueError(
                "Buzz review acknowledgement is restricted to staging"
            )
        release_sha = env.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
        expected_release_sha = env.get("BUZZ_REVIEW_RELEASE_SHA", "").strip()
        if (
            not re.fullmatch(r"[a-f0-9]{40}", release_sha)
            or expected_release_sha != release_sha
        ):
            raise ValueError("Buzz review release SHA fence does not match")
        protocol_start_epoch = _bounded_int(
            env,
            "BUZZ_REVIEW_PROTOCOL_START_EPOCH",
            0,
            1_700_000_000,
            4_294_967_295,
        )

        return cls(
            review_url=review_url,
            review_token=review_token,
            relay_url=_relay_url(env.get("BUZZ_RELAY_URL", "")),
            private_key=private_key,
            auth_tag=auth_tag,
            channel_id=channel_id,
            cli_path=cli_path,
            reviewer_pubkeys=frozenset(reviewer_values),
            service_pubkey=service_pubkey,
            deployment_environment=deployment_environment,
            release_sha=release_sha,
            protocol_start_epoch=protocol_start_epoch,
            acknowledgement_enabled=acknowledgement_enabled,
            durable_acknowledgement_enabled=durable_acknowledgement_enabled,
            acknowledgement_lease_seconds=_bounded_int(
                env, "BUZZ_REVIEW_ACK_LEASE_SECONDS", 180, 180, 600
            ),
        )


@dataclass(frozen=True)
class BuzzOperationsSettings:
    operations_url: str
    operations_token: str
    relay_url: str
    private_key: str
    auth_tag: str | None
    channel_id: str
    cli_path: Path
    reviewer_pubkeys: frozenset[str]
    service_pubkey: str
    deployment_environment: str
    release_sha: str
    protocol_start_epoch: int
    response_enabled: bool
    response_lease_seconds: int

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "BuzzOperationsSettings":
        return cls._from_env(environ, require_enabled=True)

    @classmethod
    def from_env_for_validation(
        cls, environ: Mapping[str, str] | None = None
    ) -> "BuzzOperationsSettings":
        return cls._from_env(environ, require_enabled=False)

    @classmethod
    def _from_env(
        cls,
        environ: Mapping[str, str] | None,
        *,
        require_enabled: bool,
    ) -> "BuzzOperationsSettings":
        env = os.environ if environ is None else environ
        enabled = buzz_operations_enabled(env)
        response_enabled = buzz_operations_response_enabled(env)
        if require_enabled and not enabled:
            raise ValueError("BUZZ_OPERATIONS_ENABLED must be true")
        if enabled != response_enabled:
            raise ValueError(
                "Buzz operations scanner and response gates must match"
            )
        if env.get("BUZZ_OPERATIONS_ALLOWED_CLIENTS", "") != "origintrail":
            raise ValueError(
                "BUZZ_OPERATIONS_ALLOWED_CLIENTS must be origintrail"
            )
        operations_url = _https_endpoint(
            env.get("BUZZ_OPERATIONS_URL", ""),
            "/api/buzz-operations/origintrail",
        )
        operations_token = _token(env, "BUZZ_OPERATIONS_WORKER_TOKEN")
        reserved_names = (
            "BUZZ_REVIEW_WORKER_TOKEN",
            "BUZZ_SHADOW_ACCESS_TOKEN",
            "BUZZ_DELIVERY_WORKER_TOKEN",
            "SUPABASE_SERVICE_ROLE_KEY",
            "SUPABASE_BUZZ_DELIVERY_KEY",
            "SUPABASE_BUZZ_SHADOW_KEY",
            "SUPABASE_BUZZ_REVIEW_KEY",
            "SUPABASE_BUZZ_OPERATIONS_KEY",
            "STUDIO_ACCESS_TOKEN",
            "API_SECRET",
            "PUBLICATION_WORKER_TOKEN",
            "OPENAI_API_KEY",
        )
        if any(
            env.get(name, "")
            and secrets.compare_digest(operations_token, env[name])
            for name in reserved_names
        ):
            raise ValueError("Buzz operations token must be a dedicated secret")

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
            auth_tag = json.dumps(
                _nip_oa_auth_tag(decoded), separators=(",", ":")
            )
        if secrets.compare_digest(operations_token, private_key) or (
            raw_auth and secrets.compare_digest(operations_token, raw_auth)
        ):
            raise ValueError("Buzz operations token must be a dedicated secret")

        try:
            channel_id = str(uuid.UUID(env.get("BUZZ_CHANNEL_ID", "").strip()))
        except (ValueError, AttributeError) as exc:
            raise ValueError("BUZZ_CHANNEL_ID must be a UUID") from exc
        cli_path = Path(env.get("BUZZ_CLI_PATH", ""))
        if not cli_path.is_absolute() or cli_path.name != "buzz":
            raise ValueError("BUZZ_CLI_PATH must be an absolute buzz executable path")

        reviewer_values = [
            item.strip()
            for item in env.get("BUZZ_OPERATIONS_REVIEWER_PUBKEYS", "").split(",")
        ]
        if (
            not 1 <= len(reviewer_values) <= 5
            or any(not _LOWER_HEX_64.fullmatch(item) for item in reviewer_values)
            or len(set(reviewer_values)) != len(reviewer_values)
        ):
            raise ValueError(
                "BUZZ_OPERATIONS_REVIEWER_PUBKEYS must contain 1 to 5 distinct keys"
            )
        service_pubkey = env.get("BUZZ_SERVICE_PUBKEY", "").strip()
        if not _LOWER_HEX_64.fullmatch(service_pubkey):
            raise ValueError("BUZZ_SERVICE_PUBKEY must be a lowercase hex key")
        if service_pubkey in reviewer_values:
            raise ValueError("Buzz service identity cannot command itself")

        deployment_environment = env.get("RAILWAY_ENVIRONMENT_NAME", "").strip()
        expected_environment = env.get(
            "BUZZ_OPERATIONS_EXPECTED_ENVIRONMENT", ""
        ).strip()
        if (
            deployment_environment not in {"staging", "production"}
            or expected_environment != deployment_environment
        ):
            raise ValueError("Buzz operations environment fence does not match")
        if enabled and deployment_environment != "staging":
            raise ValueError("Buzz operations v1 is restricted to staging")

        release_sha = env.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
        expected_release_sha = env.get(
            "BUZZ_OPERATIONS_RELEASE_SHA", ""
        ).strip()
        if (
            not re.fullmatch(r"[a-f0-9]{40}", release_sha)
            or release_sha != expected_release_sha
        ):
            raise ValueError("Buzz operations release SHA fence does not match")

        return cls(
            operations_url=operations_url,
            operations_token=operations_token,
            relay_url=_relay_url(env.get("BUZZ_RELAY_URL", "")),
            private_key=private_key,
            auth_tag=auth_tag,
            channel_id=channel_id,
            cli_path=cli_path,
            reviewer_pubkeys=frozenset(reviewer_values),
            service_pubkey=service_pubkey,
            deployment_environment=deployment_environment,
            release_sha=release_sha,
            protocol_start_epoch=_bounded_int(
                env, "BUZZ_OPERATIONS_PROTOCOL_START_EPOCH", 0,
                1_700_000_000, 4_294_967_295,
            ),
            response_enabled=response_enabled,
            response_lease_seconds=_bounded_int(
                env, "BUZZ_OPERATIONS_RESPONSE_LEASE_SECONDS", 180, 180, 600
            ),
        )
