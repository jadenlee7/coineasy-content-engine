from __future__ import annotations

import unittest

from core.buzz.models import BuzzThreadMessage
from core.buzz.operations import (
    BUZZ_OPERATIONS_PROTOCOL_VERSION,
    eligible_operations_commands,
    parse_operations_command,
)


CHANNEL_ID = "33333333-3333-4333-8333-333333333333"
REVIEWER = "a" * 64
SERVICE = "b" * 64
START = 1_786_000_000


def _message(
    content: str,
    *,
    event_id: str = "c" * 64,
    pubkey: str = REVIEWER,
    created_at: int = START + 100,
    tags: tuple[tuple[str, ...], ...] | None = None,
) -> BuzzThreadMessage:
    return BuzzThreadMessage(
        event_id=event_id,
        pubkey=pubkey,
        kind=9,
        content=content,
        created_at=created_at,
        tags=tags or (("h", CHANNEL_ID),),
    )


class BuzzOperationsCommandTests(unittest.TestCase):
    def test_exact_commands_only(self):
        self.assertEqual(BUZZ_OPERATIONS_PROTOCOL_VERSION, "origintrail-buzz-operations@1")
        self.assertEqual(parse_operations_command("상태"), "status")
        self.assertEqual(parse_operations_command("  오늘 기획\n"), "plan_today")
        self.assertEqual(parse_operations_command("다음 작업"), "next_task")
        self.assertEqual(parse_operations_command("보류"), "hold")
        for invalid in (
            "승인",
            "오늘 기획 부탁해",
            "`상태`",
            "상태\n다음 작업",
            "수정 요청: 상태",
            "@agent 상태",
            "상태" * 40,
        ):
            with self.subTest(invalid=invalid):
                self.assertIsNone(parse_operations_command(invalid))

    def test_top_level_commands_are_allowlisted_and_sorted(self):
        later = _message(
            "상태", event_id="d" * 64, created_at=START + 200
        )
        earlier = _message(
            "오늘 기획", event_id="c" * 64, created_at=START + 100
        )
        candidates = eligible_operations_commands(
            (later, earlier),
            channel_id=CHANNEL_ID,
            reviewer_pubkeys=frozenset({REVIEWER}),
            service_pubkey=SERVICE,
            protocol_start_epoch=START,
            now_epoch=START + 300,
        )
        self.assertEqual(
            [candidate.command for candidate in candidates],
            ["plan_today", "status"],
        )
        self.assertEqual(candidates[0].reply_to_event_id, None)
        self.assertRegex(candidates[0].command_sha256, r"^[0-9a-f]{64}$")

    def test_hold_requires_one_exact_direct_reply(self):
        response_event_id = "e" * 64
        hold = _message(
            "보류",
            tags=(
                ("h", CHANNEL_ID),
                ("e", response_event_id, "", "reply"),
            ),
        )
        candidates = eligible_operations_commands(
            (hold,),
            channel_id=CHANNEL_ID,
            reviewer_pubkeys=frozenset({REVIEWER}),
            service_pubkey=SERVICE,
            protocol_start_epoch=START,
            now_epoch=START + 300,
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].command, "hold")
        self.assertEqual(candidates[0].reply_to_event_id, response_event_id)

        invalid = (
            _message("보류"),
            _message("보류", tags=(
                ("h", CHANNEL_ID), ("e", response_event_id),
            )),
            _message("보류", tags=(
                ("h", CHANNEL_ID),
                ("e", response_event_id, "", "root"),
            )),
        )
        for value in invalid:
            with self.subTest(tags=value.tags):
                self.assertEqual(eligible_operations_commands(
                    (value,),
                    channel_id=CHANNEL_ID,
                    reviewer_pubkeys=frozenset({REVIEWER}),
                    service_pubkey=SERVICE,
                    protocol_start_epoch=START,
                    now_epoch=START + 300,
                ), ())

    def test_top_level_commands_reject_reply_tags(self):
        message = _message(
            "상태",
            tags=(
                ("h", CHANNEL_ID),
                ("e", "e" * 64, "", "reply"),
            ),
        )
        self.assertEqual(eligible_operations_commands(
            (message,),
            channel_id=CHANNEL_ID,
            reviewer_pubkeys=frozenset({REVIEWER}),
            service_pubkey=SERVICE,
            protocol_start_epoch=START,
            now_epoch=START + 300,
        ), ())

    def test_wrong_identity_channel_time_or_kind_is_ignored(self):
        base = _message("상태")
        invalid = (
            BuzzThreadMessage(**{**base.__dict__, "pubkey": SERVICE}),
            BuzzThreadMessage(**{**base.__dict__, "pubkey": "f" * 64}),
            BuzzThreadMessage(**{**base.__dict__, "kind": 40002}),
            BuzzThreadMessage(**{**base.__dict__, "created_at": START - 1}),
            BuzzThreadMessage(**{**base.__dict__, "created_at": START + 601}),
            BuzzThreadMessage(**{
                **base.__dict__,
                "tags": (("h", "44444444-4444-4444-8444-444444444444"),),
            }),
            BuzzThreadMessage(**{
                **base.__dict__,
                "tags": (("h", CHANNEL_ID), ("h", CHANNEL_ID)),
            }),
        )
        for value in invalid:
            with self.subTest(value=value):
                self.assertEqual(eligible_operations_commands(
                    (value,),
                    channel_id=CHANNEL_ID,
                    reviewer_pubkeys=frozenset({REVIEWER}),
                    service_pubkey=SERVICE,
                    protocol_start_epoch=START,
                    now_epoch=START + 300,
                ), ())

    def test_duplicate_event_id_is_processed_once(self):
        message = _message("상태")
        candidates = eligible_operations_commands(
            (message, message),
            channel_id=CHANNEL_ID,
            reviewer_pubkeys=frozenset({REVIEWER}),
            service_pubkey=SERVICE,
            protocol_start_epoch=START,
            now_epoch=START + 300,
        )
        self.assertEqual(len(candidates), 1)


if __name__ == "__main__":
    unittest.main()
