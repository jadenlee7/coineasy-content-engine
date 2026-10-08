"""Default-OFF ce2 bridge injected into the EXISTING authenticated update owner.

No poller, webhook, credential lookup, signing-key provisioning, acknowledgments,
publisher, or startup is installed here. A queue receipt is never send evidence.
Uncertain callback outcomes are quarantined in this instance, never retried.
The existing poller must retain/reconcile unknown callbacks across restarts;
the database owner additionally pins the original callback key durably.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re

from core.content_ops.final_publication_confirmation import (
    FinalConfirmationSigner, VerifiedFinalCallback, handle_final_confirmation,
)
from core.content_ops.final_publication_owner import PostgresFinalPublicationOwner
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import (
    IngressPolicy, MAX_BODY_BYTES, ReviewIngressError, _parse_callback,
)

_TOKEN = re.compile(r"ce2:[A-Za-z0-9_-]{51}\Z")
_UUID = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}\Z")
_RELEASE = re.compile(r"[a-f0-9]{40}\Z")
_UNSUPPORTED = frozenset({
    "message_thread_id", "is_topic_message", "reply_to_message", "external_reply",
    "forward_origin", "forward_from", "forward_from_chat", "forward_date",
    "forward_sender_name", "sender_chat", "via_bot", "business_connection_id",
    "is_automatic_forward", "is_from_offline", "guest_query_id", "reply_to_story",
})
_UNKNOWN = {"status": "outcome_unknown", "execution_authorized": False,
            "public_send_attempted": False}


class PollingFinalPublicationAdapter:
    def __init__(self, *, enabled=False, policy=None, signer=None,
                 review_signer=None, bindings=None, owner=None,
                 runtime_release_sha=None):
        self.enabled = enabled is True
        self.policy, self.signer, self.bindings = policy, signer, bindings
        self.owner, self.runtime_release_sha = owner, runtime_release_sha
        self._inflight: set[str] = set()
        self._unknown: set[str] = set()
        if not self.enabled:
            return
        if (type(policy) is not IngressPolicy
            or type(signer) is not FinalConfirmationSigner
            or type(review_signer) is not ButtonSigner
            or type(bindings) is not EditBindings
            or type(owner) is not PostgresFinalPublicationOwner
            or owner._enabled is not True
            or type(runtime_release_sha) is not str
            or not _RELEASE.fullmatch(runtime_release_sha)
            or owner._release_sha != runtime_release_sha):
            raise ReviewIngressError("final_polling_configuration_invalid")
        policy.validate()
        if (not all(_UUID.fullmatch(actor) for _, actor in policy.reviewers)
            or policy.room_binding != bindings.digest("room", policy.bot_id, policy.chat_id)
            or any(hmac.compare_digest(signer._key, other)
                   for other in (review_signer._key, bindings._key,
                                 policy.webhook_secret.encode("ascii")))):
            raise ReviewIngressError("final_polling_configuration_invalid")

    def _event(self, update, now):
        # Validation precedes any owner access. Reuse the established exact
        # Telegram sender/room/parser contract, but never admit the ce1 token.
        try:
            if type(update) is not dict:
                raise ValueError()
            raw = json.dumps(update, ensure_ascii=True, allow_nan=False).encode()
            if not 0 < len(raw) <= MAX_BODY_BYTES:
                raise ValueError()
            normalized = json.loads(raw)
            query = normalized["callback_query"]
            token = query["data"]
            if type(token) is not str or not _TOKEN.fullmatch(token):
                raise ValueError()
            message = query["message"]
            if type(message) is not dict or _UNSUPPORTED & message.keys():
                raise ValueError()
            # Private copies only. The trusted Telegram update remains intact.
            query["data"] = token[4:]
            encoded = json.dumps(normalized, ensure_ascii=True).encode()
        except Exception:
            raise ReviewIngressError("final_polling_callback_invalid") from None
        headers = [("Content-Type", "application/json"),
                   ("X-Telegram-Bot-Api-Secret-Token", self.policy.webhook_secret)]
        query, actor = _parse_callback(encoded, headers, self.policy, now)
        if not _UUID.fullmatch(actor):
            raise ReviewIngressError("final_polling_callback_invalid")
        return VerifiedFinalCallback(query["id"], actor, False,
            self.bindings.digest("room", self.policy.bot_id, self.policy.chat_id),
            self.bindings.digest("final-card-message@1", self.policy.bot_id,
                                 self.policy.chat_id, query["message"]["message_id"]),
            self.bindings.digest("bot", self.policy.bot_id),
            self.bindings.digest("human", self.policy.bot_id, query["from"]["id"]),
            token)

    async def handle_callback(self, update, *, now):
        if not self.enabled:
            return {"status": "disabled", "execution_authorized": False,
                    "public_send_attempted": False}
        event = self._event(update, now)
        callback_key = hashlib.sha256(event.callback_id.encode()).hexdigest()
        if (callback_key in self._unknown or callback_key in self._inflight
            or len(self._unknown) + len(self._inflight) >= 256):
            return dict(_UNKNOWN)
        self._inflight.add(callback_key)
        try:
            result = await asyncio.to_thread(handle_final_confirmation, event,
                enabled=True, signer=self.signer, owner=self.owner,
                allowed_reviewers=frozenset(actor for _, actor in self.policy.reviewers),
                room_binding=event.room_binding,
                runtime_release_sha=self.runtime_release_sha, now=now)
            if result.get("status") not in {"channels_queued", "held"}:
                raise ValueError()
            return {"status": result["status"], "execution_authorized": False,
                    "public_send_attempted": False}
        except BaseException as exc:
            # Cancellation may interrupt the wait while the owner thread commits.
            # Mark unknown before propagation. Never retry or read-and-reapply.
            self._unknown.add(callback_key)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                raise
            return dict(_UNKNOWN)
        finally:
            self._inflight.discard(callback_key)
