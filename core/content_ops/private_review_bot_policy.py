"""Read-only destination projection from the existing callback owner's policy.

Trusted operator configuration only, never inferred from a Telegram response.
This projection grants no reviewer, bot permission, enablement or send rights.
The callback signing scope is intentionally distinct from DB HMAC bindings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


class PrivateReviewBotPolicyError(ValueError):
    """Fixed non-sensitive error only."""


@dataclass(frozen=True, repr=False)
class ExistingReviewBotPolicy:
    bot_id: int
    chat_id: int
    room_binding: str
    membership_status: str

    def __post_init__(self):
        if (type(self.bot_id) is not int or not 0 < self.bot_id < 2**52
            or type(self.chat_id) is not int
            or re.fullmatch(r"-100[1-9][0-9]{6,12}", str(self.chat_id)) is None
            or type(self.room_binding) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.room_binding) is None
            or type(self.membership_status) is not str
            or self.membership_status not in {"member", "administrator"}):
            raise PrivateReviewBotPolicyError("private_review_bot_policy_invalid")

    def require_destination(self, *, bot_id, chat_id):
        self.__post_init__()
        if (type(bot_id) is not int or type(chat_id) is not int
            or self.bot_id != bot_id or self.chat_id != chat_id):
            raise PrivateReviewBotPolicyError("private_review_bot_policy_invalid")

    @classmethod
    def from_json(cls, raw, *, bot_id, chat_id):
        try:
            if type(raw) is not str or not 0 < len(raw) <= 2048:
                raise ValueError

            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError
                    value[key] = item
                return value

            value = json.loads(raw, object_pairs_hook=unique)
            if (type(value) is not dict or set(value) != {
                    "schema", "bot_id", "chat_id", "room_binding", "membership_status"}
                or value["schema"] != "existing-review-bot-policy@1"):
                raise ValueError
            policy = cls(value["bot_id"], value["chat_id"], value["room_binding"],
                         value["membership_status"])
            policy.require_destination(bot_id=bot_id, chat_id=chat_id)
            return policy
        except Exception:
            raise PrivateReviewBotPolicyError("private_review_bot_policy_invalid") from None
