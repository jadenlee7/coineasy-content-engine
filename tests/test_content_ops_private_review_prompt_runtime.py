"""Offline composition checks; no DB or Telegram calls."""
import pytest

from core.content_ops.private_review_prompt_courier import PrivateReviewPromptCourier
from core.content_ops.private_review_prompt_runtime import compose_private_prompt_factory
from core.content_ops.review_edit_ingress import EditBindings


BOT, ROOM = 123456, -1001234567890
TOKEN = str(BOT) + ":" + "x" * 32
BINDINGS = EditBindings(b"b" * 32)


def no_connection():
    raise AssertionError("DB connection during composition")


@pytest.mark.parametrize("enabled", [False, None, "true", 1])
def test_default_off_never_inspects_dependencies(enabled):
    assert compose_private_prompt_factory(enabled=enabled,
        connection_factory=object(), bindings=object(), bot_token=object(),
        bot_id=object(), chat_id=object()) is None


def test_on_builds_fresh_one_shot_couriers_without_io():
    factory = compose_private_prompt_factory(enabled=True,
        connection_factory=no_connection, bindings=BINDINGS,
        bot_token=TOKEN, bot_id=BOT, chat_id=ROOM)
    first, second = factory(), factory()
    assert type(first) is type(second) is PrivateReviewPromptCourier
    assert first is not second and first._owner is not second._owner
    assert first._sender is not second._sender
    assert first._sender._bot_id == second._sender._bot_id == BOT
    assert first._sender._chat_id == second._sender._chat_id == ROOM


@pytest.mark.parametrize("change", ["connection_factory", "bindings", "bot_token",
                                      "bot_id", "chat_id"])
def test_on_rejects_invalid_configuration_before_any_io(change):
    values = dict(connection_factory=no_connection, bindings=BINDINGS,
                  bot_token=TOKEN, bot_id=BOT, chat_id=ROOM)
    values[change] = object()
    with pytest.raises(ValueError, match="^private_prompt_runtime_invalid$"):
        compose_private_prompt_factory(enabled=True, **values)
