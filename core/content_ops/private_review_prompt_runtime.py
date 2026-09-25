"""Pure, default-OFF composition for the existing private-review poller.

The caller supplies its already-verified restricted DB factory, bindings and
existing review-bot token. No environment lookup, connection, HTTP request,
poller or public publisher is created by composition.
"""
from __future__ import annotations

from core.content_ops.private_review_prompt_courier import (
    PrivateReviewPromptCourier, TelegramPrivatePromptSender,
)
from core.content_ops.private_review_prompt_owner import PostgresPrivatePromptOwner
from core.content_ops.review_edit_ingress import EditBindings


def compose_private_prompt_factory(*, enabled=False, connection_factory=None,
                                   bindings=None, bot_token=None, bot_id=None,
                                   chat_id=None):
    if enabled is not True:
        return None
    if (not callable(connection_factory) or type(bindings) is not EditBindings
        or type(bot_token) is not str or type(bot_id) is not int
        or type(chat_id) is not int):
        raise ValueError("private_prompt_runtime_invalid")
    try:
        # Construction validates the exact bot/room configuration without I/O.
        TelegramPrivatePromptSender(bot_token=bot_token, bot_id=bot_id,
                                    chat_id=chat_id)
    except Exception:
        raise ValueError("private_prompt_runtime_invalid") from None

    def fresh_courier():
        # A new one-shot owner and sender for each non-reused committed action.
        return PrivateReviewPromptCourier(
            PostgresPrivatePromptOwner(connection_factory, bindings),
            TelegramPrivatePromptSender(bot_token=bot_token, bot_id=bot_id,
                                        chat_id=chat_id),
            bindings,
        )

    return fresh_courier
