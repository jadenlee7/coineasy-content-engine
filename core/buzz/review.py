from __future__ import annotations

import hashlib
import re
import time
from typing import Callable, Protocol
from urllib.parse import urlsplit

from core.buzz.cli import BuzzCliConfig, BuzzCliError, BuzzCliReader
from core.buzz.errors import BuzzAdapterError
from core.buzz.models import (
    BuzzRelayReceipt,
    BuzzReviewDecision,
    BuzzReviewRunResult,
    BuzzReviewTarget,
    BuzzThreadMessage,
)
from core.buzz.settings import BuzzReviewSettings


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SCHEMA = "2.0"
_DOMAIN = "coineasy-buzz-review-decision"
_PUBLISH_APPROVAL = "게시 승인: 원문·최종물 확인"
_BANNER_HASH_LABEL = "검토 배너 SHA-256: "
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_SIZE = re.compile(r"^[1-9][0-9]*$")
_MEDIA_PATH = re.compile(r"^/media/([0-9a-f]{64})(?:[.]png)?$")
_THUMB_PATH = re.compile(r"^/media/([0-9a-f]{64})[.]thumb[.]jpg$")
_BLURHASH = re.compile(r"^[\x21-\x7e]{6,200}$")
_MEDIA_SUFFIX_MARKER = "\n![image]("
_MAX_MEDIA_BYTES = 4 * 1_024 * 1_024


class ReviewControl(Protocol):
    async def first_target(self) -> BuzzReviewTarget | None: ...
    async def record(self, decision: BuzzReviewDecision) -> bool: ...


class ThreadReader(Protocol):
    async def read_thread(
        self, root_event_id: str
    ) -> tuple[BuzzThreadMessage, ...]: ...


class AcknowledgementPublisher(Protocol):
    async def send_reply_once(
        self, message: str, reply_to: str
    ) -> BuzzRelayReceipt: ...


def format_review_acknowledgement(
    decision: str, reason: str | None
) -> str:
    if decision == "approved" and reason is None:
        return (
            "✅ 게시 승인 접수\n"
            "원문·최종물 확인 결정을 기록했습니다.\n\n"
            "현재 상태: 검토 결정 기록 완료\n"
            "자동 발행: OFF"
        )
    if decision != "changes_requested" or not reason:
        raise ValueError("Buzz review acknowledgement is invalid")
    # The reviewer's reason is already preserved in the immutable decision and
    # original signed reply. Never reflect user-controlled text into a service
    # event because Buzz also resolves NIP-27 nostr:npub1 URIs into mentions.
    return (
        "🛠 수정 요청 접수\n"
        "사유는 검토자의 원문 답글에 기록했습니다.\n\n"
        "현재 상태: 수정 대기\n"
        "자동 재생성·발행: OFF"
    )


def parse_review_command(content: str) -> tuple[str, str | None] | None:
    command = content.strip()
    if _CONTROL.search(command) is not None:
        return None
    if command == _PUBLISH_APPROVAL:
        return "approved", None
    prefix = "수정 요청: "
    if command.startswith(prefix):
        reason = command[len(prefix):]
        if (
            reason != reason.strip()
            or not 1 <= len(reason) <= 500
            or len(reason.encode("utf-8")) > 1_500
        ):
            return None
        return "changes_requested", reason
    return None


def review_command_sha256(
    *,
    target: BuzzReviewTarget,
    decision_event_id: str,
    reviewer_pubkey: str,
    decision: str,
    reason: str | None,
    command_created_at_epoch: int,
) -> str:
    values = (
        _DOMAIN,
        _SCHEMA,
        target.workspace_id,
        target.job_id,
        target.delivery_event_id,
        target.channel_id,
        target.root_relay_event_id,
        target.message_sha256,
        target.protocol_version,
        decision_event_id,
        reviewer_pubkey,
        decision,
        reason or "",
        str(command_created_at_epoch),
    )
    return hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()


def _tag_values(message: BuzzThreadMessage, name: str) -> tuple[str, ...]:
    return tuple(tag[1] for tag in message.tags if len(tag) >= 2 and tag[0] == name)


def _exact_channel(message: BuzzThreadMessage, channel_id: str) -> bool:
    values = _tag_values(message, "h")
    return values == (channel_id,)


def _direct_reply(message: BuzzThreadMessage, root_event_id: str) -> bool:
    tags = tuple(tag for tag in message.tags if tag and tag[0] == "e")
    return len(tags) == 1 and len(tags[0]) == 4 \
        and tags[0][1] == root_event_id and tags[0][3] == "reply"


def _valid_root_media(
    message: BuzzThreadMessage,
    *,
    relay_url: str,
    expected_message_sha256: str,
) -> bool:
    # Buzz v0.5.4 signs the original message with one media Markdown suffix.
    # Preserve the durable receipt hash over the pre-suffix message while
    # requiring every signed media field to describe that same PNG.
    if (
        message.content.count("![image]") != 1
        or message.content.count(_MEDIA_SUFFIX_MARKER) != 1
        or not message.content.endswith(")")
    ):
        return False
    base_content, media_tail = message.content.rsplit(
        _MEDIA_SUFFIX_MARKER, maxsplit=1
    )
    media_url = media_tail[:-1]
    if (
        not base_content
        or not media_url
        or any(character.isspace() for character in media_url)
        or hashlib.sha256(base_content.encode("utf-8")).hexdigest()
        != expected_message_sha256
    ):
        return False

    hash_lines = tuple(
        line[len(_BANNER_HASH_LABEL):]
        for line in base_content.split("\n")
        if line.startswith(_BANNER_HASH_LABEL)
    )
    if (
        base_content.count(_BANNER_HASH_LABEL) != 1
        or len(hash_lines) != 1
        or not _LOWER_HEX_64.fullmatch(hash_lines[0])
    ):
        return False
    attachment_sha256 = hash_lines[0]

    imeta_tags = tuple(
        tag for tag in message.tags if tag and tag[0] == "imeta"
    )
    if len(imeta_tags) != 1:
        return False
    imeta = imeta_tags[0]
    if (
        len(imeta) != 8
        or imeta[1] != f"url {media_url}"
        or imeta[2] != "m image/png"
        or imeta[3] != f"x {attachment_sha256}"
        or not imeta[4].startswith("size ")
        or imeta[5] != "dim 1200x630"
        or not imeta[6].startswith("blurhash ")
        or not imeta[7].startswith("thumb ")
    ):
        return False
    size = imeta[4][len("size "):]
    blurhash = imeta[6][len("blurhash "):]
    thumb_url = imeta[7][len("thumb "):]
    if (
        not _CANONICAL_SIZE.fullmatch(size)
        or not 24 <= int(size) <= _MAX_MEDIA_BYTES
        or not _BLURHASH.fullmatch(blurhash)
    ):
        return False

    return _valid_media_url(
        media_url,
        relay_url=relay_url,
        attachment_sha256=attachment_sha256,
        path_pattern=_MEDIA_PATH,
    ) and _valid_media_url(
        thumb_url,
        relay_url=relay_url,
        attachment_sha256=attachment_sha256,
        path_pattern=_THUMB_PATH,
    )


def _valid_media_url(
    media_url: str,
    *,
    relay_url: str,
    attachment_sha256: str,
    path_pattern: re.Pattern[str],
) -> bool:
    if not media_url or any(character.isspace() for character in media_url):
        return False

    try:
        expected = urlsplit(relay_url.rstrip("/"))
        actual = urlsplit(media_url)
        expected_port = expected.port
        actual_port = actual.port
    except ValueError:
        return False
    if (
        expected.username is not None
        or expected.password is not None
        or not expected.hostname
        or expected.path not in {"", "/"}
        or expected.query
        or expected.fragment
        or actual.username is not None
        or actual.password is not None
        or not actual.hostname
        or actual.scheme != expected.scheme
        or actual.hostname != expected.hostname
        or actual_port != expected_port
        or actual.query
        or actual.fragment
    ):
        return False
    media_path = path_pattern.fullmatch(actual.path)
    return media_path is not None and media_path.group(1) == attachment_sha256


class OriginTrailBuzzReviewWorker:
    def __init__(
        self,
        *,
        control: ReviewControl,
        reader: ThreadReader,
        acknowledger: AcknowledgementPublisher | None,
        relay_url: str,
        channel_id: str,
        reviewer_pubkeys: frozenset[str],
        service_pubkey: str,
        clock: Callable[[], float] = time.time,
    ):
        if not reviewer_pubkeys:
            raise ValueError("Buzz review requires at least one reviewer")
        self.control = control
        self.reader = reader
        self.acknowledger = acknowledger
        self.relay_url = relay_url.rstrip("/")
        self.channel_id = channel_id
        self.reviewer_pubkeys = reviewer_pubkeys
        self.service_pubkey = service_pubkey
        self.clock = clock

    async def run_once(self) -> BuzzReviewRunResult:
        try:
            target = await self.control.first_target()
        except BuzzAdapterError as exc:
            return BuzzReviewRunResult(ok=False, status="failed", error=exc.code)
        if target is None:
            return BuzzReviewRunResult(ok=True, status="idle")
        if target.channel_id != self.channel_id:
            return BuzzReviewRunResult(
                ok=False,
                status="failed",
                job_id=target.job_id,
                error="buzz_review_target_invalid",
            )

        try:
            messages = await self.reader.read_thread(target.root_relay_event_id)
        except BuzzCliError as exc:
            return BuzzReviewRunResult(
                ok=False, status="failed", job_id=target.job_id, error=exc.code
            )

        if len({message.event_id for message in messages}) != len(messages):
            return BuzzReviewRunResult(
                ok=False,
                status="failed",
                job_id=target.job_id,
                error="buzz_review_thread_invalid",
            )
        roots = [
            message for message in messages
            if message.event_id == target.root_relay_event_id
        ]
        if (
            len(roots) != 1
            or roots[0].kind != 9
            or roots[0].pubkey != self.service_pubkey
            or not _exact_channel(roots[0], target.channel_id)
            or any(tag and tag[0] == "e" for tag in roots[0].tags)
            or not _valid_root_media(
                roots[0],
                relay_url=self.relay_url,
                expected_message_sha256=target.message_sha256,
            )
            or roots[0].created_at > target.delivered_at_epoch + 300
        ):
            return BuzzReviewRunResult(
                ok=False,
                status="failed",
                job_id=target.job_id,
                error="buzz_review_thread_invalid",
            )

        now_epoch = int(self.clock())
        candidates: list[tuple[BuzzThreadMessage, str, str | None]] = []
        for message in messages:
            if (
                message.event_id == target.root_relay_event_id
                or message.kind != 9
                or message.pubkey not in self.reviewer_pubkeys
                or not _exact_channel(message, target.channel_id)
                or not _direct_reply(message, target.root_relay_event_id)
                or message.created_at < target.delivered_at_epoch
                or message.created_at > now_epoch + 300
            ):
                continue
            parsed = parse_review_command(message.content)
            if parsed is not None:
                candidates.append((message, parsed[0], parsed[1]))

        if not candidates:
            return BuzzReviewRunResult(
                ok=True, status="awaiting_review", job_id=target.job_id
            )
        message, decision_name, reason = min(
            candidates, key=lambda item: (item[0].created_at, item[0].event_id)
        )
        decision = BuzzReviewDecision(
            target=target,
            decision_event_id=message.event_id,
            reviewer_pubkey=message.pubkey,
            decision=decision_name,
            reason=reason,
            command_sha256=review_command_sha256(
                target=target,
                decision_event_id=message.event_id,
                reviewer_pubkey=message.pubkey,
                decision=decision_name,
                reason=reason,
                command_created_at_epoch=message.created_at,
            ),
            command_created_at_epoch=message.created_at,
        )
        try:
            reused = await self.control.record(decision)
        except BuzzAdapterError as exc:
            return BuzzReviewRunResult(
                ok=False, status="failed", job_id=target.job_id, error=exc.code
            )
        if reused:
            return BuzzReviewRunResult(
                ok=True,
                status="recorded",
                job_id=target.job_id,
                decision=decision_name,
                reused=True,
                acknowledgement_status="not_attempted",
            )
        if self.acknowledger is None:
            return BuzzReviewRunResult(
                ok=True,
                status="recorded",
                job_id=target.job_id,
                decision=decision_name,
                reused=False,
                acknowledgement_status="disabled",
            )
        acknowledgement = format_review_acknowledgement(decision_name, reason)
        try:
            receipt = await self.acknowledger.send_reply_once(
                acknowledgement, message.event_id
            )
        except BuzzCliError as exc:
            # The immutable review decision is already committed. Never retry
            # the command or change publication state merely to obtain a UI
            # acknowledgement.
            return BuzzReviewRunResult(
                ok=False,
                status="recorded",
                job_id=target.job_id,
                decision=decision_name,
                reused=False,
                acknowledgement_status="unknown",
                error=exc.code,
            )
        return BuzzReviewRunResult(
            ok=True,
            status="recorded",
            job_id=target.job_id,
            decision=decision_name,
            reused=False,
            acknowledgement_status="accepted",
            acknowledgement_event_id=receipt.event_id,
        )


def build_origintrail_buzz_review_worker(
    settings: BuzzReviewSettings,
) -> OriginTrailBuzzReviewWorker:
    from core.buzz.clients import BuzzReviewControlClient
    from core.buzz.cli import BuzzCliPublisher

    config = BuzzCliConfig(
        cli_path=settings.cli_path,
        relay_url=settings.relay_url,
        private_key=settings.private_key,
        auth_tag=settings.auth_tag,
        channel_id=settings.channel_id,
    )
    return OriginTrailBuzzReviewWorker(
        control=BuzzReviewControlClient(
            url=settings.review_url, token=settings.review_token
        ),
        reader=BuzzCliReader(config),
        acknowledger=(
            BuzzCliPublisher(config)
            if settings.acknowledgement_enabled
            else None
        ),
        relay_url=settings.relay_url,
        channel_id=settings.channel_id,
        reviewer_pubkeys=settings.reviewer_pubkeys,
        service_pubkey=settings.service_pubkey,
    )


__all__ = [
    "OriginTrailBuzzReviewWorker",
    "build_origintrail_buzz_review_worker",
    "format_review_acknowledgement",
    "parse_review_command",
    "review_command_sha256",
]
