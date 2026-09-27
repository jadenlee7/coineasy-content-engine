"""Trusted in-process bridge for the existing Telegram update owner.

No second poller/webhook, credentials lookup, sends, startup, or HTTP endpoint.
Only the authenticated Telegram polling process may call this adapter. Its
owner factories must open fresh transactions per call and verify registrations
and active permissions again. Runtime composition is not installed here.
"""
from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from core.content_ops.review_ingress import (
    IngressPolicy, MAX_BODY_BYTES, ReviewIngressError, handle_review_webhook,
)
from core.content_ops.review_edit_ingress import handle_edit_reply_webhook

PRIVATE_CALLBACK_PREFIX = "ce1:"


class PollingReviewAdapter:
    def __init__(self, *, enabled=False, policy=None, signer=None,
                 review_owner=None, edit_owner=None, edit_bindings=None,
                 transactional_review_owner=None, transactional_reply_owner=None,
                 prompt_courier_factory=None):
        self.enabled = enabled is True
        self.policy, self.signer = policy, signer
        self.review_owner, self.edit_owner = review_owner, edit_owner
        self.edit_bindings = edit_bindings
        self.transactional_review_owner = transactional_review_owner
        self.transactional_reply_owner = transactional_reply_owner
        self.prompt_courier_factory = prompt_courier_factory
        if self.enabled:
            if type(policy) is not IngressPolicy:
                raise ReviewIngressError("review_ingress_policy_invalid")
            policy.validate()
            if transactional_reply_owner is not None:
                from core.content_ops.private_reply_owner import PostgresPrivateReplyOwner
                if edit_owner is not None or type(transactional_reply_owner) is not PostgresPrivateReplyOwner:
                    raise ReviewIngressError("review_ingress_policy_invalid")
            if transactional_review_owner is not None:
                from core.content_ops.private_review_owner import PostgresPrivateReviewOwner
                if review_owner is not None or type(transactional_review_owner) is not PostgresPrivateReviewOwner:
                    raise ReviewIngressError("review_ingress_policy_invalid")
            if prompt_courier_factory is not None and not callable(prompt_courier_factory):
                raise ReviewIngressError("review_ingress_policy_invalid")

    def _payload(self, update, *, callback):
        try:
            if type(update) is not dict:
                raise ValueError()
            body = json.dumps(update, ensure_ascii=True, allow_nan=False).encode()
            if not 0 < len(body) <= MAX_BODY_BYTES:
                raise ValueError()
            # Do not mutate the caller's Update or Telegram data in-place.
            normalized = json.loads(body)
            if callback:
                value = normalized["callback_query"]["data"]
                if type(value) is not str or not value.startswith(PRIVATE_CALLBACK_PREFIX):
                    raise ValueError()
                # The authenticated parser must see the private namespace and
                # reject an unprefixed legacy/public button before owner I/O.
            return json.dumps(normalized, ensure_ascii=True).encode()
        except Exception:
            raise ReviewIngressError("review_ingress_body_invalid") from None

    def _headers(self):
        # Reuse the same validation library, not a network webhook. This secret
        # is internal configuration, NOT taken from user data or sent anywhere.
        return [("Content-Type", "application/json"),
                ("X-Telegram-Bot-Api-Secret-Token", self.policy.webhook_secret)]

    async def handle_callback(self, update, *, now):
        if not self.enabled:
            return {"status": "disabled", "execution_authorized": False}
        handler = (self.transactional_review_owner.handle_update
                   if self.transactional_review_owner is not None else handle_review_webhook)
        options = ({} if self.transactional_review_owner is not None else
                   {'owner': self.review_owner, 'private_only': True})
        payload = self._payload(update, callback=True)
        result = await asyncio.to_thread(handler, enabled=True,
            raw_body=payload, headers=self._headers(),
            policy=self.policy, signer=self.signer, now=now, **options)
        if result.get("status") not in {"checked", "edit_requested", "held"}:
            raise ReviewIngressError("review_ingress_action_unconfirmed")
        if result["status"] != "edit_requested":
            return {"status": "action_recorded", "execution_authorized": False}
        if self.prompt_courier_factory is None:
            # A committed edit request is not a prompt delivery receipt.
            return {"status": "edit_requested", "execution_authorized": False}
        # The callback owner has committed. A duplicate/uncertain action must
        # not create a fresh prompt attempt, even if the caller retries.
        if result.get("reused") is not False:
            return {"status": "prompt_status_unknown", "execution_authorized": False}
        try:
            from core.content_ops.private_review_prompt_courier import PromptCommand
            query = json.loads(payload)["callback_query"]
            courier = self.prompt_courier_factory()
            receipt = await courier.run(PromptCommand(
                query["id"], str(uuid4()), self.policy.bot_id,
                self.policy.chat_id, query["from"]["id"]), enabled=True)
            if receipt == {"status": "prompt_registered", "private_send_attempts": 1,
                           "public_send_attempted": False}:
                return {"status": "prompt_registered", "execution_authorized": False}
        except Exception:
            # Includes a lost reservation/provider/confirmation ACK. Never
            # rerun the action or the courier from this adapter.
            pass
        return {"status": "prompt_status_unknown", "execution_authorized": False}

    async def handle_edit_reply(self, update, *, now):
        if not self.enabled:
            return {"status": "disabled", "execution_authorized": False}
        handler = (self.transactional_reply_owner.handle_update
                   if self.transactional_reply_owner is not None else handle_edit_reply_webhook)
        options = {} if self.transactional_reply_owner is not None else {'owner': self.edit_owner}
        return await asyncio.to_thread(handler, enabled=True,
            raw_body=self._payload(update, callback=False), headers=self._headers(),
            policy=self.policy, bindings=self.edit_bindings, now=now, **options)
