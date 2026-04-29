import os
from pathlib import Path
from fastapi import HTTPException

API_KEYS = {
    "admin": os.getenv("API_KEY_ADMIN") or os.getenv("API_SECRET"),
    "yellow": os.getenv("API_KEY_YELLOW"),
    "squid": os.getenv("API_KEY_SQUID"),
}

OUTPUT_ROOT = Path(os.getenv("OUTPUT_ROOT", "/tmp/content_engine_output")).resolve()


def check_any_valid_key(x_api_key: str) -> str:
    """Verify api_key matches any configured key. Returns client_id."""
    for cid, key in API_KEYS.items():
        if key and x_api_key == key:
            return cid
    raise HTTPException(status_code=401, detail="invalid_api_key")


def resolve_safe_path(requested: str) -> Path:
    """Normalize and validate /files/{path} request path."""
    cleaned = requested.lstrip("/").replace("//", "/")
    candidate = (OUTPUT_ROOT / cleaned).resolve()
    try:
        candidate.relative_to(OUTPUT_ROOT)
    except ValueError:
        raise HTTPException(status_code=403, detail="path_traversal_blocked")
    return candidate


def validate_client_scope(api_key: str, path: Path) -> str:
    """Verify api_key has access to path. Returns authenticated client_id."""
    client_id = check_any_valid_key(api_key)
    if client_id == "admin":
        return client_id
    expected_prefix = OUTPUT_ROOT / client_id
    try:
        path.relative_to(expected_prefix)
    except ValueError:
        raise HTTPException(status_code=403, detail="client_scope_violation")
    return client_id
