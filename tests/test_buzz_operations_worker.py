from __future__ import annotations

import hashlib
import unittest

from core.buzz.cli import BuzzCliError
from core.buzz.errors import BuzzAdapterError
from core.buzz.models import (
    BuzzOperationsResponse,
    BuzzRelayReceipt,
    BuzzThreadMessage,
)
from core.buzz.operations_worker import OriginTrailBuzzOperationsWorker


CHANNEL = "33333333-3333-4333-8333-333333333333"
REVIEWER = "a" * 64
SERVICE = "b" * 64
COMMAND_EVENT = "c" * 64
RESPONSE_EVENT = "d" * 64
START = 1_786_000_000
NOW = START + 1_000
MESSAGE = "CoinEasy 운영 상태\n대기 기획: 0 · 보류: 0\n자동 발행: OFF"


def _response(
    *, status: str = "pending", claim: bool = False,
    request_sha: str | None = None, started: int | None = None,
    relay_event: str | None = None,
) -> BuzzOperationsResponse:
    return BuzzOperationsResponse(
        workspace_id="11111111-1111-4111-8111-111111111111",
        command_event_id=COMMAND_EVENT,
        channel_id=CHANNEL,
        reply_to_event_id=COMMAND_EVENT,
        thread_root_event_id=COMMAND_EVENT,
        command="status",
        task_id=None,
        message=MESSAGE,
        message_sha256=hashlib.sha256(MESSAGE.encode()).hexdigest(),
        status=status,
        claim_granted=claim,
        reused=False,
        request_sha256=request_sha,
        delivery_started_at_epoch=started,
        relay_event_id=relay_event,
    )


def _command_message(content: str = "상태") -> BuzzThreadMessage:
    return BuzzThreadMessage(
        event_id=COMMAND_EVENT,
        pubkey=REVIEWER,
        kind=9,
        content=content,
        created_at=NOW - 10,
        tags=(("h", CHANNEL),),
    )


class FakeControl:
    def __init__(self):
        self.unknown = None
        self.backlog = None
        self.recorded = _response()
        self.claimed = _response(status="claimed", claim=True)
        self.calls: list[tuple[str, object]] = []
        self.mark_result = True
        self.mark_error: BuzzAdapterError | None = None

    async def reconcile(self, *, limit):
        self.calls.append(("reconcile", limit))
        return {"requeued_count": 0, "failed_count": 0, "unknown_count": 0}

    async def first_unknown(self):
        self.calls.append(("unknown", None))
        return self.unknown

    async def claim_response(self, *, command_event_id, worker_id, lease_seconds):
        self.calls.append(("claim", command_event_id))
        if command_event_id is None:
            return self.backlog
        return self.claimed

    async def record(self, command, *, channel_id):
        self.calls.append(("record", command.event_id))
        return self.recorded

    async def mark_response_attempt(self, response, *, worker_id, request_sha256):
        self.calls.append(("mark", request_sha256))
        if self.mark_error:
            raise self.mark_error
        return self.mark_result

    async def complete_response(
        self, response, *, worker_id, request_sha256, relay_event_id, reconciled,
    ):
        self.calls.append(("complete", (relay_event_id, reconciled)))

    async def fail_response(
        self, response, *, worker_id, error_code, retryable_before_attempt,
    ):
        self.calls.append(("fail", (error_code, retryable_before_attempt)))
        return "pending" if retryable_before_attempt else "delivery_unknown"


class FakeReader:
    def __init__(self, channel=(), thread=()):
        self.channel = tuple(channel)
        self.thread = tuple(thread)
        self.calls: list[tuple[str, object]] = []

    async def read_channel(self, *, since_epoch, limit=100):
        self.calls.append(("channel", (since_epoch, limit)))
        return self.channel

    async def read_thread(self, root_event_id):
        self.calls.append(("thread", root_event_id))
        return self.thread


class FakePublisher:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []
        self.send_error: BuzzCliError | None = None

    async def preflight(self):
        self.calls.append(("preflight", None))

    async def send_reply_once(self, message, reply_to):
        self.calls.append(("send", (message, reply_to)))
        if self.send_error:
            raise self.send_error
        return BuzzRelayReceipt(RESPONSE_EVENT)


def _worker(control, reader, publisher):
    return OriginTrailBuzzOperationsWorker(
        control=control,
        reader=reader,
        publisher=publisher,
        relay_url="https://buzz.example",
        channel_id=CHANNEL,
        reviewer_pubkeys=frozenset({REVIEWER}),
        service_pubkey=SERVICE,
        release_sha="e" * 40,
        protocol_start_epoch=START,
        response_lease_seconds=180,
        clock=lambda: NOW,
        worker_id="origintrail-operations:staging",
    )


class BuzzOperationsWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_is_read_only_and_never_sends(self):
        control = FakeControl()
        reader = FakeReader()
        publisher = FakePublisher()
        result = await _worker(control, reader, publisher).run_once()
        self.assertEqual(result.as_dict(), {"ok": True, "status": "idle"})
        self.assertEqual(publisher.calls, [])
        self.assertEqual(reader.calls, [("channel", (START, 100))])

    async def test_command_is_recorded_before_exactly_one_response(self):
        control = FakeControl()
        reader = FakeReader(channel=(_command_message(),))
        publisher = FakePublisher()
        result = await _worker(control, reader, publisher).run_once()
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "responded")
        self.assertEqual(result.response_event_id, RESPONSE_EVENT)
        names = [call[0] for call in control.calls]
        self.assertLess(names.index("record"), names.index("mark"))
        self.assertLess(names.index("mark"), names.index("complete"))
        self.assertEqual([call[0] for call in publisher.calls], ["preflight", "send"])

    async def test_mark_commit_unknown_never_calls_relay(self):
        control = FakeControl()
        control.mark_error = BuzzAdapterError("buzz_operations_control_unavailable")
        publisher = FakePublisher()
        result = await _worker(
            control, FakeReader(channel=(_command_message(),)), publisher
        ).run_once()
        self.assertFalse(result.ok)
        self.assertEqual(result.response_status, "unknown")
        self.assertEqual([call[0] for call in publisher.calls], ["preflight"])

    async def test_send_failure_becomes_unknown_and_is_not_retryable(self):
        control = FakeControl()
        publisher = FakePublisher()
        publisher.send_error = BuzzCliError("buzz_delivery_unknown")
        result = await _worker(
            control, FakeReader(channel=(_command_message(),)), publisher
        ).run_once()
        self.assertEqual(result.response_status, "delivery_unknown")
        self.assertIn(("fail", ("buzz_delivery_unknown", False)), control.calls)
        self.assertEqual(len([call for call in publisher.calls if call[0] == "send"]), 1)

    async def test_unknown_exact_thread_is_completed_without_resend(self):
        request_sha = "f" * 64
        control = FakeControl()
        control.unknown = _response(
            status="delivery_unknown", request_sha=request_sha, started=NOW - 20
        )
        observed = BuzzThreadMessage(
            event_id=RESPONSE_EVENT,
            pubkey=SERVICE,
            kind=9,
            content=MESSAGE,
            created_at=NOW - 10,
            tags=(("h", CHANNEL), ("e", COMMAND_EVENT, "", "reply")),
        )
        publisher = FakePublisher()
        result = await _worker(
            control, FakeReader(thread=(observed,)), publisher
        ).run_once()
        self.assertEqual(result.status, "response_reconciled")
        self.assertEqual(publisher.calls, [])
        self.assertIn(("complete", (RESPONSE_EVENT, True)), control.calls)

    async def test_invalid_chat_is_ignored(self):
        control = FakeControl()
        result = await _worker(
            control, FakeReader(channel=(_command_message("오늘 뭐해?"),)),
            FakePublisher(),
        ).run_once()
        self.assertEqual(result.status, "idle")
        self.assertNotIn("record", [call[0] for call in control.calls])


if __name__ == "__main__":
    unittest.main()
