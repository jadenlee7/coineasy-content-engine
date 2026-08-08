from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from core.publishers.telegram_exact import (
    ExactTelegramPublisher,
    TelegramExactConfig,
    TelegramExactError,
    load_telegram_exact_config,
    telegram_send_photo_fingerprint,
)


BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijk12345"
PNG_BYTES = b"\x89PNG\r\n\x1a\nexact-approved-banner"


def _config() -> TelegramExactConfig:
    return TelegramExactConfig(
        client_id="squid",
        public_username="squid_kor_update",
        chat_id="@squid_kor_update",
        bot_token=BOT_TOKEN,
    )


def test_load_config_is_bound_to_the_canonical_public_channel(tmp_path):
    client_dir = tmp_path / "squid"
    client_dir.mkdir()
    (client_dir / "config.yaml").write_text(
        "publishing:\n"
        "  telegram:\n"
        "    public_channel: '@squid_kor_update'\n"
        "    bot_env: TELEGRAM_BOT_TOKEN_SQUID\n"
        "    channel_env: TELEGRAM_CHANNEL_SQUID\n"
        "    active: true\n",
        encoding="utf-8",
    )
    config = load_telegram_exact_config(
        "squid",
        clients_dir=tmp_path,
        environ={
            "TELEGRAM_BOT_TOKEN_SQUID": BOT_TOKEN,
            "TELEGRAM_CHANNEL_SQUID": "@squid_kor_update",
        },
    )
    assert config.public_username == "squid_kor_update"
    assert BOT_TOKEN not in repr(config)

    with pytest.raises(TelegramExactError, match="target_mismatch"):
        load_telegram_exact_config(
            "squid",
            clients_dir=tmp_path,
            environ={
                "TELEGRAM_BOT_TOKEN_SQUID": BOT_TOKEN,
                "TELEGRAM_CHANNEL_SQUID": "@attacker_channel",
            },
        )

    with pytest.raises(TelegramExactError, match="config_invalid"):
        load_telegram_exact_config("../squid", clients_dir=tmp_path, environ={})

    (client_dir / "config.yaml").write_text(
        "publishing:\n"
        "  telegram:\n"
        "    public_channel: '@attacker_channel'\n"
        "    bot_env: TELEGRAM_BOT_TOKEN_SQUID\n"
        "    channel_env: TELEGRAM_CHANNEL_SQUID\n"
        "    active: true\n",
        encoding="utf-8",
    )
    with pytest.raises(TelegramExactError, match="config_invalid"):
        load_telegram_exact_config(
            "squid",
            clients_dir=tmp_path,
            environ={
                "TELEGRAM_BOT_TOKEN_SQUID": BOT_TOKEN,
                "TELEGRAM_CHANNEL_SQUID": "@attacker_channel",
            },
        )


@pytest.mark.asyncio
async def test_preflight_then_send_photo_once_uses_exact_plain_caption():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/getChat"):
            return httpx.Response(200, json={
                "ok": True,
                "result": {
                    "id": -1001234567890,
                    "username": "squid_kor_update",
                    "type": "channel",
                },
            })
        assert request.url.path.endswith("/sendPhoto")
        body = request.content
        assert b'name="caption"' in body
        assert "승인된 문구 그대로".encode() in body
        assert b"parse_mode" not in body
        assert b'name="photo"; filename="news-card.png"' in body
        assert PNG_BYTES in body
        return httpx.Response(200, json={
            "ok": True,
            "result": {
                "message_id": 321,
                "date": 1_786_000_000,
                "chat": {"username": "squid_kor_update", "type": "channel"},
            },
        })

    publisher = ExactTelegramPublisher(
        _config(), transport=httpx.MockTransport(handler)
    )
    await publisher.preflight()
    receipt = await publisher.send_photo_once(
        image_bytes=PNG_BYTES,
        caption="승인된 문구 그대로",
    )

    assert receipt.message_id == 321
    assert receipt.chat_username == "squid_kor_update"
    assert receipt.provider_date == datetime.fromtimestamp(
        1_786_000_000, tz=timezone.utc
    )
    assert len(calls) == 2
    assert sum(request.url.path.endswith("/sendPhoto") for request in calls) == 1
    assert all("sendMessage" not in request.url.path for request in calls)


@pytest.mark.asyncio
async def test_send_timeout_is_never_retried_and_error_is_redacted():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("contains provider request details", request=request)

    publisher = ExactTelegramPublisher(
        _config(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(TelegramExactError) as error:
        await publisher.send_photo_once(
            image_bytes=PNG_BYTES,
            caption="정확한 승인 문구",
        )
    assert error.value.code == "telegram_delivery_unknown"
    assert str(error.value) == "telegram_delivery_unknown"
    assert BOT_TOKEN not in str(error.value)
    assert calls == 1


@pytest.mark.asyncio
async def test_wrong_response_target_is_delivery_unknown_after_one_call():
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={
            "ok": True,
            "result": {
                "message_id": 1,
                "date": 1_786_000_000,
                "chat": {"username": "wrong_channel", "type": "channel"},
            },
        })

    publisher = ExactTelegramPublisher(
        _config(), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(TelegramExactError, match="telegram_delivery_unknown"):
        await publisher.send_photo_once(
            image_bytes=PNG_BYTES,
            caption="정확한 승인 문구",
        )
    assert calls == 1


def test_request_fingerprint_pins_target_caption_and_image():
    first = telegram_send_photo_fingerprint(
        public_username="squid_kor_update",
        caption="승인 문구",
        image_bytes=PNG_BYTES,
    )
    assert first == "6f6f50e8e6743cc22f890296204e827fc858cafba3a472ab5c24a995639812d1"
    assert first != telegram_send_photo_fingerprint(
        public_username="squid_kor_update",
        caption="다른 문구",
        image_bytes=PNG_BYTES,
    )
    assert first != telegram_send_photo_fingerprint(
        public_username="squid_kor_update",
        caption="승인 문구",
        image_bytes=PNG_BYTES + b"changed",
    )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"preflight_timeout_seconds": 4}, "preflight timeout"),
        ({"preflight_timeout_seconds": 31}, "preflight timeout"),
        ({"send_timeout_seconds": 29}, "send timeout"),
        ({"send_timeout_seconds": 121}, "send timeout"),
    ],
)
def test_publisher_rejects_unbounded_provider_timeouts(kwargs, message):
    with pytest.raises(ValueError, match=message):
        ExactTelegramPublisher(_config(), **kwargs)
