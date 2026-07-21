"""Small process-local cache for validated Squid visual localization regions."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from core.sources.source_image import PreparedSourceImage


VISUAL_LOCALIZATION_CACHE_VERSION = "squid-authoritative-visual-v6"
VISUAL_LOCALIZATION_CACHE_MAX_ITEMS = 64
VISUAL_LOCALIZATION_CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    regions: list[dict]


_CACHE: OrderedDict[str, _CacheEntry] = OrderedDict()
_CACHE_LOCK = threading.RLock()


def make_visual_localization_cache_key(
    *,
    client_id: str,
    template_style: str,
    model: str,
    audit_model: str,
    config_fingerprint: str,
    source_type: str,
    source_url: str,
    source_content: str,
    source_image: PreparedSourceImage,
) -> str:
    """Hash all context that can change translation or placement semantics."""
    image_bytes = base64.b64decode(source_image.base64_data, validate=True)
    metadata = json.dumps(
        {
            "version": VISUAL_LOCALIZATION_CACHE_VERSION,
            "client_id": client_id,
            "template_style": template_style,
            "model": model,
            "audit_model": audit_model,
            "config_fingerprint": config_fingerprint,
            "source_type": source_type,
            "source_url": source_url,
            "source_content": source_content,
            "width": source_image.width,
            "height": source_image.height,
            "media_type": source_image.media_type,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(len(metadata).to_bytes(8, "big"))
    digest.update(metadata)
    digest.update(image_bytes)
    return digest.hexdigest()


def get_visual_localization(key: str) -> list[dict] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            del _CACHE[key]
            return None
        _CACHE.move_to_end(key)
        return copy.deepcopy(entry.regions)


def put_visual_localization(key: str, regions: list[dict]) -> None:
    if not isinstance(regions, list) or not regions:
        return
    entry = _CacheEntry(
        expires_at=time.monotonic() + VISUAL_LOCALIZATION_CACHE_TTL_SECONDS,
        regions=copy.deepcopy(regions),
    )
    with _CACHE_LOCK:
        _CACHE[key] = entry
        _CACHE.move_to_end(key)
        while len(_CACHE) > VISUAL_LOCALIZATION_CACHE_MAX_ITEMS:
            _CACHE.popitem(last=False)


def discard_visual_localization(key: str) -> None:
    with _CACHE_LOCK:
        _CACHE.pop(key, None)


def clear_visual_localization_cache() -> None:
    """Test helper; production invalidation uses the versioned cache key."""
    with _CACHE_LOCK:
        _CACHE.clear()
