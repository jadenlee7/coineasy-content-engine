"""Offline actual controller bridge; no poller, HTTP, Telegram or DB writes."""
import asyncio
import copy

import pytest

from core.content_ops.polling_review_adapter import PollingReviewAdapter
from core.content_ops.review_buttons import review_messages
from core.content_ops.review_ingress import ReviewIngressError
import test_content_ops_review_ingress as ingress_fixture
from test_content_ops_review_ingress import NOW
from test_content_ops_review_edit_ingress import (
    Owner as EditOwner, POLICY as EDIT_POLICY, BINDINGS, NOW as EDIT_NOW, update as edit_update,
)


def fixture():
    case = ingress_fixture.ReviewIngressTest(); case.setUp()
    adapter = PollingReviewAdapter(enabled=True, policy=case.policy, signer=case.signer,
                                   review_owner=case.owner)
    return case, adapter


@pytest.mark.parametrize("enabled", [False, None, "true", 1])
def test_off_no_dependency_or_payload_access(enabled):
    a = PollingReviewAdapter(enabled=enabled)
    assert asyncio.run(a.handle_callback(object(), now=None)) == {
        "status": "disabled", "execution_authorized": False}
    assert asyncio.run(a.handle_edit_reply(object(), now=None))["status"] == "disabled"


@pytest.mark.parametrize("action", ["s", "c", "t", "x", "b", "h"])
def test_private_card_namespace_reaches_actual_signed_controller(action):
    case, adapter = fixture()
    u = case.update(action)
    u["callback_query"]["data"] = "ce1:" + u["callback_query"]["data"]
    original = copy.deepcopy(u)
    expected = "edit_requested" if action in {"x", "b", "t"} else "action_recorded"
    assert asyncio.run(adapter.handle_callback(u, now=NOW)) == {
        "status": expected, "execution_authorized": False}
    assert u == original
    assert case.owner.applies == 1 and not case.owner.outbox
    assert asyncio.run(adapter.handle_callback(u, now=NOW))["status"] == expected
    assert len(case.owner.operations) == 1


def test_generated_private_buttons_are_55_bytes_and_webhook_compatible():
    case, _ = fixture()
    card = review_messages(case.owner.current, case.signer, case.policy.room_binding,
                           now=NOW, private_only=True)
    for row in card["controls"]["reply_markup"]["inline_keyboard"]:
        for button in row:
            assert button["callback_data"].startswith("ce1:")
            assert len(button["callback_data"].encode()) == 55
    u = case.update("s")
    u["callback_query"]["data"] = card["controls"]["reply_markup"]["inline_keyboard"][2][0]["callback_data"]
    assert case.run_update(u, private_only=True)["status"] == "checked"
    with pytest.raises(ReviewIngressError):
        case.run_update(u)  # private namespace never enters the public policy


@pytest.mark.parametrize("prefix", ["", "ce1:"])
def test_signed_publication_action_is_rejected_even_after_checks(prefix):
    case, adapter = fixture()
    for action in ("s", "c"):
        asyncio.run(adapter.handle_callback(case.update(action, private=True), now=NOW))
    u = case.update("a"); u["callback_query"]["data"] = prefix + u["callback_query"]["data"]
    before = case.owner.applies
    with pytest.raises(ReviewIngressError):
        asyncio.run(adapter.handle_callback(u, now=NOW))
    assert case.owner.applies == before and not case.owner.outbox


def test_unprefixed_private_button_rejected_before_owner_io():
    case, adapter = fixture()
    with pytest.raises(ReviewIngressError):
        asyncio.run(adapter.handle_callback(case.update("s"), now=NOW))
    assert case.owner.lookups == case.owner.reads == case.owner.applies == 0


@pytest.mark.parametrize("mutation", ["actor", "room", "signature", "registration"])
def test_untrusted_context_does_not_mutate_owner(mutation):
    case, adapter = fixture(); u = case.update(private=True)
    if mutation == "actor": u["callback_query"]["from"]["id"] = 999
    if mutation == "room": u["callback_query"]["message"]["chat"]["id"] = -999
    if mutation == "signature": u["callback_query"]["data"] = "ce1:" + "A" * 51
    if mutation == "registration": case.owner.registered = False
    with pytest.raises(ReviewIngressError):
        asyncio.run(adapter.handle_callback(u, now=NOW))
    assert case.owner.applies == 0


def test_actual_reply_ingress_and_actor_bound_message_key():
    owner = EditOwner()
    adapter = PollingReviewAdapter(enabled=True, policy=EDIT_POLICY,
                                   edit_owner=owner, edit_bindings=BINDINGS)
    assert asyncio.run(adapter.handle_edit_reply(edit_update(), now=EDIT_NOW)) == owner.result
    assert len(owner.events) == 1
    assert owner.events[0].replacement_text == edit_update()["message"]["text"]


def test_oversize_or_malformed_update_has_no_owner_io():
    case, adapter = fixture()
    for u in (None, {"padding": "x"*33000}, {"callback_query": {"data": None}}, {"a": float("nan")}):
        with pytest.raises(ReviewIngressError):
            asyncio.run(adapter.handle_callback(u, now=NOW))
    assert case.owner.reads == case.owner.applies == 0
