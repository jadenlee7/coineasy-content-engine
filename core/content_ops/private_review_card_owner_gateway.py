"""Default-OFF, one-shot owner adapter for the scoped review gateway.

No database connection, service-role key, credential discovery or Telegram
call exists here. A lost owner acknowledgement consumes that step and cannot
be retried by this instance. The SQL and Netlify route remain local proposals.
"""
from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from core.content_ops.private_review_card_gateway import ButtonCanaryGateway


_HASH = re.compile(r"[a-f0-9]{64}\Z")


class GatewayPrivateCardOwnerError(RuntimeError):
    """Fixed status code; never include SQL, private copy or credentials."""


def _uuid(value):
    try:
        return type(value) is str and str(UUID(value)) == value and UUID(value).int != 0
    except (ValueError, AttributeError):
        return False


def _sha(value):
    return type(value) is str and _HASH.fullmatch(value) is not None


def _stamp(value):
    if type(value) is not str or not 20 <= len(value) <= 40:
        return False
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.utcoffset() is not None
    except ValueError:
        return False


class GatewayPrivateCardOwner:
    def __init__(self, gateway: ButtonCanaryGateway, *, enabled=False):
        if type(gateway) is not ButtonCanaryGateway or type(enabled) is not bool:
            raise GatewayPrivateCardOwnerError("private_card_owner_configuration_invalid")
        self.gateway = gateway
        self._enabled = enabled
        self._prepare_attempted = False
        self._review_id = None
        self._outbox_id = None
        self._claim_token = None
        self._bind_attempted = False
        self._bound = False
        self._card_id = None
        self._reserve_attempted: set[int] = set()
        self._confirm_attempted: set[int] = set()
        self._pending = None
        self._confirmed = 0
        self._register_attempted = False
        self._terminal_attempted = False

    async def _step(self, name, args, keys):
        if not self._enabled:
            raise GatewayPrivateCardOwnerError("private_card_owner_disabled")
        try:
            value = await self.gateway.owner_step(name, args)
            if (type(value) is not dict or set(value) != keys
                or value.get("execution_authorized") is not False):
                raise ValueError
            return value
        except Exception:
            raise GatewayPrivateCardOwnerError("private_card_owner_outcome_unknown") from None

    async def prepare_review(self, *, workspace_id, outbox_id, claim_token,
                             content_version_id, review_id):
        if not all(_uuid(value) for value in (workspace_id, outbox_id,
                claim_token, content_version_id, review_id)) or (
                content_version_id != self.gateway.content_version_id):
            raise GatewayPrivateCardOwnerError("private_card_owner_arguments_invalid")
        if self._prepare_attempted:
            raise GatewayPrivateCardOwnerError("private_card_owner_replay_denied")
        self._prepare_attempted = True
        receipt = await self._step("prepare", {"outbox_id": outbox_id,
            "claim_token": claim_token, "review_id": review_id},
            {"status", "review_id", "version_fingerprint", "epoch", "state",
             "expires_at", "execution_authorized"})
        if (receipt["status"] != "review_prepared" or receipt["review_id"] != review_id
            or not _sha(receipt["version_fingerprint"])
            or type(receipt["epoch"]) is not int or receipt["epoch"] != 0
            or receipt["state"] != "active" or not _stamp(receipt["expires_at"])):
            raise GatewayPrivateCardOwnerError("private_card_owner_outcome_unknown")
        self._review_id = review_id
        self._outbox_id = outbox_id
        self._claim_token = claim_token
        return receipt

    async def bind_outbox(self, *, review_id, outbox_id, claim_token, packet_sha256):
        if not (_uuid(review_id) and _uuid(outbox_id) and _uuid(claim_token)
                and _sha(packet_sha256)):
            raise GatewayPrivateCardOwnerError("private_card_owner_arguments_invalid")
        if (self._bind_attempted or review_id != self._review_id
            or outbox_id != self._outbox_id or claim_token != self._claim_token):
            raise GatewayPrivateCardOwnerError("private_card_owner_replay_denied")
        self._bind_attempted = True
        receipt = await self._step("bind", {"review_id": review_id,
            "outbox_id": outbox_id, "claim_token": claim_token,
            "packet_sha256": packet_sha256}, {"status", "execution_authorized"})
        if receipt != {"status": "bound", "execution_authorized": False}:
            raise GatewayPrivateCardOwnerError("private_card_owner_outcome_unknown")
        self._bound = True
        return receipt

    async def reserve_part(self, *, review_id, card_id, part_index, payload_sha256):
        if not (_uuid(review_id) and _uuid(card_id) and type(part_index) is int
                and 0 <= part_index <= 3 and _sha(payload_sha256)):
            raise GatewayPrivateCardOwnerError("private_card_owner_arguments_invalid")
        if (not self._bound or review_id != self._review_id
            or part_index != self._confirmed or self._pending is not None
            or part_index in self._reserve_attempted
            or (self._card_id is not None and card_id != self._card_id)):
            raise GatewayPrivateCardOwnerError("private_card_owner_replay_denied")
        self._reserve_attempted.add(part_index)
        receipt = await self._step("reserve", {"review_id": review_id,
            "card_id": card_id, "part_index": part_index,
            "payload_sha256": payload_sha256},
            {"status", "new_attempt", "execution_authorized"})
        if (receipt["status"] != "reserved"
            or receipt["new_attempt"] is not True):
            raise GatewayPrivateCardOwnerError("private_card_owner_outcome_unknown")
        self._pending = (part_index, card_id, payload_sha256)
        self._card_id = card_id
        return receipt

    async def confirm_part(self, *, review_id, card_id, part_index, payload_sha256,
                           message_id, message_binding, response_sha256, observed_at):
        if not (_uuid(review_id) and _uuid(card_id) and type(part_index) is int
                and 0 <= part_index <= 3 and _sha(payload_sha256)
                and type(message_id) is int and 0 < message_id < 2**53
                and _sha(message_binding) and _sha(response_sha256)
                and _stamp(observed_at)):
            raise GatewayPrivateCardOwnerError("private_card_owner_arguments_invalid")
        if (review_id != self._review_id
            or self._pending != (part_index, card_id, payload_sha256)
            or part_index in self._confirm_attempted):
            raise GatewayPrivateCardOwnerError("private_card_owner_replay_denied")
        self._confirm_attempted.add(part_index)
        receipt = await self._step("confirm", {"review_id": review_id,
            "card_id": card_id, "part_index": part_index,
            "payload_sha256": payload_sha256, "message_id": message_id,
            "message_binding": message_binding,
            "response_sha256": response_sha256,
            "observed_at": observed_at},
            {"status", "new_confirmation", "execution_authorized"})
        if (receipt["status"] != "confirmed"
            or receipt["new_confirmation"] is not True):
            raise GatewayPrivateCardOwnerError("private_card_owner_outcome_unknown")
        self._pending = None
        self._confirmed += 1
        return receipt

    async def register_card(self, evidence):
        keys = {"target_review_id", "target_card_id", "expected_fingerprint",
                "target_epoch", "target_bindings", "target_parts",
                "controls_payload_sha256", "response_sha256s", "delivered", "expires"}
        if (type(evidence) is not dict or set(evidence) != keys
            or not _uuid(evidence["target_review_id"])
            or not _uuid(evidence["target_card_id"])
            or not _sha(evidence["expected_fingerprint"])
            or type(evidence["target_epoch"]) is not int
            or evidence["target_epoch"] != 0
            or type(evidence["target_bindings"]) is not dict
            or type(evidence["target_parts"]) is not list
            or len(evidence["target_parts"]) != 3
            or type(evidence["response_sha256s"]) is not list
            or len(evidence["response_sha256s"]) != 4
            or not all(_sha(value) for value in evidence["response_sha256s"])
            or not _sha(evidence["controls_payload_sha256"])
            or not _stamp(evidence["delivered"]) or not _stamp(evidence["expires"])):
            raise GatewayPrivateCardOwnerError("private_card_owner_arguments_invalid")
        if (self._register_attempted or self._confirmed != 4
            or evidence["target_review_id"] != self._review_id
            or evidence["target_card_id"] != self._card_id):
            raise GatewayPrivateCardOwnerError("private_card_owner_replay_denied")
        self._register_attempted = True
        receipt = await self._step("register", {"review_id": evidence["target_review_id"],
            "card_id": evidence["target_card_id"],
            "expected_fingerprint": evidence["expected_fingerprint"],
            "epoch": evidence["target_epoch"],
            "bindings": evidence["target_bindings"], "parts": evidence["target_parts"],
            "controls_payload_sha256": evidence["controls_payload_sha256"],
            "response_sha256s": evidence["response_sha256s"],
            "delivered": evidence["delivered"], "expires": evidence["expires"]},
            {"status", "card_id", "reused", "execution_authorized"})
        if (receipt["status"] != "card_recorded"
            or receipt["card_id"] != evidence["target_card_id"]
            or receipt["reused"] is not False):
            raise GatewayPrivateCardOwnerError("private_card_owner_outcome_unknown")
        return receipt

    async def read_terminal(self, *, review_id, card_id, outbox_id):
        if not (_uuid(review_id) and _uuid(card_id) and _uuid(outbox_id)):
            raise GatewayPrivateCardOwnerError("private_card_owner_arguments_invalid")
        if (not self._register_attempted or self._terminal_attempted
            or review_id != self._review_id or card_id != self._card_id
            or outbox_id != self._outbox_id):
            raise GatewayPrivateCardOwnerError("private_card_owner_replay_denied")
        self._terminal_attempted = True
        receipt = await self._step("terminal", {"review_id": review_id,
            "card_id": card_id, "outbox_id": outbox_id},
            {"status", "card_id", "outbox_id", "execution_authorized"})
        if receipt != {"status": "sent", "card_id": card_id,
                       "outbox_id": outbox_id, "execution_authorized": False}:
            raise GatewayPrivateCardOwnerError("private_card_owner_outcome_unknown")
        return receipt
