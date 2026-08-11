from __future__ import annotations

import hashlib
import unittest

from core.buzz.cli import BuzzCliError
from core.buzz.models import BuzzRelayReceipt, BuzzReviewTarget, BuzzThreadMessage
from core.buzz.review import (
    OriginTrailBuzzReviewWorker,
    format_review_acknowledgement,
    parse_review_command,
    review_command_sha256,
)


WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "22222222-2222-4222-8222-222222222222"
CHANNEL_ID = "33333333-3333-4333-8333-333333333333"
RELAY_URL = "https://buzz.example"
DELIVERY_EVENT_ID = "a" * 64
ROOT_ID = "b" * 64
REVIEWER = "c" * 64
OTHER = "d" * 64
ATTACHMENT_SHA256 = "1" * 64
MEDIA_URL = f"{RELAY_URL}/media/{ATTACHMENT_SHA256}.png"
THUMB_URL = f"{RELAY_URL}/media/{ATTACHMENT_SHA256}.thumb.jpg"
BLURHASH = "LKO2?U%2Tw=w]~RBVZRi};RPxuwH"
MEDIA_SIZE = 2_048
NOW = 1_786_100_000
ROOT_CONTENT = (
    "OriginTrail review package v5\n"
    f"검토 배너 SHA-256: {ATTACHMENT_SHA256}"
)
ROOT_MESSAGE_SHA = hashlib.sha256(ROOT_CONTENT.encode()).hexdigest()


def _target(message_sha256: str = ROOT_MESSAGE_SHA) -> BuzzReviewTarget:
    return BuzzReviewTarget(
        workspace_id=WORKSPACE_ID,
        job_id=JOB_ID,
        delivery_event_id=DELIVERY_EVENT_ID,
        channel_id=CHANNEL_ID,
        root_relay_event_id=ROOT_ID,
        message_sha256=message_sha256,
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


def _imeta(
    *,
    url: str = MEDIA_URL,
    mime: str = "image/png",
    sha256: str = ATTACHMENT_SHA256,
    size: str = str(MEDIA_SIZE),
    dim: str = "1200x630",
    blurhash: str = BLURHASH,
    thumb_url: str = THUMB_URL,
    extra: tuple[str, ...] = (),
) -> tuple[str, ...]:
    return (
        "imeta",
        f"url {url}",
        f"m {mime}",
        f"x {sha256}",
        f"size {size}",
        f"dim {dim}",
        f"blurhash {blurhash}",
        f"thumb {thumb_url}",
        *extra,
    )


def _root(
    *,
    pubkey: str = OTHER,
    kind: int = 9,
    base_content: str = ROOT_CONTENT,
    media_url: str = MEDIA_URL,
    signed_content: str | None = None,
    imeta_tags: tuple[tuple[str, ...], ...] | None = None,
    extra_tags: tuple[tuple[str, ...], ...] = (),
) -> BuzzThreadMessage:
    if signed_content is None:
        signed_content = f"{base_content}\n![image]({media_url})"
    if imeta_tags is None:
        imeta_tags = (_imeta(url=media_url),)
    return BuzzThreadMessage(
        event_id=ROOT_ID,
        pubkey=pubkey,
        kind=kind,
        content=signed_content,
        created_at=NOW - 120,
        tags=(("h", CHANNEL_ID),) + imeta_tags + extra_tags,
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


class FakeAcknowledger:
    def __init__(self, error: str | None = None):
        self.error = error
        self.replies = []

    async def send_reply_once(self, message, reply_to):
        self.replies.append((message, reply_to))
        if self.error:
            raise BuzzCliError(self.error)
        return BuzzRelayReceipt(event_id="f" * 64)


def _worker(
    control: FakeControl,
    reader: FakeReader,
    acknowledger: FakeAcknowledger | None = None,
    *,
    acknowledgement_enabled: bool = True,
):
    return OriginTrailBuzzReviewWorker(
        control=control,
        reader=reader,
        acknowledger=(
            acknowledger or FakeAcknowledger()
            if acknowledgement_enabled
            else None
        ),
        relay_url=RELAY_URL,
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

    def test_acknowledgement_copy_is_bounded_and_never_mentions(self):
        self.assertIn(
            "자동 발행: OFF",
            format_review_acknowledgement("approved", None),
        )
        marker = "DO_NOT_REFLECT_7f3a"
        message = format_review_acknowledgement(
            "changes_requested",
            (
                marker
                + " @origin_trail "
                "nostr:npub10elfcs4fr0l0r8af98jlmgdh9c8tcxjvz9qkw"
                "038js35mp4dma8qzvjptg "
                + ("가" * 500)
            ),
        )
        self.assertNotIn(marker, message)
        self.assertNotIn("@", message)
        self.assertNotIn("nostr:npub1", message.lower())
        self.assertIn("원문 답글", message)
        self.assertLessEqual(len(message.encode("utf-8")), 1024)


class BuzzReviewWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_does_not_read_relay(self):
        control = FakeControl(None)
        reader = FakeReader([])
        result = await _worker(control, reader).run_once()
        self.assertEqual(result.as_dict(), {"ok": True, "status": "idle"})
        self.assertEqual(reader.roots, [])

    async def test_production_shaped_root_and_first_direct_reply_are_recorded(self):
        first = _message("e" * 64, "수정 요청: 숫자를 다시 확인", created_at=NOW - 20)
        later = _message(
            "f" * 64, "게시 승인: 원문·최종물 확인", created_at=NOW - 10
        )
        control = FakeControl(_target())
        acknowledger = FakeAcknowledger()
        result = await _worker(
            control, FakeReader([later, _root(), first]), acknowledger
        ).run_once()

        self.assertEqual(result.status, "recorded")
        self.assertEqual(result.decision, "changes_requested")
        self.assertEqual(result.acknowledgement_status, "accepted")
        self.assertEqual(result.acknowledgement_event_id, "f" * 64)
        self.assertEqual(acknowledger.replies[0][1], "e" * 64)
        self.assertIn("수정 요청 접수", acknowledger.replies[0][0])
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

    async def test_reused_decision_never_sends_a_second_acknowledgement(self):
        control = FakeControl(_target(), reused=True)
        acknowledger = FakeAcknowledger()
        result = await _worker(
            control,
            FakeReader([_root(), _message("e" * 64, "게시 승인: 원문·최종물 확인")]),
            acknowledger,
        ).run_once()
        self.assertTrue(result.ok)
        self.assertTrue(result.reused)
        self.assertEqual(result.acknowledgement_status, "not_attempted")
        self.assertEqual(acknowledger.replies, [])

    async def test_default_off_acknowledgement_records_without_relay_write(self):
        control = FakeControl(_target())
        result = await _worker(
            control,
            FakeReader([_root(), _message("e" * 64, "게시 승인: 원문·최종물 확인")]),
            acknowledgement_enabled=False,
        ).run_once()
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "recorded")
        self.assertFalse(result.reused)
        self.assertEqual(result.acknowledgement_status, "disabled")
        self.assertEqual(len(control.recorded), 1)

    async def test_ack_failure_never_retries_or_reverses_recorded_decision(self):
        control = FakeControl(_target())
        acknowledger = FakeAcknowledger("buzz_review_acknowledgement_unknown")
        result = await _worker(
            control,
            FakeReader([_root(), _message("e" * 64, "게시 승인: 원문·최종물 확인")]),
            acknowledger,
        ).run_once()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "recorded")
        self.assertEqual(result.error, "buzz_review_acknowledgement_unknown")
        self.assertEqual(result.acknowledgement_status, "unknown")
        self.assertEqual(len(control.recorded), 1)
        self.assertEqual(len(acknowledger.replies), 1)

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

        bad_root = _root(
            base_content=(
                "tampered\n"
                f"검토 배너 SHA-256: {ATTACHMENT_SHA256}"
            )
        )
        result = await _worker(
            FakeControl(_target()), FakeReader([bad_root])
        ).run_once()
        self.assertEqual(result.error, "buzz_review_thread_invalid")

    async def test_root_must_be_authored_by_the_fenced_service_identity(self):
        foreign_root = _root(pubkey="e" * 64)
        result = await _worker(
            FakeControl(_target()), FakeReader([foreign_root])
        ).run_once()
        self.assertEqual(result.error, "buzz_review_thread_invalid")

    async def test_root_must_be_kind_9_and_must_not_be_a_reply(self):
        invalid_roots = (
            _root(kind=40_002),
            _root(
                extra_tags=(("e", "f" * 64, "", "reply"),),
            ),
        )
        for invalid_root in invalid_roots:
            with self.subTest(kind=invalid_root.kind, tags=invalid_root.tags):
                control = FakeControl(_target())
                acknowledger = FakeAcknowledger()
                result = await _worker(
                    control, FakeReader([invalid_root]), acknowledger
                ).run_once()
                self.assertFalse(result.ok)
                self.assertEqual(result.error, "buzz_review_thread_invalid")
                self.assertEqual(control.recorded, [])
                self.assertEqual(acknowledger.replies, [])

    async def test_root_media_envelope_is_strictly_bound_and_fail_closed(self):
        other_sha256 = "2" * 64
        other_media_url = f"{RELAY_URL}/media/{other_sha256}.png"
        evil_media_url = (
            f"https://attacker.example/media/{ATTACHMENT_SHA256}.png"
        )
        missing_hash_base = "OriginTrail review package v5"
        duplicate_hash_base = (
            f"{ROOT_CONTENT}\n"
            f"검토 배너 SHA-256: {ATTACHMENT_SHA256}"
        )
        cases = (
            (
                "missing suffix close",
                _root(signed_content=f"{ROOT_CONTENT}\n![image]({MEDIA_URL}"),
                _target(),
            ),
            (
                "multiple suffixes",
                _root(signed_content=(
                    f"{ROOT_CONTENT}\n![image]({MEDIA_URL})"
                    f"\n![image]({MEDIA_URL})"
                )),
                _target(),
            ),
            (
                "suffix not trailing",
                _root(signed_content=(
                    f"{ROOT_CONTENT}\n![image]({MEDIA_URL})\n"
                )),
                _target(),
            ),
            ("missing imeta", _root(imeta_tags=()), _target()),
            (
                "duplicate imeta",
                _root(imeta_tags=(_imeta(), _imeta())),
                _target(),
            ),
            (
                "mismatched imeta URL",
                _root(imeta_tags=(_imeta(url=other_media_url),)),
                _target(),
            ),
            (
                "non-PNG MIME",
                _root(imeta_tags=(_imeta(mime="video/mp4"),)),
                _target(),
            ),
            (
                "attachment hash mismatch",
                _root(
                    media_url=other_media_url,
                    imeta_tags=(_imeta(
                        url=other_media_url,
                        sha256=other_sha256,
                        thumb_url=(
                            f"{RELAY_URL}/media/{other_sha256}.thumb.jpg"
                        ),
                    ),),
                ),
                _target(),
            ),
            (
                "noncanonical size",
                _root(imeta_tags=(_imeta(size="02048"),)),
                _target(),
            ),
            (
                "oversized media",
                _root(imeta_tags=(_imeta(size=str(4 * 1_024 * 1_024 + 1)),)),
                _target(),
            ),
            (
                "wrong dimensions",
                _root(imeta_tags=(_imeta(dim="1200x675"),)),
                _target(),
            ),
            (
                "unsafe media origin",
                _root(
                    media_url=evil_media_url,
                    imeta_tags=(_imeta(
                        url=evil_media_url,
                        thumb_url=(
                            "https://attacker.example/media/"
                            f"{ATTACHMENT_SHA256}.thumb.jpg"
                        ),
                    ),),
                ),
                _target(),
            ),
            (
                "media URL credentials",
                _root(
                    media_url=(
                        "https://user@buzz.example/media/"
                        f"{ATTACHMENT_SHA256}.png"
                    ),
                    imeta_tags=(_imeta(url=(
                        "https://user@buzz.example/media/"
                        f"{ATTACHMENT_SHA256}.png"
                    )),),
                ),
                _target(),
            ),
            (
                "media URL query",
                _root(
                    media_url=f"{MEDIA_URL}?download=1",
                    imeta_tags=(_imeta(url=f"{MEDIA_URL}?download=1"),),
                ),
                _target(),
            ),
            (
                "unsafe media extension",
                _root(
                    media_url=(
                        f"{RELAY_URL}/media/{ATTACHMENT_SHA256}.jpg"
                    ),
                    imeta_tags=(_imeta(url=(
                        f"{RELAY_URL}/media/{ATTACHMENT_SHA256}.jpg"
                    )),),
                ),
                _target(),
            ),
            (
                "missing blurhash",
                _root(imeta_tags=(_imeta()[:6] + (_imeta()[7],),)),
                _target(),
            ),
            (
                "missing thumbnail",
                _root(imeta_tags=(_imeta()[:-1],)),
                _target(),
            ),
            (
                "invalid blurhash",
                _root(imeta_tags=(_imeta(blurhash="bad hash"),)),
                _target(),
            ),
            (
                "unsafe thumbnail",
                _root(imeta_tags=(_imeta(thumb_url=(
                    "https://attacker.example/media/"
                    f"{ATTACHMENT_SHA256}.thumb.jpg"
                )),)),
                _target(),
            ),
            (
                "wrong thumbnail path",
                _root(imeta_tags=(_imeta(
                    thumb_url=f"{RELAY_URL}/media/{other_sha256}.thumb.jpg"
                ),)),
                _target(),
            ),
            (
                "unexpected duration field",
                _root(imeta_tags=(_imeta(extra=("duration 10",)),)),
                _target(),
            ),
            (
                "missing banner hash line",
                _root(base_content=missing_hash_base),
                _target(hashlib.sha256(missing_hash_base.encode()).hexdigest()),
            ),
            (
                "duplicate banner hash line",
                _root(base_content=duplicate_hash_base),
                _target(hashlib.sha256(duplicate_hash_base.encode()).hexdigest()),
            ),
        )

        for name, invalid_root, target in cases:
            with self.subTest(name=name):
                control = FakeControl(target)
                acknowledger = FakeAcknowledger()
                result = await _worker(
                    control, FakeReader([invalid_root]), acknowledger
                ).run_once()
                self.assertFalse(result.ok)
                self.assertEqual(result.error, "buzz_review_thread_invalid")
                self.assertEqual(control.recorded, [])
                self.assertEqual(acknowledger.replies, [])


if __name__ == "__main__":
    unittest.main()
