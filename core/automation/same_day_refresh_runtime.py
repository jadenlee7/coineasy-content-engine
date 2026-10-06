"""Local configuration/image-stamp checks only; no DB, HTTP or provider clients."""
from __future__ import annotations

import os
import re
import secrets
import stat
import uuid
from typing import Callable, Mapping


IMAGE_STAMP_PATH = "/app/same-day-refresh-build-sha"
_SHA40 = re.compile(r"[a-f0-9]{40}\Z")
_ALLOWED_CREDENTIAL_NAMES = frozenset({
    "SUPABASE_SERVICE_ROLE_KEY", "STUDIO_AUTOMATION_TOKEN",
})


def read_image_stamp() -> str:
    # No CLI argument or environment variable can select another stamp path.
    fd = os.open(IMAGE_STAMP_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o222:
            raise ValueError("same_day_refresh_image_stamp_invalid")
        raw = os.read(fd, 42)
        if re.fullmatch(rb"[a-f0-9]{40}\n", raw) is None:
            raise ValueError("same_day_refresh_image_stamp_invalid")
        return raw[:-1].decode("ascii")
    finally:
        os.close(fd)


def _credential_name_boundary(env: Mapping[str, str]) -> None:
    # Inspect names, not provider/DB credential values. Even empty unexpected
    # keys are rejected rather than treating a sealed value as absent.
    for name in env:
        upper = name.upper()
        if name in _ALLOWED_CREDENTIAL_NAMES:
            continue
        if name == "GPG_KEY" and env.get(name) == "":
            continue  # Docker clears the Python base image's unused metadata.
        if (upper.startswith(("TELEGRAM_", "TYPEFULLY_", "GROK_", "XAI_", "OPENAI_", "ANTHROPIC_",
                              "AWS_", "AZURE_", "GOOGLE_", "GCP_", "PG", "DATABASE_", "DB_",
                              "POSTGRES_", "POSTGRESQL_", "REDIS_", "MONGO_", "MONGODB_", "MYSQL_"))
            or upper.endswith(("_KEY", "_KEY_ID", "_TOKEN", "_SECRET", "_PASSWORD", "_DSN", "_CREDENTIALS"))
            or upper in {"TOKEN", "SECRET", "PASSWORD", "CLOUDSDK_CONFIG", "HTTP_PROXY", "HTTPS_PROXY",
                         "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                         "CURL_CA_BUNDLE"}):
            raise ValueError("same_day_refresh_credential_boundary")


def validate_runtime(
    env: Mapping[str, str], *, required_enabled: bool,
    stamp_reader: Callable[[], str] = read_image_stamp,
) -> dict[str, object]:
    """Validate local equality, never claim hosted origin or authorize execution."""
    if env.get("OFFICIAL_X_SAME_DAY_REFRESH_ENABLED") != ("true" if required_enabled else "false"):
        raise ValueError("same_day_refresh_flag_invalid")
    _credential_name_boundary(env)
    configured = env.get("OFFICIAL_X_SAME_DAY_REFRESH_RELEASE_SHA", "")
    native = env.get("RAILWAY_GIT_COMMIT_SHA", "")
    image = stamp_reader()
    if any(type(value) is not str or _SHA40.fullmatch(value) is None
           for value in (configured, native, image)):
        raise ValueError("same_day_refresh_release_mismatch")
    if not secrets.compare_digest(configured, native) or not secrets.compare_digest(configured, image):
        raise ValueError("same_day_refresh_release_mismatch")
    workspace = env.get("CONTENT_STUDIO_WORKSPACE_ID", "")
    if type(workspace) is not str:
        raise ValueError("same_day_refresh_workspace_invalid")
    parsed = uuid.UUID(workspace)
    if str(parsed) != workspace or parsed.version not in {1, 2, 3, 4, 5}:
        raise ValueError("same_day_refresh_workspace_invalid")
    return {
        "release_sha": configured,
        "native_runtime_sha_matches": True,
        "image_stamp_matches": True,
        "workspace_config_valid": True,
        # Equal local inputs do not independently establish GitHub origin,
        # deployed image digest, workspace existence, or downstream API SHA.
        "image_attestation": False,
        "hosted_provenance_verified": False,
    }
