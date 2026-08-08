from __future__ import annotations

import hashlib
import unittest

from core.buzz.models import BuzzReviewTarget, BuzzThreadMessage
from core.buzz.review import (
    OriginTrailBuzzReviewWorker,
    parse_review_command,
    review_command_sha256,
)


WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "22222222-2222-4222-8222-222222222222"
CHANNEL_ID = "33333333-3333-4333-8333-333333333333"
DELIVERY_EVENT_ID = "a" * 64
ROOT_ID = "b" * 64
REVIEWER = "c" * 64
OTHER = "d" * 64
NOW = 1_786_100_000
ROOT_CONTENT = "OriginTrail review package v4"
ROOT_MESSAGE_SHA = hashlib.sha256(ROOT_CONTENT.encode()).hexdigest()


def _target() -> BuzzReviewTarget:
    return BuzzReviewTarget(
        workspace_id=WORKSPACE_ID,
        job_id=JOB_ID,
        delivery_event_id=DELIVERY_EVENT_ID,
        channel_id=CHANNEL_ID,
        root_relay_event_id=ROOT_ID,
        message_sha256=ROOT_MESSAGE_SHA,
        protocol_version="origintrail-buzz-review@2",
        delivered_at_epoch=NOW - 100,
    )


def _message(
    event_id: str,
    content: str,
    *,
    pubkey: str = REVIEWER,
    created_at: int = NOW - 10,
    channel_id: str = CHANNEL_ID,
    e_tags: tuple[str, ...] = (ROOT_ID,),
    kind: int = 9,
) -> BuzzThreadMessage:
    tags = (("h", channel_id),) + tuple(
        ("e", value, "", "reply") for value in e_tags
    )
    return BuzzThreadMessage(
        event_id=event_id,
        pubkey=pubkey,
        kind=kind,
        content=content,
        created_at=created_at,
        tags=tags,
    )


def _root() -> BuzzThreadMessage:
    return BuzzThreadMessage(
        event_id=ROOT_ID,
        pubkey=OTHER,
        kind=40002,
        content=ROOT_CONTENT,
        created_at=NOW - 120,
        tags=(("h", CHANNEL_ID),),
    )


class FakeControl:
    def __init__(self, target: BuzzReviewTarget | None = None, reused: bool = False):
        self.target = target
        self.reused = reused
        self.recorded = []

    async def first_target(self):
        return self.target

    async def record(self, decision):
        self.recorded.append(decision)
        return self.reused


class FakeReader:
    def __init__(self, messages):
        self.messages = tuple(messages)
        self.roots = []

    async def read_thread(self, root_event_id):
        self.roots.append(root_event_id)
        return self.messages


def _worker(control: FakeControl, reader: FakeReader):
    return OriginTrailBuzzReviewWorker(
        control=control,
        reader=reader,
        channel_id=CHANNEL_ID,
        reviewer_pubkeys=frozenset({REVIEWER}),
        service_pubkey=OTHER,
        clock=lambda: NOW,
    )


class BuzzReviewCommandTests(unittest.TestCase):
    def test_only_two_exact_korean_commands_are_accepted(self):
        self.assertEqual(
            parse_review_command(" 게시 승인: 원문·최종물 확인 "),
            ("approved", None),
        )
        self.assertEqual(
            parse_review_command("수정 요청: 출처 문장을 더 명확히"),
            ("changes_requested", "출처 문장을 더 명확히"),
        )
        for invalid in (
            "승인", "승인합니다", "게시 승인", "수정 요청:",
            "수정요청: 이유", "수정 요청:  이유",
            "수정 요청: 이유\n추가", "approve", "수정 요청: " + ("가" * 501),
        ):
            with self.subTest(invalid=invalid[:20]):
                self.assertIsNone(parse_review_command(invalid))

    def test_hash_binds_every_decision_identity(self):
        target = _target()
        first = review_command_sha256(
            target=target,
            decision_event_id="e" * 64,
            reviewer_pubkey=REVIEWER,
            decision="approved",
            reason=None,
            command_created_at_epoch=NOW,
        )
        second = review_command_sha256(
            target=target,
            decision_event_id="f" * 64,
            reviewer_pubkey=REVIEWER,
            decision="approved",
            reason=None,
            command_created_at_epoch=NOW,
        )
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 64)


class BuzzReviewWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_does_not_read_relay(self):
        control = FakeControl(None)
        reader = FakeReader([])
        result = await _worker(control, reader).run_once()
        self.assertEqual(result.as_dict(), {"ok": True, "status": "idle"})
        self.assertEqual(reader.roots, [])

    async def test_first_valid_allowed_direct_reply_is_recorded(self):
        first = _message("e" * 64, "수정 요청: 숫자를 다시 확인", created_at=NOW - 20)
        later = _message(
            "f" * 64, "게시 승인: 원문·최종물 확인", created_at=NOW - 10
        )
        control = FakeControl(_target())
        result = await _worker(control, FakeReader([later, _root(), first])).run_once()

        self.assertEqual(result.status, "recorded")
        self.assertEqual(result.decision, "changes_requested")
        self.assertEqual(len(control.recorded), 1)
        decision = control.recorded[0]
        self.assertEqual(decision.decision_event_id, "e" * 64)
        self.assertEqual(decision.reason, "숫자를 다시 확인")
        self.assertEqual(
            decision.command_sha256,
            review_command_sha256(
                target=_target(),
                decision_event_id="e" * 64,
                reviewer_pubkey=REVIEWER,
                decision="changes_requested",
                reason="숫자를 다시 확인",
                command_created_at_epoch=NOW - 20,
            ),
        )

    async def test_wrong_author_channel_nested_future_and_noncommand_are_ignored(self):
        messages = [
            _root(),
            _message("1" * 64, "게시 승인: 원문·최종물 확인", pubkey=OTHER),
            _message("2" * 64, "게시 승인: 원문·최종물 확인", channel_id="44444444-4444-4444-8444-444444444444"),
            _message("3" * 64, "게시 승인: 원문·최종물 확인", e_tags=(ROOT_ID, "9" * 64)),
            _message("4" * 64, "게시 승인: 원문·최종물 확인", created_at=NOW + 301),
            _message("5" * 64, "좋아 보입니다"),
        ]
        control = FakeControl(_target())
        result = await _worker(control, FakeReader(messages)).run_once()
        self.assertEqual(result.status, "awaiting_review")
        self.assertEqual(control.recorded, [])

    async def test_duplicate_event_identity_fails_closed(self):
        duplicate = _message("e" * 64, "게시 승인: 원문·최종물 확인")
        control = FakeControl(_target())
        result = await _worker(
            control, FakeReader([_root(), duplicate, duplicate])
        ).run_once()
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "buzz_review_thread_invalid")
        self.assertEqual(control.recorded, [])

    async def test_unmarked_reply_is_ignored_and_root_hash_mismatch_fails(self):
        unmarked = BuzzThreadMessage(
            event_id="e" * 64,
            pubkey=REVIEWER,
            kind=9,
            content="게시 승인: 원문·최종물 확인",
            created_at=NOW - 10,
            tags=(("h", CHANNEL_ID), ("e", ROOT_ID)),
        )
        control = FakeControl(_target())
        result = await _worker(control, FakeReader([_root(), unmarked])).run_once()
        self.assertEqual(result.status, "awaiting_review")
        self.assertEqual(control.recorded, [])

        bad_root = BuzzThreadMessage(
            event_id=ROOT_ID,
            pubkey=OTHER,
            kind=40_002,
            content="tampered",
            created_at=NOW - 120,
            tags=(("h", CHANNEL_ID),),
        )
        result = await _worker(
            FakeControl(_target()), FakeReader([bad_root])
        ).run_once()
        self.assertEqual(result.error, "buzz_review_thread_invalid")

    async def test_root_must_be_authored_by_the_fenced_service_identity(self):
        foreign_root = BuzzThreadMessage(
            event_id=ROOT_ID,
            pubkey="e" * 64,
            kind=40_002,
            content=ROOT_CONTENT,
            created_at=NOW - 120,
            tags=(("h", CHANNEL_ID),),
        )
        result = await _worker(
            FakeControl(_target()), FakeReader([foreign_root])
        ).run_once()
        self.assertEqual(result.error, "buzz_review_thread_invalid")


if __name__ == "__main__":
    unittest.main()
