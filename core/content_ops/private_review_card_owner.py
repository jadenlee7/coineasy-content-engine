"""Default-OFF PostgreSQL owner for one-shot private review card sends.

No connection discovery, credential, worker entrypoint or Telegram I/O. Each
call uses a fresh injected transaction and returns only after its commit ACK.
An uncertain DB result is never converted into permission to send again.
The SQL functions are a local proposal, not a deployed runtime dependency.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from uuid import UUID


_SHA = re.compile(r"[a-f0-9]{64}\Z")


class PrivateCardOwnerError(RuntimeError):
    """Fixed status code; never include SQL, copy, credentials or response."""


def _uuid(value):
    try:
        return (type(value) is str and str(UUID(value)) == value
                and UUID(value).int != 0)
    except (ValueError, AttributeError):
        return False


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _stamp(value):
    if type(value) is not str or not 20 <= len(value) <= 40:
        return False
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.utcoffset() is not None
    except ValueError:
        return False


class PostgresPrivateCardOwner:
    """Use only with a separately authorized, minimally granted DB role."""

    def __init__(self, connection_factory, *, enabled=False):
        if not callable(connection_factory) or type(enabled) is not bool:
            raise PrivateCardOwnerError("private_card_owner_configuration_invalid")
        self._factory = connection_factory
        self._enabled = enabled

    def _call(self, query, args, *, expected_keys):
        if not self._enabled:
            raise PrivateCardOwnerError("private_card_owner_disabled")
        try:
            with self._factory() as connection:
                if connection.autocommit is not False:
                    raise PrivateCardOwnerError("private_card_owner_transaction_required")
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), "
                                   "set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute(query, args)
                    row = cursor.fetchone()
                    if (type(row) not in (tuple, list) or len(row) != 1
                        or type(row[0]) is not dict or set(row[0]) != expected_keys
                        or row[0].get("execution_authorized") is not False):
                        raise PrivateCardOwnerError("private_card_owner_outcome_unknown")
                    receipt = row[0]
            return receipt
        except Exception:
            raise PrivateCardOwnerError("private_card_owner_outcome_unknown") from None

    async def bind_outbox(self, *, review_id, outbox_id, claim_token,
                          packet_sha256):
        if not (_uuid(review_id) and _uuid(outbox_id) and _uuid(claim_token)
                and _sha(packet_sha256)):
            raise PrivateCardOwnerError("private_card_owner_arguments_invalid")
        return await asyncio.to_thread(self._call,
            "select private.bind_content_ops_button_card_outbox("
            "%s::uuid,%s::uuid,%s::uuid,%s)",
            (review_id, outbox_id, claim_token, packet_sha256),
            expected_keys={"status", "execution_authorized"})

    async def reserve_part(self, *, review_id, card_id, part_index,
                           payload_sha256):
        if not (_uuid(review_id) and _uuid(card_id)
                and type(part_index) is int and 0 <= part_index <= 3
                and _sha(payload_sha256)):
            raise PrivateCardOwnerError("private_card_owner_arguments_invalid")
        return await asyncio.to_thread(self._call,
            "select private.reserve_content_ops_button_card_send("
            "%s::uuid,%s::uuid,%s::smallint,%s)",
            (review_id, card_id, part_index, payload_sha256),
            expected_keys={"status", "new_attempt", "execution_authorized"})

    async def confirm_part(self, *, review_id, card_id, part_index,
                           payload_sha256, message_id, message_binding, response_sha256,
                           observed_at):
        if not (_uuid(review_id) and _uuid(card_id)
                and type(part_index) is int and 0 <= part_index <= 3
                and type(message_id) is int and 0 < message_id < 2**53
                and _sha(payload_sha256) and _sha(message_binding)
                and _sha(response_sha256) and _stamp(observed_at)):
            raise PrivateCardOwnerError("private_card_owner_arguments_invalid")
        return await asyncio.to_thread(self._call,
            "select private.confirm_content_ops_button_card_send("
            "%s::uuid,%s::uuid,%s::smallint,%s,%s::bigint,%s,%s,%s::timestamptz)",
            (review_id, card_id, part_index, payload_sha256, message_id,
             message_binding, response_sha256, observed_at),
            expected_keys={"status", "new_confirmation", "execution_authorized"})

    async def read_terminal(self, *, review_id, card_id, outbox_id):
        if not (_uuid(review_id) and _uuid(card_id) and _uuid(outbox_id)):
            raise PrivateCardOwnerError("private_card_owner_arguments_invalid")
        return await asyncio.to_thread(self._call,
            "select private.read_content_ops_button_card_terminal("
            "%s::uuid,%s::uuid,%s::uuid)",
            (review_id, card_id, outbox_id),
            expected_keys={"status", "card_id", "outbox_id",
                           "execution_authorized"})

    async def register_card(self, evidence):
        required = {"target_review_id", "target_card_id", "expected_fingerprint",
                    "target_epoch", "target_bindings", "target_parts",
                    "controls_payload_sha256", "response_sha256s",
                    "delivered", "expires"}
        if (type(evidence) is not dict or set(evidence) != required
            or not _uuid(evidence["target_review_id"])
            or not _uuid(evidence["target_card_id"])
            or not _sha(evidence["expected_fingerprint"])
            or type(evidence["target_epoch"]) is not int
            or evidence["target_epoch"] < 0
            or type(evidence["target_bindings"]) is not dict
            or type(evidence["target_parts"]) is not list
            or len(evidence["target_parts"]) != 3
            or not _sha(evidence["controls_payload_sha256"])
            or type(evidence["response_sha256s"]) is not list
            or len(evidence["response_sha256s"]) != 4
            or not all(_sha(value) for value in evidence["response_sha256s"])
            or not _stamp(evidence["delivered"])
            or not _stamp(evidence["expires"])):
            raise PrivateCardOwnerError("private_card_owner_arguments_invalid")
        try:
            bindings = json.dumps(evidence["target_bindings"], sort_keys=True,
                                  separators=(",", ":"), allow_nan=False)
            parts = json.dumps(evidence["target_parts"], sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
            responses = json.dumps(evidence["response_sha256s"],
                                   separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError):
            raise PrivateCardOwnerError("private_card_owner_arguments_invalid") from None
        if len(bindings) > 4096 or len(parts) > 4096 or len(responses) > 512:
            raise PrivateCardOwnerError("private_card_owner_arguments_invalid")
        return await asyncio.to_thread(self._call,
            "select private.register_content_ops_button_card_from_sends("
            "%s::uuid,%s::uuid,%s,%s::bigint,%s::jsonb,%s::jsonb,%s,%s::jsonb,"
            "%s::timestamptz,%s::timestamptz)",
            (evidence["target_review_id"], evidence["target_card_id"],
             evidence["expected_fingerprint"], evidence["target_epoch"],
             bindings, parts, evidence["controls_payload_sha256"],
             responses, evidence["delivered"], evidence["expires"]),
            expected_keys={"status", "card_id", "reused", "execution_authorized"})
