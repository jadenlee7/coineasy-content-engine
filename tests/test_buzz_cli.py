from __future__ import annotations

import hashlib
import json
import struct
import unittest
from pathlib import Path

from core.buzz.cli import (
    BUZZ_CLI_RELEASE,
    BUZZ_OPERATIONS_RESPONSE_TEMPLATE_VERSION,
    BUZZ_REVIEW_ACK_TEMPLATE_VERSION,
    BuzzCliConfig,
    BuzzCliError,
    BuzzCliPublisher,
    BuzzCliReader,
    CommandResult,
    buzz_message_fingerprints,
    buzz_operations_reply_fingerprints,
    buzz_reply_fingerprints,
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
            _event(),
            studio_origin="https://console.example",
            attachment_sha256=_attachment().content_sha256,
        )
        self.assertNotIn("@", message)
        self.assertNotIn("prompt", message.lower())
        self.assertIn("OriginTrail 7월 업데이트", message)
        self.assertIn("DKG V10과 Buzz 통합", message)
        self.assertIn("상태: needs_review", message)
        self.assertIn("실측 비용: $0.002200", message)
        self.assertIn("https://console.example/?batch=", message)
        self.assertIn(
            f"검토 배너 SHA-256: {_attachment().content_sha256}", message
        )
        self.assertEqual(message.count("검토 배너 SHA-256: "), 1)
        self.assertIn("자동 발행: OFF", message)
        self.assertLessEqual(len(message.encode("utf-8")), 1_024)

    def test_long_korean_summary_is_truncated_on_utf8_boundary(self):
        event = _event()
        event = BuzzShadowEvent(
            **{**event.__dict__, "summary_ko": "검증 가능한 컨텍스트 " * 100}
        )
        message = format_origintrail_message(
            event,
            studio_origin="https://console.example",
            attachment_sha256=_attachment().content_sha256,
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
                event,
                studio_origin="https://console.example",
                attachment_sha256=_attachment().content_sha256,
            )

    def test_message_rejects_noncanonical_attachment_hash(self):
        for attachment_sha256 in ("A" * 64, "a" * 63, "not-a-hash"):
            with self.subTest(attachment_sha256=attachment_sha256):
                with self.assertRaisesRegex(
                    BuzzCliError, "buzz_delivery_request_invalid"
                ):
                    format_origintrail_message(
                        _event(),
                        studio_origin="https://console.example",
                        attachment_sha256=attachment_sha256,
                    )

    def test_request_fingerprint_binds_relay_channel_and_message(self):
        message = format_origintrail_message(
            _event(),
            studio_origin="https://console.example",
            attachment_sha256=_attachment().content_sha256,
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

    async def test_reply_uses_exact_text_only_argv_and_stdin(self):
        reply_to = "b" * 64
        message = "게시 승인: 원문·최종물 확인"
        runner = FakeRunner([CommandResult(
            0,
            json.dumps({
                "event_id": EVENT_ID,
                "accepted": True,
                # Buzz v0.5.4 exposes the relay acknowledgement here, not
                # the submitted content. The durable request fingerprint
                # already binds the exact reply body before this call.
                "message": "stored",
                "mention_pubkeys": [],
            }, ensure_ascii=False).encode(),
            b"",
        )])

        receipt = await _publisher(runner).send_reply_once(
            message, reply_to
        )

        self.assertEqual(receipt.event_id, EVENT_ID)
        argv, stdin, env = runner.calls[0]
        self.assertEqual(argv, (
            "/opt/coineasy/bin/buzz", "messages", "send", "--channel",
            CHANNEL_ID, "--content", "-", "--reply-to", reply_to,
        ))
        self.assertEqual(stdin, "게시 승인: 원문·최종물 확인".encode("utf-8"))
        self.assertFalse(any(
            argument in {"--file", "--broadcast", "--mention", "--mentions"}
            for argument in argv
        ))
        self.assertEqual(set(env), {"LANG", "BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY"})

    async def test_reply_rejects_invalid_text_or_target_before_cli(self):
        invalid_inputs = (
            ("", "b" * 64),
            ("a" * 1_025, "b" * 64),
            ("문의: owner@example.com", "b" * 64),
            (
                "승인 nostr:npub10elfcs4fr0l0r8af98jlmgdh9c8tcxjvz9qkw"
                "038js35mp4dma8qzvjptg",
                "b" * 64,
            ),
            (
                "승인 NOSTR:NPUB10ELFCS4FR0L0R8AF98JLMGDH9C8TCXJVZ9QKW"
                "038JS35MP4DMA8QZVJPTG",
                "b" * 64,
            ),
            ("\ud800", "b" * 64),
            ("valid", "B" * 64),
            ("valid", "b" * 63),
            ("valid", "not-an-event-id"),
        )
        for message, reply_to in invalid_inputs:
            with self.subTest(message=repr(message), reply_to=reply_to):
                runner = FakeRunner([])
                with self.assertRaisesRegex(
                    BuzzCliError, "buzz_delivery_request_invalid"
                ):
                    await _publisher(runner).send_reply_once(message, reply_to)
                self.assertEqual(runner.calls, [])

    async def test_reply_utf8_limit_is_measured_in_bytes(self):
        reply_to = "b" * 64
        accepted_message = "가" * 341 + "a"
        accepted_runner = FakeRunner([CommandResult(
            0,
            json.dumps({
                "event_id": EVENT_ID,
                "accepted": True,
                "message": accepted_message,
                "mention_pubkeys": [],
            }).encode(),
            b"",
        )])
        await _publisher(accepted_runner).send_reply_once(
            accepted_message, reply_to
        )
        self.assertEqual(len(accepted_runner.calls[0][1]), 1_024)

        rejected_runner = FakeRunner([])
        with self.assertRaisesRegex(
            BuzzCliError, "buzz_delivery_request_invalid"
        ):
            await _publisher(rejected_runner).send_reply_once(
                "가" * 342, reply_to
            )
        self.assertEqual(rejected_runner.calls, [])

    async def test_reply_nonzero_or_invalid_output_is_unknown(self):
        invalid_results = (
            CommandResult(5, b"", b'{"error":"conflict"}'),
            CommandResult(0, b"not-json", b""),
            CommandResult(0, json.dumps([]).encode(), b""),
            CommandResult(0, json.dumps({
                "event_id": EVENT_ID,
                "accepted": False,
                "message": "not accepted",
                "mention_pubkeys": [],
            }).encode(), b""),
            CommandResult(0, json.dumps({
                "event_id": "invalid",
                "accepted": True,
                "message": "ok",
                "mention_pubkeys": [],
            }).encode(), b""),
            CommandResult(0, json.dumps({
                "event_id": EVENT_ID,
                "accepted": True,
                "message": "ok",
                "mention_pubkeys": ["unexpected"],
            }).encode(), b""),
            CommandResult(0, json.dumps({
                "event_id": EVENT_ID,
                "accepted": True,
                "message": 7,
                "mention_pubkeys": [],
            }).encode(), b""),
        )
        for result in invalid_results:
            with self.subTest(result=result):
                runner = FakeRunner([result])
                with self.assertRaisesRegex(
                    BuzzCliError, "buzz_delivery_unknown"
                ):
                    await _publisher(runner).send_reply_once(
                        "fixed reply", "b" * 64
                    )
                self.assertEqual(len(runner.calls), 1)

    def test_reply_fingerprint_binds_durable_send_identity(self):
        message = "게시 승인: 원문·최종물 확인"
        reply_to = "b" * 64
        first = buzz_reply_fingerprints(
            relay_url="https://buzz.example/",
            channel_id=CHANNEL_ID,
            service_pubkey="c" * 64,
            release_sha="a" * 40,
            reply_to=reply_to,
            message=message,
        )
        self.assertEqual(BUZZ_REVIEW_ACK_TEMPLATE_VERSION, "origintrail-buzz-review-ack@1")
        self.assertEqual(first, buzz_reply_fingerprints(
            relay_url="https://buzz.example",
            channel_id=CHANNEL_ID,
            service_pubkey="c" * 64,
            release_sha="a" * 40,
            reply_to=reply_to,
            message=message,
        ))
        self.assertNotEqual(first, buzz_reply_fingerprints(
            relay_url="https://buzz.example",
            channel_id=CHANNEL_ID,
            service_pubkey="d" * 64,
            release_sha="a" * 40,
            reply_to=reply_to,
            message=message,
        ))
        self.assertNotEqual(first, buzz_reply_fingerprints(
            relay_url="https://buzz.example",
            channel_id=CHANNEL_ID,
            service_pubkey="c" * 64,
            release_sha="b" * 40,
            reply_to=reply_to,
            message=message,
        ))

    def test_operations_reply_has_a_separate_release_bound_domain(self):
        values = {
            "relay_url": "https://buzz.example",
            "channel_id": CHANNEL_ID,
            "service_pubkey": "c" * 64,
            "release_sha": "a" * 40,
            "reply_to": "b" * 64,
            "message": "CoinEasy 운영 상태\n자동 발행: OFF",
        }
        operations = buzz_operations_reply_fingerprints(**values)
        review = buzz_reply_fingerprints(**values)
        self.assertEqual(
            BUZZ_OPERATIONS_RESPONSE_TEMPLATE_VERSION,
            "origintrail-buzz-operations-response@1",
        )
        self.assertNotEqual(operations, review)
        self.assertNotEqual(operations, buzz_operations_reply_fingerprints(
            **{**values, "release_sha": "d" * 40}
        ))

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

    async def test_thread_reader_parses_production_shaped_full_json(self):
        root = "b" * 64
        reply = "c" * 64
        reviewer = "d" * 64
        service = "e" * 64
        attachment = _attachment()
        attachment_sha256 = attachment.content_sha256
        media_url = f"https://buzz.example/media/{attachment_sha256}.png"
        base_content = format_origintrail_message(
            _event(),
            studio_origin="https://console.example",
            attachment_sha256=attachment_sha256,
        )
        payload = json.dumps([
            {
                "id": root,
                "pubkey": service,
                "kind": 9,
                "content": f"{base_content}\n![image]({media_url})",
                "created_at": 1_786_099_900,
                "tags": [
                    ["h", CHANNEL_ID],
                    [
                        "imeta",
                        f"url {media_url}",
                        "m image/png",
                        f"x {attachment_sha256}",
                        f"size {len(attachment.content)}",
                        "dim 1200x630",
                        "blurhash LKO2?U%2Tw=w]~RBVZRi};RPxuwH",
                        (
                            "thumb https://buzz.example/media/"
                            f"{attachment_sha256}.thumb.jpg"
                        ),
                    ],
                ],
            },
            {
                "id": reply,
                "pubkey": reviewer,
                "kind": 9,
                "content": "게시 승인: 원문·최종물 확인",
                "created_at": 1_786_100_000,
                "tags": [["h", CHANNEL_ID], ["e", root, "", "reply"]],
            },
        ]).encode()
        runner = FakeRunner([CommandResult(0, payload, b"")])
        reader = BuzzCliReader(_publisher(runner).config, runner=runner)
        messages = await reader.read_thread(root)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].event_id, root)
        self.assertEqual(messages[0].kind, 9)
        self.assertEqual(messages[0].tags[1][0], "imeta")
        self.assertEqual(messages[1].content, "게시 승인: 원문·최종물 확인")
        self.assertEqual(messages[1].tags[1], ("e", root, "", "reply"))
        self.assertEqual(runner.calls[0][0], (
            "/opt/coineasy/bin/buzz", "--format", "json", "messages",
            "thread", "--channel", CHANNEL_ID, "--event", root,
            "--limit", "100", "--depth-limit", "8",
        ))
        self.assertEqual(runner.calls[0][1], b"")

    async def test_thread_reader_rejects_malformed_normalized_events(self):
        runner = FakeRunner([CommandResult(
            0,
            json.dumps([{
                "id": "b" * 64,
                "pubkey": "d" * 64,
                "kind": 9,
                "content": "게시 승인: 원문·최종물 확인",
                "created_at": 1_786_100_000,
                "tags": [],
                "sig": "must-not-be-present",
            }]).encode(),
            b"",
        )])
        reader = BuzzCliReader(_publisher(runner).config, runner=runner)
        with self.assertRaisesRegex(BuzzCliError, "buzz_review_thread_invalid"):
            await reader.read_thread("b" * 64)

    async def test_thread_reader_rejects_lone_surrogates_fail_closed(self):
        valid = {
            "id": "b" * 64,
            "pubkey": "d" * 64,
            "kind": 9,
            "content": "게시 승인: 원문·최종물 확인",
            "created_at": 1_786_100_000,
            "tags": [["h", CHANNEL_ID]],
        }
        malformed_values = (
            {**valid, "content": "\ud800"},
            {**valid, "tags": [["h", "\ud800"]]},
        )
        for malformed in malformed_values:
            with self.subTest(malformed=malformed):
                runner = FakeRunner([CommandResult(
                    0, json.dumps([malformed]).encode(), b""
                )])
                reader = BuzzCliReader(_publisher(runner).config, runner=runner)
                with self.assertRaisesRegex(
                    BuzzCliError, "buzz_review_thread_invalid"
                ):
                    await reader.read_thread("b" * 64)

    async def test_channel_reader_uses_bounded_kind_nine_query(self):
        event = {
            "id": "b" * 64,
            "pubkey": "d" * 64,
            "kind": 9,
            "content": "오늘 기획",
            "created_at": 1_786_100_000,
            "tags": [["h", CHANNEL_ID]],
        }
        runner = FakeRunner([CommandResult(
            0, json.dumps([event]).encode(), b""
        )])
        reader = BuzzCliReader(_publisher(runner).config, runner=runner)
        messages = await reader.read_channel(
            since_epoch=1_786_099_000, limit=25
        )
        self.assertEqual(messages[0].content, "오늘 기획")
        self.assertEqual(runner.calls[0][0], (
            "/opt/coineasy/bin/buzz", "--format", "json", "messages",
            "get", "--channel", CHANNEL_ID, "--since", "1786099000",
            "--limit", "25", "--kinds", "9",
        ))
        self.assertEqual(runner.calls[0][1], b"")

    async def test_channel_reader_rejects_invalid_bounds_without_io(self):
        reader = BuzzCliReader(
            _publisher(FakeRunner([])).config, runner=FakeRunner([])
        )
        for since_epoch, limit in ((0, 1), (True, 1), (1, 0), (1, 101)):
            with self.subTest(since_epoch=since_epoch, limit=limit):
                with self.assertRaisesRegex(
                    BuzzCliError, "buzz_operations_request_invalid"
                ):
                    await reader.read_channel(
                        since_epoch=since_epoch, limit=limit
                    )

    async def test_channel_reader_rejects_malformed_event_fail_closed(self):
        runner = FakeRunner([CommandResult(
            0,
            json.dumps([{
                "id": "b" * 64,
                "pubkey": "d" * 64,
                "kind": 9,
                "content": "\ud800",
                "created_at": 1_786_100_000,
                "tags": [["h", CHANNEL_ID]],
            }]).encode(),
            b"",
        )])
        reader = BuzzCliReader(_publisher(runner).config, runner=runner)
        with self.assertRaisesRegex(
            BuzzCliError, "buzz_operations_channel_invalid"
        ):
            await reader.read_channel(since_epoch=1_786_099_000)


class RunCommandTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_timed_out_subprocess_is_killed_before_raising(self):
        import asyncio
        from unittest import mock

        from core.buzz import cli as cli_module

        class HungProcess:
            def __init__(self):
                self.killed = False
                self.waited = False
                self.returncode = None

            async def communicate(self, stdin):
                await asyncio.sleep(3600)

            def kill(self):
                self.killed = True

            async def wait(self):
                self.waited = True
                return -9

        hung = HungProcess()

        async def fake_exec(*argv, **kwargs):
            return hung

        with (
            mock.patch("asyncio.create_subprocess_exec", fake_exec),
            mock.patch.object(cli_module, "_PROCESS_TIMEOUT_SECONDS", 0.01),
        ):
            with self.assertRaisesRegex(BuzzCliError, "buzz_cli_preflight_failed"):
                await cli_module._run_command(
                    ("/opt/coineasy/bin/buzz", "channels", "get"),
                    stdin=b"",
                    env={},
                )
        self.assertTrue(hung.killed)
        self.assertTrue(hung.waited)


if __name__ == "__main__":
    unittest.main()
