import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.publishers.telegram_review import (
    TelegramContentOpsRelayConfig,
    TelegramReviewConfig,
    decode_review_image_data_url,
    send_telegram_review,
    telegram_content_ops_relay_config,
    telegram_review_config,
)


BOT_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijk12345"
RELAY_BOT_TOKEN = "987654321:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijk12345"
CHAT_ID = "123456789"
COLLABORATION_CHAT_ID = "-1001234567890"
ITEM_ID = "22222222-2222-4222-8222-222222222222"
VERSION_ID = "33333333-3333-4333-8333-333333333333"
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00"
IMAGE_DATA_URL = (
    "data:image/png;base64," + base64.b64encode(PNG_BYTES).decode("ascii")
)
ADMIN_KEY = "telegram-review-admin-key"
CLIENT_KEY = "telegram-review-squid-key"


class _FakeResponse:
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _FakeAsyncClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url: str, *, json=None, data=None, files=None):
        self.calls.append({
            "url": url,
            "json": json,
            "data": data,
            "files": files,
        })
        return self.responses.pop(0)


def notification_payload(**overrides):
    payload = {
        "content_item_id": ITEM_ID,
        "content_version_id": VERSION_ID,
        "client_id": "squid",
        "content_kind": "article",
        "caption_html": "🔎 <b>검토 요청 · Squid</b>",
        "message_html": (
            "⚠️ <b>미승인 검토용 · 전달/게시 금지</b>\n<b>검토용 Telegram 문구</b>\n\n검토할 내용\n\n"
            "<i>승인 전에는 게시되지 않습니다.</i>"
        ),
        "review_url": (
            "https://coineasy-newscard.netlify.app/"
            f"?view=library&content={ITEM_ID}"
        ),
        "image_url": "",
        "image_data_url": IMAGE_DATA_URL,
    }
    payload.update(overrides)
    return payload


def test_review_config_requires_a_numeric_private_target(monkeypatch):
    monkeypatch.setenv("TELEGRAM_REVIEW_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_REVIEW_CHAT_ID", CHAT_ID)
    assert telegram_review_config() == TelegramReviewConfig(BOT_TOKEN, CHAT_ID)

    monkeypatch.setenv("TELEGRAM_REVIEW_CHAT_ID", "@public_channel")
    assert telegram_review_config() is None


def test_content_ops_relay_requires_a_separate_bot_and_room(monkeypatch):
    primary = TelegramReviewConfig(BOT_TOKEN, CHAT_ID)
    monkeypatch.setenv("TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", RELAY_BOT_TOKEN)
    monkeypatch.setenv(
        "TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", COLLABORATION_CHAT_ID
    )
    assert telegram_content_ops_relay_config(primary) == (
        TelegramContentOpsRelayConfig(RELAY_BOT_TOKEN, COLLABORATION_CHAT_ID)
    )

    monkeypatch.setenv("TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", BOT_TOKEN)
    assert telegram_content_ops_relay_config(primary) is None

    monkeypatch.setenv("TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", RELAY_BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", CHAT_ID)
    assert telegram_content_ops_relay_config(primary) is None

    monkeypatch.setenv("TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", "@private_group")
    assert telegram_content_ops_relay_config(primary) is None


def test_review_image_data_url_is_bounded_and_validated():
    assert decode_review_image_data_url(IMAGE_DATA_URL) == ("image/png", PNG_BYTES)
    assert decode_review_image_data_url("data:image/png;base64,bm90LXBuZw==") is None
    assert decode_review_image_data_url("https://attacker.example/banner.png") is None


@pytest.mark.asyncio
async def test_review_sender_posts_banner_then_copy_without_exposing_config(monkeypatch):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"ok": True, "result": {"message_id": 1}}),
        _FakeResponse(200, {"ok": True, "result": {"message_id": 2}}),
    ])
    monkeypatch.setattr(
        "core.publishers.telegram_review.httpx.AsyncClient",
        lambda *args, **kwargs: fake,
    )
    result = await send_telegram_review(
        config=TelegramReviewConfig(BOT_TOKEN, CHAT_ID),
        caption_html="🔎 <b>검토 요청 · Squid</b>",
        message_html="⚠️ <b>미승인 검토용 · 전달/게시 금지</b>\n<b>검토용 Telegram 문구</b>\n\n검토할 내용",
        review_url=notification_payload()["review_url"],
        image_data_url=IMAGE_DATA_URL,
    )

    assert result == {
        "sent": True,
        "photo_sent": True,
        "text_sent": True,
        "collaboration_configured": False,
        "collaboration_sent": False,
        "collaboration_photo_sent": False,
    }
    assert len(fake.calls) == 2
    assert fake.calls[0]["url"].endswith("/sendPhoto")
    assert fake.calls[0]["data"]["chat_id"] == CHAT_ID
    assert fake.calls[0]["files"]["photo"][1] == PNG_BYTES
    assert fake.calls[1]["url"].endswith("/sendMessage")
    assert (
        fake.calls[1]["json"]["reply_markup"]["inline_keyboard"][0][0]["text"]
        == "콘텐츠 스튜디오 열기"
    )
    assert (
        fake.calls[1]["json"]["reply_markup"]["inline_keyboard"][0][0]["url"]
        == notification_payload()["review_url"]
    )
    assert "bot_token" not in result
    assert "chat_id" not in result


@pytest.mark.asyncio
async def test_review_sender_copies_the_exact_packet_to_the_collaboration_room(
    monkeypatch,
):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"ok": True, "result": {"message_id": 1}}),
        _FakeResponse(200, {"ok": True, "result": {"message_id": 2}}),
        _FakeResponse(200, {"ok": True, "result": {"message_id": 3}}),
        _FakeResponse(200, {"ok": True, "result": {"message_id": 4}}),
    ])
    monkeypatch.setattr(
        "core.publishers.telegram_review.httpx.AsyncClient",
        lambda *args, **kwargs: fake,
    )

    result = await send_telegram_review(
        config=TelegramReviewConfig(BOT_TOKEN, CHAT_ID),
        collaboration_config=TelegramContentOpsRelayConfig(
            RELAY_BOT_TOKEN, COLLABORATION_CHAT_ID
        ),
        caption_html="🔎 <b>검토 요청 · Squid</b>",
        message_html="검토할 내용",
        review_url=notification_payload()["review_url"],
        image_data_url=IMAGE_DATA_URL,
    )

    assert result["text_sent"] is True
    assert result["collaboration_configured"] is True
    assert result["collaboration_sent"] is True
    assert result["collaboration_photo_sent"] is True
    assert [
        call["data"]["chat_id"] if call["data"] else call["json"]["chat_id"]
        for call in fake.calls
    ] == [CHAT_ID, CHAT_ID, COLLABORATION_CHAT_ID, COLLABORATION_CHAT_ID]
    assert all(f"/bot{BOT_TOKEN}/" in call["url"] for call in fake.calls[:2])
    assert all(
        f"/bot{RELAY_BOT_TOKEN}/" in call["url"] for call in fake.calls[2:]
    )


@pytest.mark.asyncio
async def test_collaboration_failure_does_not_hide_primary_review_delivery(
    monkeypatch,
):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"ok": True, "result": {"message_id": 1}}),
        _FakeResponse(500, {"ok": False}),
    ])
    monkeypatch.setattr(
        "core.publishers.telegram_review.httpx.AsyncClient",
        lambda *args, **kwargs: fake,
    )

    result = await send_telegram_review(
        config=TelegramReviewConfig(BOT_TOKEN, CHAT_ID),
        collaboration_config=TelegramContentOpsRelayConfig(
            RELAY_BOT_TOKEN, COLLABORATION_CHAT_ID
        ),
        caption_html="검토 요청",
        message_html="검토할 내용",
        review_url=notification_payload()["review_url"],
    )

    assert result["sent"] is True
    assert result["text_sent"] is True
    assert result["collaboration_configured"] is True
    assert result["collaboration_sent"] is False


@pytest.fixture
def api_client(monkeypatch):
    from api import security, server

    monkeypatch.setattr(
        security,
        "API_KEYS",
        {"admin": ADMIN_KEY, "squid": CLIENT_KEY},
    )
    monkeypatch.setenv("TELEGRAM_REVIEW_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("TELEGRAM_REVIEW_CHAT_ID", CHAT_ID)
    monkeypatch.delenv("TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", raising=False)
    return TestClient(server.app)


def test_review_relay_is_admin_only_and_validates_targets(api_client, monkeypatch):
    from api import server

    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return {"sent": True, "photo_sent": True, "text_sent": True}

    monkeypatch.setattr(server, "send_telegram_review", fake_send)
    forbidden = api_client.post(
        "/review-notifications/telegram",
        headers={"x-api-key": CLIENT_KEY},
        json=notification_payload(),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "telegram_review_requires_admin_key"

    unsafe = api_client.post(
        "/review-notifications/telegram",
        headers={"x-api-key": ADMIN_KEY},
        json=notification_payload(
            review_url=f"https://attacker.example/?view=library&content={ITEM_ID}",
        ),
    )
    assert unsafe.status_code == 422

    response = api_client.post(
        "/review-notifications/telegram",
        headers={"x-api-key": ADMIN_KEY},
        json=notification_payload(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "sent": True,
        "photo_sent": True,
        "text_sent": True,
    }
    assert len(calls) == 1
    assert calls[0]["config"] == TelegramReviewConfig(BOT_TOKEN, CHAT_ID)
    assert calls[0]["collaboration_config"] is None
    assert calls[0]["image_data_url"] == IMAGE_DATA_URL
