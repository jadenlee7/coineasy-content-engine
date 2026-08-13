import asyncio
import base64
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from core.publishers.telegram_review import (
    TelegramContentOpsRelayConfig,
    TelegramReviewConfig,
    build_telegram_grok_qa_message,
    decode_review_image_data_url,
    send_telegram_grok_qa_verdict,
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
GROK_QA_RELAY_TOKEN = "grok-qa-relay-dedicated-token-20260813"


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


def grok_qa_payload(**overrides):
    payload = {
        "content_item_id": ITEM_ID,
        "content_version_id": VERSION_ID,
        "client_id": "squid",
        "content_kind": "daily_news",
        "title": "Squid가 Telegram에서 열렸어요",
        "decision": "PASS",
        "summary": "공식 원문과 한국어 문구, Squid 브랜드 표현이 모두 일치합니다.",
        "fact_check": {
            "status": "PASS",
            "checks": ["공식 X 원문의 Telegram 공개 사실을 확인했습니다."],
            "source_urls": [
                "https://x.com/squidrouter/status/2083266484789514640"
            ],
        },
        "brand_check": {
            "status": "PASS",
            "checks": ["Squid 공식 명칭과 절제된 문장 구조를 확인했습니다."],
        },
        "issues": [],
        "next_action": "ready_for_human_approval",
        "review_url": (
            "https://coineasy-newscard.netlify.app/"
            f"?view=library&content={ITEM_ID}"
        ),
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


def test_grok_qa_message_is_bounded_escaped_and_explicitly_advisory():
    message = build_telegram_grok_qa_message(
        client_id="squid",
        content_kind="daily_news",
        title="<script>Squid</script>",
        content_item_id=ITEM_ID,
        content_version_id=VERSION_ID,
        decision="WARN",
        summary="표현 한 곳을 사람이 다시 확인해야 합니다.",
        fact_status="PASS",
        fact_checks=["공식 X 원문과 핵심 사실이 일치합니다."],
        source_urls=[
            "https://x.com/squidrouter/status/2083266484789514640"
        ],
        brand_status="WARN",
        brand_checks=["한국어 간격을 한 번 더 확인해야 합니다."],
        issues=[{
            "severity": "WARN",
            "code": "brand_spacing",
            "message": "헤드라인 여백을 확인하세요.",
        }],
        next_action="revise_banner",
    )
    assert "CoinEasy Grok QA · 자문 판정" in message
    assert "자동 승인/발행 아님" in message
    assert "&lt;script&gt;Squid&lt;/script&gt;" in message
    assert "최종 승인과 공개 발행은 사람이" in message
    assert "<script>" not in message
    assert len(message) <= 4_096


@pytest.mark.asyncio
async def test_grok_qa_sender_uses_only_the_content_ops_room(monkeypatch):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"ok": True, "result": {"message_id": 8}}),
        _FakeResponse(200, {"ok": True, "result": {"message_id": 9}}),
    ])
    monkeypatch.setattr(
        "core.publishers.telegram_review.httpx.AsyncClient",
        lambda *args, **kwargs: fake,
    )
    sent = await send_telegram_grok_qa_verdict(
        config=TelegramContentOpsRelayConfig(
            RELAY_BOT_TOKEN, COLLABORATION_CHAT_ID
        ),
        message_html="🤖 <b>CoinEasy Grok QA</b>",
        review_url=grok_qa_payload()["review_url"],
        image_data_url=IMAGE_DATA_URL,
    )
    assert sent == "sent"
    assert len(fake.calls) == 2
    assert f"/bot{RELAY_BOT_TOKEN}/sendPhoto" in fake.calls[0]["url"]
    assert fake.calls[0]["data"]["chat_id"] == COLLABORATION_CHAT_ID
    assert fake.calls[0]["files"]["photo"][1] == PNG_BYTES
    assert "자동 승인/발행 아님" in fake.calls[0]["data"]["caption"]
    assert f"/bot{RELAY_BOT_TOKEN}/sendMessage" in fake.calls[1]["url"]
    assert fake.calls[1]["json"]["chat_id"] == COLLABORATION_CHAT_ID
    assert BOT_TOKEN not in fake.calls[0]["url"]


@pytest.mark.asyncio
async def test_grok_qa_sender_never_retries_after_partial_packet(monkeypatch):
    fake = _FakeAsyncClient([
        _FakeResponse(200, {"ok": True, "result": {"message_id": 8}}),
        _FakeResponse(400, {"ok": False, "description": "message rejected"}),
    ])
    monkeypatch.setattr(
        "core.publishers.telegram_review.httpx.AsyncClient",
        lambda *args, **kwargs: fake,
    )
    outcome = await send_telegram_grok_qa_verdict(
        config=TelegramContentOpsRelayConfig(
            RELAY_BOT_TOKEN, COLLABORATION_CHAT_ID
        ),
        message_html="🤖 <b>CoinEasy Grok QA</b>",
        review_url=grok_qa_payload()["review_url"],
        image_data_url=IMAGE_DATA_URL,
    )
    assert outcome == "delivery_unknown"
    assert [call["url"].rsplit("/", 1)[-1] for call in fake.calls] == [
        "sendPhoto",
        "sendMessage",
    ]


def test_grok_qa_relay_is_admin_only_private_and_never_publishes(monkeypatch):
    from api import server

    monkeypatch.setenv("GROK_QA_RELAY_TOKEN", GROK_QA_RELAY_TOKEN)
    monkeypatch.setenv("API_SECRET", ADMIN_KEY)
    monkeypatch.setenv("TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", RELAY_BOT_TOKEN)
    monkeypatch.setenv(
        "TELEGRAM_CONTENT_OPS_RELAY_CHAT_ID", COLLABORATION_CHAT_ID
    )
    calls = []

    async def fake_send(**kwargs):
        calls.append(kwargs)
        return "sent"

    monkeypatch.setattr(server, "send_telegram_grok_qa_verdict", fake_send)
    client = TestClient(server.app)
    forbidden = client.post(
        "/internal/grok-qa-verdict",
        headers={"x-api-key": CLIENT_KEY},
        json=grok_qa_payload(),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "grok_qa_verdict_requires_relay_token"

    response = client.post(
        "/internal/grok-qa-verdict",
        headers={"x-grok-qa-relay-token": GROK_QA_RELAY_TOKEN},
        json=grok_qa_payload(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "sent": True,
        "advisory_only": True,
        "public_publish": False,
    }
    assert len(calls) == 1
    assert calls[0]["config"] == TelegramContentOpsRelayConfig(
        RELAY_BOT_TOKEN, COLLABORATION_CHAT_ID
    )
    assert calls[0]["image_data_url"] == IMAGE_DATA_URL
    assert "publish" not in calls[0]
    assert "typefully" not in calls[0]


def test_grok_qa_relay_auth_precedes_body_validation_and_image_decode(monkeypatch):
    from api import server

    monkeypatch.setenv("GROK_QA_RELAY_TOKEN", GROK_QA_RELAY_TOKEN)
    decode_calls = []

    def decode_spy(value):
        decode_calls.append(value)
        raise AssertionError("unauthenticated body reached image decode")

    monkeypatch.setattr(server, "decode_review_image_data_url", decode_spy)
    client = TestClient(server.app)
    invalid_body = json.dumps(grok_qa_payload(
        image_data_url="data:image/png;base64," + ("A" * 32),
    )).encode("utf-8")

    missing = client.post(
        "/api/internal/grok-qa-verdict",
        content=invalid_body,
        headers={"content-type": "application/json"},
    )
    wrong = client.post(
        "/internal/grok-qa-verdict",
        content=invalid_body,
        headers={
            "content-type": "application/json",
            "x-grok-qa-relay-token": "x" * len(GROK_QA_RELAY_TOKEN),
        },
    )
    unauthenticated_oversize = client.post(
        "/internal/grok-qa-verdict",
        content=b"x" * (server.GROK_QA_VERDICT_MAX_BODY_BYTES + 1),
    )

    statuses = [
        missing.status_code,
        wrong.status_code,
        unauthenticated_oversize.status_code,
    ]
    assert statuses == [
        403,
        403,
        403,
    ]
    assert decode_calls == []

    # With the exact credential, FastAPI/Pydantic owns schema validation. The
    # same invalid body therefore reaches validation and returns 422.
    monkeypatch.setattr(
        server,
        "decode_review_image_data_url",
        lambda value: decode_calls.append(value) or None,
    )
    authenticated = client.post(
        "/internal/grok-qa-verdict",
        content=invalid_body,
        headers={
            "content-type": "application/json",
            "x-grok-qa-relay-token": GROK_QA_RELAY_TOKEN,
        },
    )
    assert authenticated.status_code == 422
    assert len(decode_calls) == 1

    monkeypatch.setattr(server, "GROK_QA_VERDICT_MAX_BODY_BYTES", 64)
    authenticated_oversize = client.post(
        "/internal/grok-qa-verdict",
        content=b"x" * (server.GROK_QA_VERDICT_MAX_BODY_BYTES + 1),
        headers={"x-grok-qa-relay-token": GROK_QA_RELAY_TOKEN},
    )
    assert authenticated_oversize.status_code == 413
    assert authenticated_oversize.json()["detail"] == (
        "grok_qa_verdict_body_too_large"
    )
    assert len(decode_calls) == 1


def test_grok_qa_relay_rejects_oversize_and_chunked_before_validation(monkeypatch):
    from api import server

    monkeypatch.setenv("GROK_QA_RELAY_TOKEN", GROK_QA_RELAY_TOKEN)
    decode_calls = []
    monkeypatch.setattr(
        server,
        "decode_review_image_data_url",
        lambda value: decode_calls.append(value) or None,
    )
    monkeypatch.setattr(server, "GROK_QA_VERDICT_MAX_BODY_BYTES", 64)

    class GateProbe:
        called = False

        async def __call__(self, scope, receive, send):
            self.called = True
            raise AssertionError("rejected body reached FastAPI")

    async def request_gate(headers, messages):
        downstream = GateProbe()
        gate = server._GrokQaVerdictRequestGate(downstream)
        sent = []
        remaining = list(messages)

        async def receive():
            return remaining.pop(0)

        async def send(message):
            sent.append(message)

        await gate(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/internal/grok-qa-verdict",
                "headers": headers,
            },
            receive,
            send,
        )
        return downstream.called, sent

    auth_header = (b"x-grok-qa-relay-token", GROK_QA_RELAY_TOKEN.encode())
    oversize_called, oversize_sent = asyncio.run(request_gate(
        [
            auth_header,
            (
                b"content-length",
                str(server.GROK_QA_VERDICT_MAX_BODY_BYTES + 1).encode(),
            ),
        ],
        [],
    ))
    streamed_oversize_called, streamed_oversize_sent = asyncio.run(
        request_gate(
            [auth_header, (b"content-length", b"64")],
            [{
                "type": "http.request",
                "body": b"x" * 65,
                "more_body": False,
            }],
        )
    )
    chunked_called, chunked_sent = asyncio.run(request_gate(
        [
            auth_header,
            (b"transfer-encoding", b"chunked"),
        ],
        [{"type": "http.request", "body": b"{}", "more_body": False}],
    ))
    missing_length_called, missing_length_sent = asyncio.run(request_gate(
        [auth_header],
        [{"type": "http.request", "body": b"{}", "more_body": False}],
    ))

    assert oversize_called is False
    assert oversize_sent[0]["status"] == 413
    assert streamed_oversize_called is False
    assert streamed_oversize_sent[0]["status"] == 413
    assert chunked_called is False
    assert chunked_sent[0]["status"] == 400
    assert missing_length_called is False
    assert missing_length_sent[0]["status"] == 411
    assert decode_calls == []


def test_grok_qa_rejects_inconsistent_pass_and_external_review_target(monkeypatch):
    from api import server

    monkeypatch.setenv("GROK_QA_RELAY_TOKEN", GROK_QA_RELAY_TOKEN)
    client = TestClient(server.app)
    inconsistent = client.post(
        "/internal/grok-qa-verdict",
        headers={"x-grok-qa-relay-token": GROK_QA_RELAY_TOKEN},
        json=grok_qa_payload(issues=[{
            "severity": "WARN",
            "code": "source_gap",
            "message": "확인이 필요합니다.",
        }]),
    )
    assert inconsistent.status_code == 422

    external = client.post(
        "/internal/grok-qa-verdict",
        headers={"x-grok-qa-relay-token": GROK_QA_RELAY_TOKEN},
        json=grok_qa_payload(
            review_url=f"https://attacker.example/?view=library&content={ITEM_ID}"
        ),
    )
    assert external.status_code == 422

    missing_banner = grok_qa_payload()
    missing_banner.pop("image_data_url")
    missing = client.post(
        "/internal/grok-qa-verdict",
        headers={"x-grok-qa-relay-token": GROK_QA_RELAY_TOKEN},
        json=missing_banner,
    )
    assert missing.status_code == 422

    invalid = client.post(
        "/internal/grok-qa-verdict",
        headers={"x-grok-qa-relay-token": GROK_QA_RELAY_TOKEN},
        json=grok_qa_payload(image_data_url="data:image/png;base64,bm90LXBuZw=="),
    )
    assert invalid.status_code == 422
