from __future__ import annotations

import unittest
from pathlib import Path

from core.buzz.cli import (
    BuzzCliConfig,
    BuzzCliError,
    BuzzCliPublisher,
    CommandResult,
    buzz_message_fingerprints,
    format_origintrail_message,
)
from core.buzz.models import BuzzShadowEvent


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
    def test_fixed_message_contains_metadata_only_and_no_mentions(self):
        message = format_origintrail_message(
            _event(), studio_origin="https://console.example"
        )
        self.assertNotIn("@", message)
        self.assertNotIn("headline", message.lower())
        self.assertNotIn("prompt", message.lower())
        self.assertIn("Status: needs_review", message)
        self.assertIn("Measured cost: $0.002200", message)
        self.assertIn("https://console.example/?batch=", message)

    def test_request_fingerprint_binds_relay_channel_and_message(self):
        message = format_origintrail_message(
            _event(), studio_origin="https://console.example"
        )
        first = buzz_message_fingerprints(
            relay_url="https://buzz.example",
            channel_id=CHANNEL_ID,
            message=message,
        )
        self.assertEqual(first, buzz_message_fingerprints(
            relay_url="https://buzz.example/",
            channel_id=CHANNEL_ID,
            message=message,
        ))
        self.assertNotEqual(first, buzz_message_fingerprints(
            relay_url="https://other.example",
            channel_id=CHANNEL_ID,
            message=message,
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
        receipt = await publisher.send_once("fixed message")

        self.assertEqual(receipt.event_id, EVENT_ID)
        argv, stdin, env = runner.calls[0]
        self.assertEqual(argv, (
            "/opt/coineasy/bin/buzz", "messages", "send", "--channel",
            CHANNEL_ID, "--content", "-",
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
            await _publisher(runner).send_once("fixed message")

    async def test_nonzero_send_is_unknown_even_for_write_conflict(self):
        runner = FakeRunner([CommandResult(5, b"", b'{"error":"conflict"}')])
        with self.assertRaisesRegex(BuzzCliError, "buzz_delivery_unknown"):
            await _publisher(runner).send_once("fixed message")

    async def test_read_only_preflight_uses_exact_channel(self):
        runner = FakeRunner([CommandResult(0, b'{"id":"channel"}', b"")])
        await _publisher(runner).preflight()
        self.assertEqual(runner.calls[0][0], (
            "/opt/coineasy/bin/buzz", "channels", "get", "--channel", CHANNEL_ID,
        ))
        self.assertEqual(runner.calls[0][1], b"")


if __name__ == "__main__":
    unittest.main()
