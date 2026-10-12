"""Default-OFF exact human confirmation to atomic channel-queue admission.

This adapter has no provider client or credentials. Its one database transaction
records the decision, attributed approval, Telegram queue and Typefully daily
slot together. A receipt is queue evidence, never Telegram/X delivery evidence.
Unknown commit outcomes MUST use read_terminal with the original IDs/key.
"""
from __future__ import annotations

from core.content_ops.final_decision_owner import (
    FinalDecisionOwnerError, PostgresFinalDecisionOwner, _sha, _uuid,
)


class PostgresFinalPublicationOwner(PostgresFinalDecisionOwner):
    decision_mode = "telegram_and_typefully_draft"

    def _validate_rpc_receipt(self, receipt):
        self._validate_receipt(receipt, terminal="reused" not in receipt)

    def read_confirmation(self, room_binding, message_binding, bot_binding, human_binding):
        snapshot = super().read_confirmation(
            room_binding, message_binding, bot_binding, human_binding)
        if snapshot.decision_mode != self.decision_mode:
            raise FinalDecisionOwnerError("final_publication_mode_conflict")
        return snapshot

    def apply_final_decision(self, *, snapshot_sha256, version_id, card_id,
                             version_fingerprint, message_binding, bot_binding,
                             room_binding, human_binding, runtime_release_sha,
                             reviewer_id, action, idempotency_key):
        if not self._enabled:
            raise FinalDecisionOwnerError("final_publication_owner_disabled")
        if (not all(_uuid(v) for v in (version_id, card_id, reviewer_id))
            or not all(_sha(v) for v in (snapshot_sha256, version_fingerprint,
                message_binding, bot_binding, room_binding, human_binding, idempotency_key))
            or runtime_release_sha != self._callback_release_sha
            or action not in ("confirm_publication", "hold")):
            raise FinalDecisionOwnerError("final_publication_owner_arguments_invalid")
        result = self._call(
            "select private.record_content_ops_final_publication("
            "%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (card_id, reviewer_id, version_id, version_fingerprint,
             snapshot_sha256, message_binding, bot_binding, room_binding,
             human_binding, self._release_sha, action, idempotency_key,
             runtime_release_sha, self._release_pair_sha256),
            {"status", "decision_id", "approval_id", "publication_id", "job_id",
             "typefully_slot_id", "reused", "execution_authorized"})
        self._validate_receipt(result, terminal=False)
        return {key: result[key] for key in ("status", "decision_id", "reused")}

    @staticmethod
    def _validate_receipt(result, *, terminal):
        allowed = {"held", "channels_queued"} | ({"not_recorded"} if terminal else set())
        ids = ("approval_id", "publication_id", "job_id", "typefully_slot_id")
        if (result["status"] not in allowed
            or (not terminal and type(result["reused"]) is not bool)
            or (result["status"] != "not_recorded" and not _uuid(result["decision_id"]))
            or (result["status"] == "not_recorded" and result["decision_id"] is not None)
            or (result["status"] == "channels_queued" and
                not all(_uuid(result[key]) for key in ids))
            or (result["status"] != "channels_queued" and
                any(result[key] is not None for key in ids))):
            raise FinalDecisionOwnerError("final_publication_owner_outcome_unknown")

    def read_terminal(self, *, card_id, reviewer_id, idempotency_key):
        if not self._enabled:
            raise FinalDecisionOwnerError("final_publication_owner_disabled")
        if not _uuid(card_id) or not _uuid(reviewer_id) or not _sha(idempotency_key):
            raise FinalDecisionOwnerError("final_publication_owner_arguments_invalid")
        result = self._call(
            "select private.read_content_ops_final_publication_terminal("
            "%s::uuid,%s::uuid,%s)", (card_id, reviewer_id, idempotency_key),
            {"status", "decision_id", "approval_id", "publication_id", "job_id",
             "typefully_slot_id", "execution_authorized"})
        self._validate_receipt(result, terminal=True)
        return result
