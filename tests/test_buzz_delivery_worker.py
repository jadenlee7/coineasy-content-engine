from __future__ import annotations

import unittest

from core.buzz.cli import BuzzCliError
from core.buzz.models import (
    BuzzDeliveryClaim,
    BuzzRelayReceipt,
    BuzzShadowEvent,
)
from core.buzz.worker import OriginTrailBuzzDeliveryWorker


EVENT_ID = "a" * 64
JOB_ID = "22222222-2222-4222-8222-222222222222"
CHANNEL_ID = "33333333-3333-4333-8333-333333333333"


def _event() -> BuzzShadowEvent:
    return BuzzShadowEvent(
        event_id=EVENT_ID,
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
        headline_ko="OriginTrail 7월 업데이트",
        summary_ko="DKG V10과 Buzz 통합의 핵심 내용을 정리했습니다.",
    )


class FakeShadow:
    def __init__(self, event=_event()):
        self.event = event
        self.calls = 0

    async def first_event(self):
        self.calls += 1
        return self.event


class FakeControl:
    def __init__(
        self,
        *,
        claim_granted=True,
        claim_status="claimed",
        mark_result=True,
        mark_error=None,
        complete_error=None,
    ):
        self.claim_granted = claim_granted
        self.claim_status = claim_status
        self.mark_result = mark_result
        self.mark_error = mark_error
        self.complete_error = complete_error
        self.events: list[tuple] = []

    async def claim(self, event, **kwargs):
        self.events.append(("claim", kwargs))
        return BuzzDeliveryClaim(
            event_id=event.event_id,
            job_id=event.job_id,
            channel_id=kwargs["channel_id"],
            message_sha256=kwargs["message_sha256"],
            request_sha256=kwargs["request_sha256"],
            status=self.claim_status,
            claim_granted=self.claim_granted,
            reused=False,
        )

    async def mark_attempt(self, event_id, **kwargs):
        self.events.append(("attempt", kwargs))
        if self.mark_error:
            raise self.mark_error
        return self.mark_result

    async def complete(self, event_id, **kwargs):
        self.events.append(("complete", kwargs))
        if self.complete_error:
            raise self.complete_error

    async def fail(self, event_id, **kwargs):
        self.events.append(("fail", kwargs))
        return "pending" if kwargs["retryable_before_attempt"] else "delivery_unknown"


class FakePublisher:
    def __init__(self, *, preflight_error=None, send_error=None):
        self.preflight_error = preflight_error
        self.send_error = send_error
        self.events: list[tuple] = []

    async def preflight(self):
        self.events.append(("preflight",))
        if self.preflight_error:
            raise self.preflight_error

    async def send_once(self, message):
        self.events.append(("send", message))
        if self.send_error:
            raise self.send_error
        return BuzzRelayReceipt(event_id="d" * 64)


def _worker(control, publisher, shadow=None):
    return OriginTrailBuzzDeliveryWorker(
        shadow=shadow or FakeShadow(),
        control=control,
        publisher=publisher,
        studio_origin="https://console.example",
        relay_url="https://buzz.example",
        channel_id=CHANNEL_ID,
        worker_id="origintrail-buzz:test-1234",
    )


class BuzzDeliveryWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_attempt_fences_one_send_then_completes(self):
        control = FakeControl()
        publisher = FakePublisher()
        result = await _worker(control, publisher).run_once()
        self.assertEqual(result.as_dict(), {
            "ok": True,
            "claimed": True,
            "status": "delivered",
            "event_id": EVENT_ID,
        })
        self.assertEqual([event[0] for event in control.events], [
            "claim", "attempt", "complete"
        ])
        self.assertEqual([event[0] for event in publisher.events], [
            "preflight", "send"
        ])

    async def test_attempt_response_loss_never_calls_buzz(self):
        control = FakeControl(mark_error=RuntimeError("response lost"))
        publisher = FakePublisher()
        result = await _worker(control, publisher).run_once()
        self.assertEqual(result.status, "delivery_unknown")
        self.assertEqual([event[0] for event in publisher.events], ["preflight"])
        self.assertEqual([event[0] for event in control.events], ["claim", "attempt"])

    async def test_reused_attempt_never_calls_buzz(self):
        control = FakeControl(mark_result=False)
        publisher = FakePublisher()
        result = await _worker(control, publisher).run_once()
        self.assertEqual(result.error, "buzz_delivery_attempt_already_started")
        self.assertEqual([event[0] for event in publisher.events], ["preflight"])

    async def test_cli_error_after_marker_becomes_unknown_without_retry(self):
        control = FakeControl()
        publisher = FakePublisher(send_error=BuzzCliError("buzz_delivery_unknown"))
        result = await _worker(control, publisher).run_once()
        self.assertEqual(result.status, "delivery_unknown")
        self.assertEqual([event[0] for event in publisher.events], ["preflight", "send"])
        self.assertEqual(control.events[-1][0], "fail")
        self.assertFalse(control.events[-1][1]["retryable_before_attempt"])

    async def test_completion_response_loss_never_resends(self):
        control = FakeControl(complete_error=RuntimeError("response lost"))
        publisher = FakePublisher()
        result = await _worker(control, publisher).run_once()
        self.assertEqual(result.error, "buzz_delivery_completion_unavailable")
        self.assertEqual([event[0] for event in publisher.events], ["preflight", "send"])
        self.assertEqual([event[0] for event in control.events], [
            "claim", "attempt", "complete"
        ])

    async def test_preflight_network_error_retries_before_attempt_only(self):
        control = FakeControl()
        publisher = FakePublisher(preflight_error=BuzzCliError(
            "buzz_cli_preflight_failed", retryable_before_attempt=True
        ))
        result = await _worker(control, publisher).run_once()
        self.assertEqual(result.status, "pending")
        self.assertEqual([event[0] for event in control.events], ["claim", "fail"])
        self.assertTrue(control.events[-1][1]["retryable_before_attempt"])

    async def test_existing_terminal_receipt_stops_before_preflight(self):
        control = FakeControl(claim_granted=False, claim_status="delivered")
        publisher = FakePublisher()
        result = await _worker(control, publisher).run_once()
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "delivered")
        self.assertEqual(publisher.events, [])

    async def test_empty_shadow_is_idle_without_control_or_relay(self):
        control = FakeControl()
        publisher = FakePublisher()
        result = await _worker(control, publisher, FakeShadow(None)).run_once()
        self.assertEqual(result.status, "idle")
        self.assertEqual(control.events, [])
        self.assertEqual(publisher.events, [])


if __name__ == "__main__":
    unittest.main()
