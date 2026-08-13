from __future__ import annotations

import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit


_SHA = re.compile(r"^[a-f0-9]{40}$")
_TOKEN_INVALID = re.compile(r"[^\x21-\x7e]")


def autonomous_ops_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    value = env.get("AUTONOMOUS_OPS_ENABLED", "false")
    if value not in {"true", "false"}:
        raise ValueError("AUTONOMOUS_OPS_ENABLED must be true or false")
    return value == "true"


@dataclass(frozen=True)
class AutonomousOpsSettings:
    control_url: str
    control_token: str
    environment: str
    release_sha: str

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "AutonomousOpsSettings":
        return cls._from_env(environ, require_enabled=True)

    @classmethod
    def from_env_for_validation(
        cls, environ: Mapping[str, str] | None = None
    ) -> "AutonomousOpsSettings":
        return cls._from_env(environ, require_enabled=False)

    @classmethod
    def _from_env(
        cls, environ: Mapping[str, str] | None, *, require_enabled: bool
    ) -> "AutonomousOpsSettings":
        env = os.environ if environ is None else environ
        enabled = autonomous_ops_enabled(env)
        record_enabled = env.get("AUTONOMOUS_OPS_RECORD_ENABLED", "false")
        if record_enabled not in {"true", "false"}:
            raise ValueError("AUTONOMOUS_OPS_RECORD_ENABLED must be true or false")
        if enabled != (record_enabled == "true"):
            raise ValueError("Autonomous Ops gates must match")
        if require_enabled and not enabled:
            raise ValueError("AUTONOMOUS_OPS_ENABLED must be true")
        if env.get("AUTONOMOUS_OPS_ALLOWED_CLIENTS", "") != "origintrail":
            raise ValueError("AUTONOMOUS_OPS_ALLOWED_CLIENTS must be origintrail")
        raw_url = env.get("AUTONOMOUS_OPS_URL", "").strip()
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.query
            or parsed.fragment
            or parsed.path != "/api/autonomous-ops/origintrail"
        ):
            raise ValueError("AUTONOMOUS_OPS_URL is invalid")
        token = env.get("AUTONOMOUS_OPS_WORKER_TOKEN", "")
        if (
            not 32 <= len(token) <= 512
            or _TOKEN_INVALID.search(token) is not None
        ):
            raise ValueError("AUTONOMOUS_OPS_WORKER_TOKEN is invalid")
        reserved = (
            "OPENAI_API_KEY", "PUBLICATION_WORKER_TOKEN",
            "BATCH_DISPATCHER_TOKEN", "SUPABASE_SERVICE_ROLE_KEY",
            "BUZZ_PRIVATE_KEY", "BUZZ_OPERATIONS_WORKER_TOKEN",
            "BUZZ_REVIEW_WORKER_TOKEN", "BUZZ_DELIVERY_WORKER_TOKEN",
        )
        if any(
            env.get(name, "")
            and secrets.compare_digest(token, env[name])
            for name in reserved
        ):
            raise ValueError("Autonomous Ops token must be dedicated")
        environment = env.get("RAILWAY_ENVIRONMENT_NAME", "").strip()
        if (
            environment != "staging"
            or env.get("AUTONOMOUS_OPS_EXPECTED_ENVIRONMENT", "") != environment
        ):
            raise ValueError("Autonomous Ops is restricted to staging")
        release = env.get("RAILWAY_GIT_COMMIT_SHA", "").strip()
        if (
            not _SHA.fullmatch(release)
            or env.get("AUTONOMOUS_OPS_RELEASE_SHA", "").strip() != release
        ):
            raise ValueError("Autonomous Ops release SHA fence does not match")
        return cls(
            control_url=raw_url, control_token=token,
            environment=environment, release_sha=release,
        )


__all__ = ["AutonomousOpsSettings", "autonomous_ops_enabled"]
