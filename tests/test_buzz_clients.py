from __future__ import annotations

import hashlib
import json
import struct
import unittest

import httpx

from core.buzz.clients import (
    BuzzDeliveryControlClient,
    BuzzReviewControlClient,
    BuzzShadowClient,
)
from core.buzz.errors import BuzzAdapterError
from core.buzz.models import BuzzReviewDecision, BuzzReviewTarget, BuzzShadowEvent


JOB_ID = "22222222-2222-4222-8222-222222222222"
TOKEN = "shadow-token-that-is-dedicated-and-long-enough"
ACK_MESSAGE = (
    "✅ 게시 승인 접수\n"
    "원문·최종물 확인 결정을 기록했습니다.\n\n"
    "현재 상태: 검토 결정 기록 완료\n"
    "자동 발행: OFF"
)


def _event() -> BuzzShadowEvent:
    return BuzzShadowEvent(
        event_id="a" * 64,
        event_type="origintrail.batch_review_ready.v1",
        job_id=JOB_ID,
        review_ref=f"batch:{JOB_ID}",
        client_id="origintrail",
        agent_id="origintrail_client_agent",
        workflow_kind="official_source_nonurgent_pack",
        result_code="needs_review",
        model_tier="S",
        actual_cost_microusd=2200,
        finished_at="2026-08-03T12:00:00.000Z",
        source_url="https://x.com/origin_trail/status/2082883998829752783",
        studio_review_path=f"/?batch={JOB_ID}",
        headline_ko="OriginTrail 업데이트",
        summary_ko="검토용 요약",
    )


def _png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", 1_200, 630)
    )


def _review_decision() -> BuzzReviewDecision:
    target = BuzzReviewTarget(
        workspace_id="11111111-1111-4111-8111-111111111111",
        job_id=JOB_ID,
        delivery_event_id="a" * 64,
        channel_id="33333333-3333-4333-8333-333333333333",
        root_relay_event_id="b" * 64,
        message_sha256="f" * 64,
        protocol_version="origintrail-buzz-review@2",
        delivered_at_epoch=1_786_100_000,
    )
    return BuzzReviewDecision(
        target=target,
        decision_event_id="c" * 64,
        reviewer_pubkey="d" * 64,
        decision="approved",
        reason=None,
        command_sha256="e" * 64,
        command_created_at_epoch=1_786_100_100,
    )


def _review_record_response(
    decision: BuzzReviewDecision,
    *,
    reused: bool,
) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "mode": "publish_intent_review",
        "workspace_id": decision.target.workspace_id,
        "job_id": decision.target.job_id,
        "delivery_event_id": decision.target.delivery_event_id,
        "channel_id": decision.target.channel_id,
        "root_relay_event_id": decision.target.root_relay_event_id,
        "message_sha256": decision.target.message_sha256,
        "protocol_version": decision.target.protocol_version,
        "decision_event_id": decision.decision_event_id,
        "reviewer_pubkey": decision.reviewer_pubkey,
        "decision": decision.decision,
        "reason": decision.reason,
        "command_sha256": decision.command_sha256,
        "command_created_at_epoch": decision.command_created_at_epoch,
        "reused": reused,
    }


def _ack_object(
    *,
    status: str = "claimed",
    claim_granted: bool = True,
    request_sha256: str | None = None,
    delivery_started_at_epoch: int | None = None,
    relay_event_id: str | None = None,
) -> dict[str, object]:
    return {
        "job_id": JOB_ID,
        "channel_id": "33333333-3333-4333-8333-333333333333",
        "root_relay_event_id": "b" * 64,
        "decision_event_id": "c" * 64,
        "decision": "approved",
        "reason": None,
        "command_created_at_epoch": 1_786_100_100,
        "template_version": "origintrail-buzz-review-ack@1",
        "message": ACK_MESSAGE,
        "status": status,
        "claim_granted": claim_granted,
        "reused": False,
        "message_sha256": hashlib.sha256(ACK_MESSAGE.encode()).hexdigest(),
        "request_sha256": request_sha256,
        "delivery_started_at_epoch": delivery_started_at_epoch,
        "relay_event_id": relay_event_id,
    }


class BuzzShadowBannerClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_exact_authenticated_bounded_png(self):
        content = _png()
        digest = hashlib.sha256(content).hexdigest()

        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url),
                "https://console.example/api/buzz-shadow/origintrail/batch/"
                f"{JOB_ID}/banner.png",
            )
            self.assertEqual(request.headers["x-coineasy-buzz-key"], TOKEN)
            return httpx.Response(200, headers={
                "content-type": "image/png",
                "content-length": str(len(content)),
                "content-disposition": (
                    f'inline; filename="origintrail-review-{JOB_ID}.png"'
                ),
                "x-coineasy-content-sha256": digest,
            }, content=content)

        client = BuzzShadowClient(
            url="https://console.example/api/buzz-shadow/origintrail/batch",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        attachment = await client.banner(_event())
        self.assertEqual(attachment.media_type, "image/png")
        self.assertEqual(attachment.content_sha256, digest)
        self.assertEqual(attachment.content, content)

    async def test_hash_or_dimensions_mismatch_fails_closed(self):
        content = _png()

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={
                "content-type": "image/png",
                "content-length": str(len(content)),
                "content-disposition": (
                    f'inline; filename="origintrail-review-{JOB_ID}.png"'
                ),
                "x-coineasy-content-sha256": "b" * 64,
            }, content=content)

        client = BuzzShadowClient(
            url="https://console.example/api/buzz-shadow/origintrail/batch",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(BuzzAdapterError, "buzz_banner_invalid_response"):
            await client.banner(_event())

    async def test_accepts_netlify_stream_without_content_length(self):
        content = _png()
        digest = hashlib.sha256(content).hexdigest()

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={
                "content-type": "image/png",
                "content-disposition": (
                    f'inline; filename="origintrail-review-{JOB_ID}.png"'
                ),
                "x-coineasy-content-sha256": digest,
            }, stream=httpx.ByteStream(content))

        client = BuzzShadowClient(
            url="https://console.example/api/buzz-shadow/origintrail/batch",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        attachment = await client.banner(_event())
        self.assertEqual(attachment.content_sha256, digest)
        self.assertEqual(attachment.content, content)


class BuzzDeliveryControlClientCutoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_legacy_missing_attachment_echo(self):
        event = _event()
        attachment_sha = "d" * 64

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(body["attachment_sha256"], attachment_sha)
            return httpx.Response(200, json={
                "event_id": event.event_id,
                "job_id": event.job_id,
                "channel_id": "33333333-3333-4333-8333-333333333333",
                "message_sha256": "b" * 64,
                "request_sha256": "c" * 64,
                "status": "claimed",
                "claim_granted": True,
                "reused": False,
            })

        client = BuzzDeliveryControlClient(
            url="https://console.example/api/buzz-delivery/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        claim = await client.claim(
            event,
            channel_id="33333333-3333-4333-8333-333333333333",
            message_sha256="b" * 64,
            request_sha256="c" * 64,
            attachment_sha256=attachment_sha,
            worker_id="origintrail-buzz:test-1234",
            lease_seconds=180,
        )
        self.assertEqual(claim.attachment_sha256, attachment_sha)

    async def test_rejects_a_different_attachment_echo(self):
        event = _event()

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "event_id": event.event_id,
                "job_id": event.job_id,
                "channel_id": "33333333-3333-4333-8333-333333333333",
                "message_sha256": "b" * 64,
                "request_sha256": "c" * 64,
                "attachment_sha256": "e" * 64,
                "status": "claimed",
                "claim_granted": True,
                "reused": False,
            })

        client = BuzzDeliveryControlClient(
            url="https://console.example/api/buzz-delivery/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(
            BuzzAdapterError, "buzz_delivery_control_invalid_response"
        ):
            await client.claim(
                event,
                channel_id="33333333-3333-4333-8333-333333333333",
                message_sha256="b" * 64,
                request_sha256="c" * 64,
                attachment_sha256="d" * 64,
                worker_id="origintrail-buzz:test-1234",
                lease_seconds=180,
            )


class BuzzReviewControlClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_one_strict_target_and_uses_dedicated_header(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["x-coineasy-buzz-review-key"], TOKEN)
            self.assertEqual(
                json.loads(request.content), {"action": "list", "limit": 1}
            )
            return httpx.Response(200, json={
                "schema_version": "2.0",
                "mode": "publish_intent_review",
                "workspace_id": "11111111-1111-4111-8111-111111111111",
                "targets": [{
                    "job_id": JOB_ID,
                    "delivery_event_id": "a" * 64,
                    "channel_id": "33333333-3333-4333-8333-333333333333",
                    "root_relay_event_id": "b" * 64,
                    "message_sha256": "f" * 64,
                    "protocol_version": "origintrail-buzz-review@2",
                    "delivered_at_epoch": 1_786_100_000,
                }],
            })

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        target = await client.first_target()
        self.assertIsNotNone(target)
        assert target is not None
        self.assertEqual(target.job_id, JOB_ID)
        self.assertEqual(target.root_relay_event_id, "b" * 64)

    async def test_record_requires_exact_echo(self):
        decision = _review_decision()

        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_review_record_response(decision, reused=False),
            )

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        result = await client.record(decision)
        self.assertFalse(result.reused)
        self.assertEqual(result.acknowledgement_status, "disabled")

    async def test_record_with_ack_requires_durable_status_and_exact_action(self):
        decision = _review_decision()

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual(body["action"], "record_with_ack")
            response = _review_record_response(decision, reused=False)
            response["acknowledgement_status"] = "pending"
            return httpx.Response(200, json=response)

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        result = await client.record(decision, durable_acknowledgement=True)
        self.assertFalse(result.reused)
        self.assertEqual(result.acknowledgement_status, "pending")

    async def test_durable_ack_claim_validates_stored_message_and_hash(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(json.loads(request.content), {
                "action": "ack_claim",
                "job_id": JOB_ID,
                "worker_id": "origintrail-buzz-review:test-worker",
                "lease_seconds": 180,
            })
            return httpx.Response(200, json={
                "schema_version": "1.0",
                "mode": "durable_review_acknowledgement",
                "workspace_id": "11111111-1111-4111-8111-111111111111",
                "acknowledgement": _ack_object(),
            })

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        acknowledgement = await client.claim_acknowledgement(
            job_id=JOB_ID,
            worker_id="origintrail-buzz-review:test-worker",
            lease_seconds=180,
        )
        self.assertIsNotNone(acknowledgement)
        assert acknowledgement is not None
        self.assertEqual(acknowledgement.message, ACK_MESSAGE)
        self.assertEqual(
            acknowledgement.message_sha256,
            hashlib.sha256(ACK_MESSAGE.encode()).hexdigest(),
        )

    async def test_ack_attempt_complete_and_reconcile_require_exact_echoes(self):
        request_sha256 = "9" * 64
        relay_event_id = "8" * 64

        async def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            base = {
                "schema_version": "1.0",
                "mode": "durable_review_acknowledgement",
                "workspace_id": "11111111-1111-4111-8111-111111111111",
            }
            if body["action"] == "ack_attempt":
                return httpx.Response(200, json={
                    **base,
                    "job_id": JOB_ID,
                    "status": "attempt_started",
                    "message_sha256": body["message_sha256"],
                    "request_sha256": request_sha256,
                    "authorized_once": True,
                    "reused": False,
                })
            if body["action"] == "ack_complete":
                self.assertTrue(body["reconciled"])
                return httpx.Response(200, json={
                    **base,
                    "job_id": JOB_ID,
                    "status": "delivered",
                    "request_sha256": request_sha256,
                    "relay_event_id": relay_event_id,
                    "reused": False,
                })
            return httpx.Response(200, json={
                **base,
                "reconciled_count": 1,
                "pending_count": 0,
                "failed_count": 0,
                "delivery_unknown_count": 1,
            })

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        self.assertTrue(await client.mark_acknowledgement_attempt(
            JOB_ID,
            worker_id="origintrail-buzz-review:test-worker",
            message_sha256=hashlib.sha256(ACK_MESSAGE.encode()).hexdigest(),
            request_sha256=request_sha256,
        ))
        await client.complete_acknowledgement(
            JOB_ID,
            worker_id="origintrail-buzz-review:test-worker",
            request_sha256=request_sha256,
            relay_event_id=relay_event_id,
            reconciled=True,
        )
        self.assertEqual(
            (await client.reconcile_acknowledgements(limit=25))[
                "delivery_unknown_count"
            ],
            1,
        )

    async def test_ack_complete_retries_commit_unknown_with_identical_body(self):
        request_sha256 = "9" * 64
        relay_event_id = "8" * 64
        attempts: list[bytes] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(bytes(request.content))
            if len(attempts) == 1:
                raise httpx.ConnectError("commit unknown", request=request)
            return httpx.Response(200, json={
                "schema_version": "1.0",
                "mode": "durable_review_acknowledgement",
                "workspace_id": "11111111-1111-4111-8111-111111111111",
                "job_id": JOB_ID,
                "status": "delivered",
                "request_sha256": request_sha256,
                "relay_event_id": relay_event_id,
                "reused": True,
            })

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        await client.complete_acknowledgement(
            JOB_ID,
            worker_id="origintrail-buzz-review:test-worker",
            request_sha256=request_sha256,
            relay_event_id=relay_event_id,
            reconciled=False,
        )
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0], attempts[1])

    async def test_record_retries_transport_once_and_exposes_reused(self):
        decision = _review_decision()
        request_bodies: list[bytes] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            request_bodies.append(bytes(request.content))
            if len(request_bodies) == 1:
                raise httpx.ConnectError("commit status unknown", request=request)
            return httpx.Response(
                200,
                json=_review_record_response(decision, reused=True),
            )

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        result = await client.record(decision)
        self.assertTrue(result.reused)
        self.assertEqual(len(request_bodies), 2)
        self.assertEqual(request_bodies[0], request_bodies[1])

    async def test_record_retries_5xx_exactly_once(self):
        decision = _review_decision()
        request_bodies: list[bytes] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            request_bodies.append(bytes(request.content))
            return httpx.Response(503)

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(
            BuzzAdapterError,
            "buzz_review_control_unavailable",
        ):
            await client.record(decision)
        self.assertEqual(len(request_bodies), 2)
        self.assertEqual(request_bodies[0], request_bodies[1])

    async def test_record_does_not_retry_4xx(self):
        decision = _review_decision()
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(409)

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(
            BuzzAdapterError,
            "buzz_review_control_unavailable",
        ):
            await client.record(decision)
        self.assertEqual(calls, 1)

    async def test_list_does_not_use_commit_unknown_retry(self):
        calls = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(503)

        client = BuzzReviewControlClient(
            url="https://console.example/api/buzz-review/origintrail",
            token=TOKEN,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaisesRegex(
            BuzzAdapterError,
            "buzz_review_control_unavailable",
        ):
            await client.first_target()
        self.assertEqual(calls, 1)

if __name__ == "__main__":
    unittest.main()
