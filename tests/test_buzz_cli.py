from __future__ import annotations

import hashlib
import struct
import unittest
from pathlib import Path

from core.buzz.cli import (
    BUZZ_CLI_RELEASE,
    BuzzCliConfig,
    BuzzCliError,
    BuzzCliPublisher,
    CommandResult,
    buzz_message_fingerprints,
    format_origintrail_message,
)
from core.buzz.models import BuzzAttachment, BuzzShadowEvent


CHANNEL_ID = "33333333-3333-4333-8333-333333333333"
EVENT_ID = "a" * 64


def _event() -> BuzzShadowEvent:
    job_id = "22222222-2222-4222-8222-222222222222"
    return BuzzShadowEvent(
        event_id=EVENT_ID,
        event_type="origintrail.batch_review_ready.v1",
        job_id=job_id,
        review_ref=f"batch:{job_id}",
        client_id="origintrail",
        agent_id="origintrail_client_agent",
        workflow_kind="official_source_nonurgent_pack",
        result_code="needs_review",
        model_tier="S",
        actual_cost_microusd=2200,
        finished_at="2026-08-03T12:00:00.000Z",
        source_url="https://x.com/origin_trail/status/2082883998829752783",
        studio_review_path=f"/?batch={job_id}",
        headline_ko="OriginTrail 7월 업데이트",
        summary_ko=(
            "DKG V10과 Buzz 통합, 검증 가능한 출처의 핵심 내용을 "
            "정리했습니다."
        ),
    )


def _attachment(content: bytes | None = None) -> BuzzAttachment:
    job_id = "22222222-2222-4222-8222-222222222222"
    png = content or (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", 1_200, 630)
    )
    return BuzzAttachment(
        filename=f"origintrail-review-{job_id}.png",
        media_type="image/png",
        content_sha256=hashlib.sha256(png).hexdigest(),
        content=png,
    )


class FakeRunner:
    def __init__(self, results: list[CommandResult]):
        self.results = results
        self.calls: list[tuple[tuple[str, ...], bytes, dict[str, str]]] = []

    async def __call__(self, argv, *, stdin, env):
        self.calls.append((argv, stdin, env))
        return self.results.pop(0)


def _publisher(runner: FakeRunner) -> BuzzCliPublisher:
    return BuzzCliPublisher(
        BuzzCliConfig(
            cli_path=Path("/opt/coineasy/bin/buzz"),
            relay_url="https://buzz.example",
            private_key="1" * 64,
            auth_tag=None,
            channel_id=CHANNEL_ID,
        ),
        runner=runner,
    )


class BuzzCliTests(unittest.IsolatedAsyncioTestCase):
    def test_request_fingerprint_is_bound_to_reviewed_cli_release(self):
        self.assertEqual(BUZZ_CLI_RELEASE, "desktop-v0.5.4")

    def test_fixed_message_contains_bounded_preview_and_no_mentions(self):
        message = format_origintrail_message(
            _event(), studio_origin="https://console.example"
        )
        self.assertNotIn("@", message)
        self.assertNotIn("prompt", message.lower())
        self.assertIn("OriginTrail 7월 업데이트", message)
        self.assertIn("DKG V10과 Buzz 통합", message)
        self.assertIn("상태: needs_review", message)
        self.assertIn("실측 비용: $0.002200", message)
        self.assertIn("https://console.example/?batch=", message)
        self.assertIn("자동 발행: OFF", message)
        self.assertLessEqual(len(message.encode("utf-8")), 1_024)

    def test_long_korean_summary_is_truncated_on_utf8_boundary(self):
        event = _event()
        event = BuzzShadowEvent(
            **{**event.__dict__, "summary_ko": "검증 가능한 컨텍스트 " * 100}
        )
        message = format_origintrail_message(
            event, studio_origin="https://console.example"
        )
        self.assertLessEqual(len(message.encode("utf-8")), 1_024)
        self.assertIn("…", message)
        message.encode("utf-8").decode("utf-8")

    def test_preview_mentions_fail_closed_before_relay(self):
        event = _event()
        event = BuzzShadowEvent(
            **{**event.__dict__, "summary_ko": "문의: owner@example.com"}
        )
        with self.assertRaisesRegex(
            BuzzCliError, "buzz_delivery_request_invalid"
        ):
            format_origintrail_message(
                event, studio_origin="https://console.example"
            )

    def test_request_fingerprint_binds_relay_channel_and_message(self):
        message = format_origintrail_message(
            _event(), studio_origin="https://console.example"
        )
        first = buzz_message_fingerprints(
            relay_url="https://buzz.example",
            channel_id=CHANNEL_ID,
            message=message,
            attachment=_attachment(),
        )
        self.assertEqual(first, buzz_message_fingerprints(
            relay_url="https://buzz.example/",
            channel_id=CHANNEL_ID,
            message=message,
            attachment=_attachment(),
        ))
        self.assertNotEqual(first, buzz_message_fingerprints(
            relay_url="https://other.example",
            channel_id=CHANNEL_ID,
            message=message,
            attachment=_attachment(),
        ))
        self.assertNotEqual(first, buzz_message_fingerprints(
            relay_url="https://buzz.example",
            channel_id=CHANNEL_ID,
            message=message,
            attachment=_attachment(
                b"\x89PNG\r\n\x1a\n"
                + b"\x00\x00\x00\rIHDR"
                + struct.pack(">II", 1_200, 630)
                + b"changed"
            ),
        ))

    async def test_send_uses_exact_argv_stdin_and_minimal_secret_environment(self):
        runner = FakeRunner([CommandResult(
            0,
            (
                '{"event_id":"' + EVENT_ID
                + '","accepted":true,"message":"ok","mention_pubkeys":[]}'
            ).encode(),
            b"",
        )])
        publisher = _publisher(runner)
        receipt = await publisher.send_once("fixed message", _attachment())

        self.assertEqual(receipt.event_id, EVENT_ID)
        argv, stdin, env = runner.calls[0]
        self.assertEqual(argv[:7], (
            "/opt/coineasy/bin/buzz", "messages", "send", "--channel",
            CHANNEL_ID, "--content", "-",
        ))
        self.assertEqual(argv[7], "--file")
        self.assertTrue(argv[8].endswith(
            "/origintrail-review-22222222-2222-4222-8222-222222222222.png"
        ))
        self.assertEqual(stdin, b"fixed message")
        self.assertEqual(set(env), {"LANG", "BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY"})
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)

    async def test_success_with_mentions_is_treated_as_unknown(self):
        runner = FakeRunner([CommandResult(
            0,
            (
                '{"event_id":"' + EVENT_ID
                + '","accepted":true,"message":"ok","mention_pubkeys":["x"]}'
            ).encode(),
            b"",
        )])
        with self.assertRaisesRegex(BuzzCliError, "buzz_delivery_unknown"):
            await _publisher(runner).send_once("fixed message", _attachment())

    async def test_nonzero_send_is_unknown_even_for_write_conflict(self):
        runner = FakeRunner([CommandResult(5, b"", b'{"error":"conflict"}')])
        with self.assertRaisesRegex(BuzzCliError, "buzz_delivery_unknown"):
            await _publisher(runner).send_once("fixed message", _attachment())

    async def test_invalid_attachment_fails_before_invoking_buzz(self):
        runner = FakeRunner([])
        invalid = BuzzAttachment(
            filename="banner.svg",
            media_type="image/svg+xml",
            content_sha256="a" * 64,
            content=b"<svg/>",
        )
        with self.assertRaisesRegex(
            BuzzCliError, "buzz_delivery_attachment_invalid"
        ):
            await _publisher(runner).send_once("fixed message", invalid)
        self.assertEqual(runner.calls, [])

    async def test_read_only_preflight_uses_exact_channel(self):
        runner = FakeRunner([CommandResult(0, b'{"id":"channel"}', b"")])
        await _publisher(runner).preflight()
        self.assertEqual(runner.calls[0][0], (
            "/opt/coineasy/bin/buzz", "channels", "get", "--channel", CHANNEL_ID,
        ))
        self.assertEqual(runner.calls[0][1], b"")


if __name__ == "__main__":
    unittest.main()
