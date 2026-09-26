"""Unmounted, default-OFF PostgreSQL adapter for a private final decision.

The injected snapshot reader must authenticate and project the registered card
by exact room, message, bot and human bindings. This adapter never approves,
queues, or sends a public post. A lost commit acknowledgement is unknown and
must be reconciled using the original card and callback key, never a new key.
"""
from __future__ import annotations

import re
from uuid import UUID

from core.content_ops.final_publication_confirmation import FinalConfirmationSnapshot


_SHA = re.compile(r"[a-f0-9]{64}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")


class FinalDecisionOwnerError(RuntimeError):
    """Fixed non-sensitive status code only."""


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


class PostgresFinalDecisionOwner:
    """Requires a separately reviewed, least-privilege DB identity."""

    def __init__(self, connection_factory, snapshot_reader, *, release_sha, enabled=False):
        if (not callable(connection_factory) or not callable(snapshot_reader)
            or type(release_sha) is not str or _RELEASE.fullmatch(release_sha) is None
            or type(enabled) is not bool):
            raise FinalDecisionOwnerError("final_decision_owner_configuration_invalid")
        self._factory = connection_factory
        self._reader = snapshot_reader
        self._release_sha = release_sha
        self._enabled = enabled

    def read_confirmation(self, room_binding, message_binding, bot_binding, human_binding):
        if not self._enabled:
            raise FinalDecisionOwnerError("final_decision_owner_disabled")
        if not all(_sha(value) for value in
                   (room_binding, message_binding, bot_binding, human_binding)):
            raise FinalDecisionOwnerError("final_decision_owner_arguments_invalid")
        try:
            result = self._reader(room_binding, message_binding,
                                  bot_binding, human_binding)
            if type(result) is not FinalConfirmationSnapshot:
                raise ValueError("invalid snapshot")
            result.validate()
            if result.release_sha != self._release_sha:
                raise ValueError("release conflict")
            return result
        except Exception:
            raise FinalDecisionOwnerError("final_decision_owner_read_unknown") from None

    def _call(self, query, args, keys):
        try:
            with self._factory() as connection:
                if connection.autocommit is not False:
                    raise ValueError("transaction required")
                with connection.cursor() as cursor:
                    cursor.execute("select set_config('lock_timeout','5000',true), "
                                   "set_config('statement_timeout','10000',true)")
                    cursor.fetchone()
                    cursor.execute(query, args)
                    row = cursor.fetchone()
                    if (type(row) not in (tuple, list) or len(row) != 1
                        or type(row[0]) is not dict or set(row[0]) != keys
                        or row[0]["execution_authorized"] is not False):
                        raise ValueError("invalid receipt")
                    receipt = row[0]
            return receipt
        except Exception:
            raise FinalDecisionOwnerError("final_decision_owner_outcome_unknown") from None

    def apply_final_decision(self, *, snapshot_sha256, version_id, card_id,
                             version_fingerprint, message_binding, bot_binding,
                             room_binding, human_binding, runtime_release_sha,
                             reviewer_id, action, idempotency_key):
        if not self._enabled:
            raise FinalDecisionOwnerError("final_decision_owner_disabled")
        if (not all(_uuid(v) for v in (version_id, card_id, reviewer_id))
            or not all(_sha(v) for v in (snapshot_sha256, version_fingerprint,
                 message_binding, bot_binding, room_binding, human_binding,
                 idempotency_key))
            or runtime_release_sha != self._release_sha
            or action not in ("confirm_publication", "hold")):
            raise FinalDecisionOwnerError("final_decision_owner_arguments_invalid")
        result = self._call(
            "select private.record_content_ops_final_decision("
            "%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (card_id, reviewer_id, version_id, version_fingerprint,
             snapshot_sha256, message_binding, bot_binding, room_binding,
             human_binding, runtime_release_sha, action, idempotency_key),
            {"status", "decision_id", "reused", "execution_authorized"})
        if (result["status"] not in ("held", "confirmed_pending_publication_owner")
            or not _uuid(result["decision_id"]) or type(result["reused"]) is not bool):
            raise FinalDecisionOwnerError("final_decision_owner_outcome_unknown")
        return {key: result[key] for key in ("status", "decision_id", "reused")}

    def read_terminal(self, *, card_id, reviewer_id, idempotency_key):
        if not self._enabled:
            raise FinalDecisionOwnerError("final_decision_owner_disabled")
        if not _uuid(card_id) or not _uuid(reviewer_id) or not _sha(idempotency_key):
            raise FinalDecisionOwnerError("final_decision_owner_arguments_invalid")
        result = self._call(
            "select private.read_content_ops_final_decision_terminal("
            "%s::uuid,%s::uuid,%s)",
            (card_id, reviewer_id, idempotency_key),
            {"status", "decision_id", "execution_authorized"})
        if (result["status"] not in ("not_recorded", "held",
                "confirmed_pending_publication_owner")
            or (result["decision_id"] is None) != (result["status"] == "not_recorded")
            or (result["decision_id"] is not None and not _uuid(result["decision_id"]))):
            raise FinalDecisionOwnerError("final_decision_owner_outcome_unknown")
        return result
