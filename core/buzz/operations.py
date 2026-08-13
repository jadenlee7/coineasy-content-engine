from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable

from core.buzz.models import BuzzOperationsCommand, BuzzThreadMessage


BUZZ_OPERATIONS_PROTOCOL_VERSION = "origintrail-buzz-operations@1"
BUZZ_OPERATIONS_RESPONSE_TEMPLATE_VERSION = (
    "origintrail-buzz-operations-response@1"
)
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_COMMANDS = {
    "상태": "status",
    "오늘 기획": "plan_today",
    "다음 작업": "next_task",
    "보류": "hold",
}


def parse_operations_command(content: str) -> str | None:
    if not isinstance(content, str):
        return None
    try:
        if len(content.encode("utf-8")) > 64:
            return None
    except UnicodeEncodeError:
        return None
    return _COMMANDS.get(content.strip())


def _exact_channel_tag(
    message: BuzzThreadMessage, channel_id: str
) -> bool:
    return [tag for tag in message.tags if tag and tag[0] == "h"] == [
        ("h", channel_id)
    ]


def _reply_target(message: BuzzThreadMessage) -> str | None:
    tags = [tag for tag in message.tags if tag and tag[0] == "e"]
    if (
        len(tags) != 1
        or len(tags[0]) != 4
        or tags[0][2] != ""
        or tags[0][3] != "reply"
        or not _LOWER_HEX_64.fullmatch(tags[0][1])
    ):
        return None
    return tags[0][1]


def eligible_operations_commands(
    messages: Iterable[BuzzThreadMessage],
    *,
    channel_id: str,
    reviewer_pubkeys: frozenset[str],
    service_pubkey: str,
    protocol_start_epoch: int,
    now_epoch: int,
) -> tuple[BuzzOperationsCommand, ...]:
    try:
        canonical_channel_id = str(uuid.UUID(channel_id))
    except (ValueError, AttributeError):
        return ()
    if (
        not reviewer_pubkeys
        or any(not _LOWER_HEX_64.fullmatch(key) for key in reviewer_pubkeys)
        or not _LOWER_HEX_64.fullmatch(service_pubkey)
        or service_pubkey in reviewer_pubkeys
        or isinstance(protocol_start_epoch, bool)
        or not isinstance(protocol_start_epoch, int)
        or not 1 <= protocol_start_epoch <= 4_294_967_295
        or isinstance(now_epoch, bool)
        or not isinstance(now_epoch, int)
        or now_epoch < protocol_start_epoch
    ):
        return ()

    candidates: list[BuzzOperationsCommand] = []
    seen: set[str] = set()
    for message in messages:
        command = parse_operations_command(message.content)
        if (
            command is None
            or message.event_id in seen
            or message.kind != 9
            or message.pubkey not in reviewer_pubkeys
            or message.pubkey == service_pubkey
            or not _exact_channel_tag(message, canonical_channel_id)
            or not protocol_start_epoch <= message.created_at <= now_epoch + 300
        ):
            continue
        reply_to = _reply_target(message)
        if command == "hold":
            if reply_to is None:
                continue
        elif any(tag and tag[0] == "e" for tag in message.tags):
            continue
        seen.add(message.event_id)
        command_sha = hashlib.sha256(
            (
                "coineasy-buzz-operations-command\0"
                f"{BUZZ_OPERATIONS_PROTOCOL_VERSION}\0{canonical_channel_id}\0"
                f"{message.event_id}\0{message.pubkey}\0{command}\0"
                f"{message.created_at}\0{reply_to or ''}"
            ).encode("utf-8")
        ).hexdigest()
        candidates.append(BuzzOperationsCommand(
            event_id=message.event_id,
            reviewer_pubkey=message.pubkey,
            command=command,
            command_sha256=command_sha,
            created_at_epoch=message.created_at,
            reply_to_event_id=reply_to,
        ))
    candidates.sort(key=lambda candidate: (
        candidate.created_at_epoch, candidate.event_id
    ))
    return tuple(candidates)


__all__ = [
    "BUZZ_OPERATIONS_PROTOCOL_VERSION",
    "BUZZ_OPERATIONS_RESPONSE_TEMPLATE_VERSION",
    "eligible_operations_commands",
    "parse_operations_command",
]
