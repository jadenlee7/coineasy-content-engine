import base64

import pytest

from core.sources import visual_localization_cache as cache_module

from core.sources.source_image import PreparedSourceImage
from core.sources.visual_localization_cache import (
    clear_visual_localization_cache,
    discard_visual_localization,
    get_visual_localization,
    make_visual_localization_cache_key,
    put_visual_localization,
)


@pytest.fixture(autouse=True)
def empty_cache():
    clear_visual_localization_cache()
    yield
    clear_visual_localization_cache()


def _image(payload=b"prepared-jpeg"):
    return PreparedSourceImage(
        media_type="image/jpeg",
        base64_data=base64.b64encode(payload).decode("ascii"),
        width=480,
        height=320,
    )


def _key(**overrides):
    values = {
        "client_id": "squid",
        "template_style": "remix",
        "model": "claude-opus-4-8",
        "audit_model": "claude-sonnet-4-5-20250929",
        "config_fingerprint": "squid-news-card-config-v1",
        "source_type": "tweet",
        "source_url": "https://x.com/squidrouter/status/1",
        "source_content": "Long week? Sundays are for",
        "source_image": _image(),
    }
    values.update(overrides)
    return make_visual_localization_cache_key(**values)


def test_successful_regions_are_deep_copied_and_discardable():
    key = _key()
    regions = [{"source_text": "chillin'", "text": "여유롭게", "x": 40}]
    put_visual_localization(key, regions)
    regions[0]["text"] = "변경"

    first = get_visual_localization(key)
    assert first == [{"source_text": "chillin'", "text": "여유롭게", "x": 40}]
    first[0]["text"] = "손상"
    assert get_visual_localization(key)[0]["text"] == "여유롭게"

    discard_visual_localization(key)
    assert get_visual_localization(key) is None


@pytest.mark.parametrize(
    "override",
    [
        {"client_id": "yellow"},
        {"source_url": "https://x.com/squidrouter/status/2"},
        {"source_content": "Different copy"},
        {"model": "claude-sonnet-4-5-20250929"},
        {"audit_model": "claude-sonnet-4-6"},
        {"config_fingerprint": "squid-news-card-config-v2"},
        {"source_image": _image(b"different-image")},
    ],
)
def test_key_partitions_every_semantic_input(override):
    assert _key(**override) != _key()


def test_empty_or_failed_regions_are_not_cached():
    key = _key()
    put_visual_localization(key, [])
    assert get_visual_localization(key) is None


def test_expired_entry_is_removed(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: now)
    key = _key()
    put_visual_localization(key, [{"text": "여유롭게"}])

    now += cache_module.VISUAL_LOCALIZATION_CACHE_TTL_SECONDS + 0.001
    assert get_visual_localization(key) is None


def test_lru_evicts_least_recently_used_entry(monkeypatch):
    monkeypatch.setattr(cache_module, "VISUAL_LOCALIZATION_CACHE_MAX_ITEMS", 2)
    first = _key(source_url="https://x.com/squidrouter/status/1")
    second = _key(source_url="https://x.com/squidrouter/status/2")
    third = _key(source_url="https://x.com/squidrouter/status/3")
    put_visual_localization(first, [{"text": "첫째"}])
    put_visual_localization(second, [{"text": "둘째"}])
    assert get_visual_localization(first) == [{"text": "첫째"}]

    put_visual_localization(third, [{"text": "셋째"}])

    assert get_visual_localization(second) is None
    assert get_visual_localization(first) == [{"text": "첫째"}]
    assert get_visual_localization(third) == [{"text": "셋째"}]
