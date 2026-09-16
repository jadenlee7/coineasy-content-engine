"""Read-only owner adapter for the LOCAL proposal, not an importer/issuer.

Use only the guarded transaction cursor. Lock order: card (already held),
original evidence, execution approval. No new connection, commit, role switch,
credential lookup, retry or fallback. The runtime roles remain denied access.
"""
from core.content_ops.cancellation_markup_authority import (
    OriginalControlEvidence, MarkupExecutionApproval,
)
from core.content_ops.prompt_receipt import canonical_uuid


class PostgresMarkupAuthorityReader:
    def __init__(self, *, enabled=False):
        self._enabled = enabled

    def __call__(self, *, cursor=None, card_id=None, attempt_id=None, actor_id=None):
        if self._enabled is not True:
            return None, None
        try:
            if not all(canonical_uuid(v) for v in (card_id,attempt_id,actor_id)):
                raise ValueError('invalid identity')
            cursor.execute('''select card_id::text,receipt_sha256,parent_binding_sha256,
                bot_id,chat_id,message_id,thread_id,delivered_at,message_date,text_sha256,
                entities_json,markup_json
                from private.content_ops_button_control_evidence where card_id=%s::uuid for share''',
                (card_id,))
            evidence = cursor.fetchone()
            if evidence is None:
                return None, None
            cursor.execute('''select approval_id::text,attempt_id::text,card_id::text,
                actor_id::text,human_binding,plan_seal,original_receipt_sha256,action,
                approved_at,expires_at,active
                from private.content_ops_button_markup_approvals
                where card_id=%s::uuid and attempt_id=%s::uuid and actor_id=%s::uuid for share''',
                (card_id,attempt_id,actor_id))
            approval = cursor.fetchone()
            if approval is None:
                return None, None
            # DTO semantic checks are performed by ExactCancellationMarkupAuthority.
            return OriginalControlEvidence(*evidence), MarkupExecutionApproval(*approval)
        except Exception:
            raise ValueError('cancellation_markup_owner_read_refused') from None
