"""Default-OFF DB owner for an unmounted final review-card courier.

Each fixed SQL call uses an injected, fresh transaction. A method returns
only after the connection context commits. No credentials, provider client,
runtime route, approval, or publication authority are provided here.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from uuid import UUID


_SHA = re.compile(r"[a-f0-9]{64}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")


class FinalCardOwnerError(RuntimeError):
    """Fixed, non-sensitive status code only."""


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _release(value):
    return type(value) is str and _RELEASE.fullmatch(value) is not None


def _stamp(value):
    if type(value) is not str or not 20 <= len(value) <= 40:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


class PostgresFinalCardOwner:
    """Use only with a separately authorized, minimally granted DB identity."""

    def __init__(self, connection_factory, *, enabled=False):
        if not callable(connection_factory) or type(enabled) is not bool:
            raise FinalCardOwnerError("final_card_owner_configuration_invalid")
        self._factory = connection_factory
        self._enabled = enabled

    def _call(self, query, args, *, expected_keys):
        if not self._enabled:
            raise FinalCardOwnerError("final_card_owner_disabled")
        try:
            with self._factory() as connection:
                if connection.autocommit is not False:
                    raise FinalCardOwnerError("final_card_owner_transaction_required")
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), "
                                   "set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute(query, args)
                    row = cursor.fetchone()
                    if (type(row) not in (tuple, list) or len(row) != 1
                        or type(row[0]) is not dict
                        or set(row[0]) != expected_keys
                        or row[0]["execution_authorized"] is not False):
                        raise FinalCardOwnerError("final_card_owner_outcome_unknown")
                    receipt = row[0]
            # A lost commit acknowledgement raises above. The caller must
            # treat that step as uncertain and must not retry a provider send.
            return receipt
        except Exception:
            raise FinalCardOwnerError("final_card_owner_outcome_unknown") from None

    async def reserve_delivery(self, *, delivery_id, review_id, parent_card_id,
                               actor_id, version_fingerprint, bot_binding,
                               room_binding, human_binding, snapshot_sha256,
                               packet_sha256, release_sha,
                               telegram_route_binding, typefully_route_binding):
        if (not all(_uuid(v) for v in
                    (delivery_id, review_id, parent_card_id, actor_id))
            or not all(_sha(v) for v in
                    (version_fingerprint, bot_binding, room_binding,
                     human_binding, snapshot_sha256, packet_sha256,
                     telegram_route_binding, typefully_route_binding))
            or not _release(release_sha)):
            raise FinalCardOwnerError("final_card_owner_arguments_invalid")
        receipt = await asyncio.to_thread(self._call,
            "select private.reserve_content_ops_final_card_delivery("
            "%s::uuid,%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (delivery_id, review_id, parent_card_id, actor_id,
             version_fingerprint, bot_binding, room_binding, human_binding,
             snapshot_sha256, packet_sha256, release_sha,
             telegram_route_binding, typefully_route_binding),
            expected_keys={"status", "delivery_id", "expires_at",
                           "execution_authorized"})
        if (receipt["status"] != "delivery_reserved"
            or receipt["delivery_id"] != delivery_id
            or not _stamp(receipt["expires_at"])):
            raise FinalCardOwnerError("final_card_owner_outcome_unknown")
        return receipt

    async def begin_part(self, *, delivery_id, part_index, payload_sha256):
        if (not _uuid(delivery_id) or type(part_index) is not int
            or not 0 <= part_index <= 3 or not _sha(payload_sha256)):
            raise FinalCardOwnerError("final_card_owner_arguments_invalid")
        receipt = await asyncio.to_thread(self._call,
            "select private.begin_content_ops_final_card_part("
            "%s::uuid,%s::smallint,%s)",
            (delivery_id, part_index, payload_sha256),
            expected_keys={"status", "reused", "execution_authorized"})
        if (receipt["status"] not in
                ("attempt_recorded", "delivery_unknown", "confirmed")
            or type(receipt["reused"]) is not bool
            or (receipt["status"] == "attempt_recorded") == receipt["reused"]):
            raise FinalCardOwnerError("final_card_owner_outcome_unknown")
        return receipt

    async def confirm_part(self, *, delivery_id, part_index, payload_sha256,
                           message_binding, response_sha256):
        if (not _uuid(delivery_id) or type(part_index) is not int
            or not 0 <= part_index <= 3
            or not all(_sha(v) for v in
                (payload_sha256, message_binding, response_sha256))):
            raise FinalCardOwnerError("final_card_owner_arguments_invalid")
        receipt = await asyncio.to_thread(self._call,
            "select private.confirm_content_ops_final_card_part("
            "%s::uuid,%s::smallint,%s,%s,%s)",
            (delivery_id, part_index, payload_sha256,
             message_binding, response_sha256),
            expected_keys={"status", "reused", "execution_authorized"})
        if receipt["status"] != "confirmed" or type(receipt["reused"]) is not bool:
            raise FinalCardOwnerError("final_card_owner_outcome_unknown")
        return receipt

    async def register_card(self, *, delivery_id):
        if not _uuid(delivery_id):
            raise FinalCardOwnerError("final_card_owner_arguments_invalid")
        receipt = await asyncio.to_thread(self._call,
            "select private.register_content_ops_final_card(%s::uuid)",
            (delivery_id,),
            expected_keys={"status", "card_id", "reused",
                           "execution_authorized"})
        if (receipt["status"] != "card_registered"
            or receipt["card_id"] != delivery_id
            or type(receipt["reused"]) is not bool):
            raise FinalCardOwnerError("final_card_owner_outcome_unknown")
        return receipt

    async def read_registered(self, *, delivery_id):
        if not _uuid(delivery_id):
            raise FinalCardOwnerError("final_card_owner_arguments_invalid")
        receipt = await asyncio.to_thread(self._call,
            "select private.read_content_ops_final_card_terminal(%s::uuid)",
            (delivery_id,),
            expected_keys={"status", "card_id", "execution_authorized"})
        if (receipt["status"] not in ("card_registered", "not_registered")
            or receipt["card_id"] !=
                (delivery_id if receipt["status"] == "card_registered" else None)):
            raise FinalCardOwnerError("final_card_owner_outcome_unknown")
        return receipt
