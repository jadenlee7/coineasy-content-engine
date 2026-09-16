"""Synthetic, transport-free pre-send planning and response-matching tests."""
from dataclasses import replace
import hashlib
import json

import pytest

from core.content_ops.prompt_attempt import (
    DeliveredPacketPart, PromptPlanError, PromptReviewContext, PromptTarget,
    RegisteredControlCard, plan_prompt_attempt, prompt_instruction,
)
from core.content_ops.prompt_receipt import PromptReceiptError, validate_prompt_response
from core.content_ops.review_edit_ingress import EditBindings


NOW = 1_800_000_000
BINDINGS = EditBindings(b"synthetic-plan-test-key-only-00001")
BOT, CHAT, HUMAN = 101, -202, 303
ERROR = "^prompt_plan_unavailable$"


def uid(n):
    return f"10000000-0000-4000-8000-{n:012d}"


def inputs(client="squid", action="edit_telegram", thread_id=None):
    target = PromptTarget(uid(1), client, uid(2), uid(3), "a" * 64)
    review = PromptReviewContext(target, uid(4), uid(6), uid(5),
        BINDINGS.digest("human", BOT, HUMAN), 1, "b" * 64, action,
        NOW - 10, NOW + 900, actor_active=True)
    parts = tuple(DeliveredPacketPart(kind, BINDINGS.digest("prompt", BOT, CHAT, mid),
        str(mid % 10) * 64) for kind, mid in zip(("image", "telegram", "x"), (11, 12, 13)))
    card = RegisteredControlCard(target, uid(6), uid(4), 0,
        BINDINGS.digest("bot", BOT), BINDINGS.digest("room", BOT, CHAT),
        BINDINGS.digest("prompt", BOT, CHAT, 14), "c" * 64, "d" * 64,
        parts, NOW - 30, NOW + 600, thread_id=thread_id, active=True, status="registered")
    return dict(enabled=True, review=review, card=card, bindings=BINDINGS,
        attempt_id=uid(7), bot_id=BOT, chat_id=CHAT, human_id=HUMAN,
        thread_id=thread_id, now=NOW)


def change_record(args, name, **changes):
    return dict(args, **{name: replace(args[name], **changes)})


class Unreadable:
    def __getattribute__(self, name):
        raise AssertionError("Disabled planner must not inspect inputs")


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "true"])
def test_default_off_does_not_read_inputs(enabled):
    assert plan_prompt_attempt(enabled=enabled, review=Unreadable(), card=Unreadable(),
        bindings=Unreadable(), attempt_id=Unreadable(), now=Unreadable()) is None
    assert plan_prompt_attempt() is None


@pytest.mark.parametrize("client", ["yellow", "babylon", "squid", "origintrail"])
@pytest.mark.parametrize("action", ["edit_telegram", "edit_x"])
def test_four_clients_and_both_channels_produce_exact_pinned_attempt(client, action):
    args = inputs(client, action)
    attempt = plan_prompt_attempt(**args)
    assert attempt.attempt_id == uid(7)
    assert attempt.review_id == uid(4) and attempt.actor_id == uid(5)
    assert attempt.epoch == 1 and attempt.edit_action_key == "b" * 64
    assert attempt.version_fingerprint == args["review"].target.version_fingerprint
    assert attempt.started_at == NOW and attempt.expires_at == NOW + 600
    assert attempt.expected_text_sha256 == hashlib.sha256(prompt_instruction(action).encode()).hexdigest()
    assert len(attempt.packet_receipt_sha256) == 64
    assert attempt.bot_id == BOT and attempt.chat_id == CHAT and attempt.human_id == HUMAN
    assert uid(7) not in repr(attempt) and str(CHAT) not in repr(attempt)
    for record in (args["review"], args["card"], args["card"].target, *args["card"].parts):
        assert uid(1) not in repr(record) and "a" * 64 not in repr(record)


@pytest.mark.parametrize("kind", ["reuse_x", "duplicate_parts", "short", "long", "list", "order", "wrong_kind", "partial", "rejected", "unknown", "not_part", "bad_payload", "bad_message"])
def test_requires_three_distinct_successful_parts_and_separate_registered_control_card(kind):
    args = inputs()
    card = args["card"]
    parts = list(card.parts)
    if kind == "reuse_x": card = replace(card, message_binding=parts[2].message_binding)
    if kind == "duplicate_parts": parts[1] = replace(parts[1], message_binding=parts[0].message_binding)
    if kind == "short": parts.pop()
    if kind == "long": parts.append(parts[-1])
    if kind == "order": parts.reverse()
    if kind == "wrong_kind": parts[0] = replace(parts[0], kind="photo")
    if kind in ("partial", "rejected", "unknown"):
        parts[1] = replace(parts[1], outcome={"partial": "pending", "unknown": "delivery_unknown"}.get(kind, kind))
    if kind == "not_part": parts[0] = {}
    if kind == "bad_payload": parts[0] = replace(parts[0], payload_sha256="g" * 64)
    if kind == "bad_message": parts[0] = replace(parts[0], message_binding="a" * 63)
    card = replace(card, parts=parts if kind == "list" else tuple(parts))
    with pytest.raises(PromptPlanError, match=ERROR): plan_prompt_attempt(**dict(args, card=card))


@pytest.mark.parametrize("field,value", [("workspace_id", uid(20)), ("client_id", "yellow"),
    ("content_item_id", uid(21)), ("content_version_id", uid(22)), ("version_fingerprint", "e" * 64)])
def test_card_and_review_must_match_every_target_field(field, value):
    args = inputs()
    card = replace(args["card"], target=replace(args["card"].target, **{field: value}))
    with pytest.raises(PromptPlanError, match=ERROR): plan_prompt_attempt(**dict(args, card=card))


@pytest.mark.parametrize("field,value", [("workspace_id", "bad"), ("client_id", "other"),
    ("content_item_id", "00000000-0000-0000-0000-000000000000"),
    ("content_version_id", uid(3).replace("-", "")), ("version_fingerprint", "A" * 64)])
def test_matching_but_invalid_target_is_not_accepted(field, value):
    args = inputs()
    target = replace(args["review"].target, **{field: value})
    args["review"] = replace(args["review"], target=target)
    args["card"] = replace(args["card"], target=target)
    with pytest.raises(PromptPlanError, match=ERROR): plan_prompt_attempt(**args)


@pytest.mark.parametrize("name,field,value", [
    ("review", "actor_active", False), ("review", "actor_active", 1),
    ("card", "active", False), ("card", "active", 1),
    ("card", "status", "unregistered"), ("card", "status", "sent"),
    ("review", "state", "active"), ("review", "review_id", uid(10)),
    ("review", "card_registration_id", uid(10)),
    ("review", "card_registration_id", "bad"),
    ("review", "actor_id", "bad"), ("card", "registration_id", "bad"),
    ("review", "edit_action_key", "g" * 64), ("review", "human_binding", "e" * 64),
    ("card", "bot_binding", "e" * 64), ("card", "room_binding", "e" * 64),
    ("card", "packet_receipt_sha256", "short"), ("card", "card_receipt_sha256", "short"),
])
def test_owner_state_identity_registration_and_hashes_are_fail_closed(name, field, value):
    args = change_record(inputs(), name, **{field: value})
    with pytest.raises(PromptPlanError, match=ERROR): plan_prompt_attempt(**args)


@pytest.mark.parametrize("field,value", [("bot_id", 102), ("chat_id", -203),
    ("human_id", 304), ("bot_id", True), ("human_id", True), ("chat_id", True),
    ("chat_id", 202), ("chat_id", -(2**52)), ("human_id", BOT),
    ("thread_id", 5), ("thread_id", True), ("attempt_id", "bad"), ("bindings", None)])
def test_supplied_identity_and_topic_must_match_registered_binding(field, value):
    with pytest.raises(PromptPlanError, match=ERROR):
        plan_prompt_attempt(**dict(inputs(), **{field: value}))


def test_forum_topic_must_match_exactly_and_not_boolean():
    args = inputs(thread_id=5)
    assert plan_prompt_attempt(**args).thread_id == 5
    for topic in (None, 6, True, 0):
        with pytest.raises(PromptPlanError, match=ERROR):
            plan_prompt_attempt(**change_record(args, "card", thread_id=topic))


@pytest.mark.parametrize("card_epoch,review_epoch", [(0, 0), (1, 1), (1, 3),
    (3, 2), (-1, 0), (False, 1), (0, True), (1.0, 2), (1, 2.0)])
def test_card_epoch_must_be_exact_predecessor_of_edit_action_epoch(card_epoch, review_epoch):
    args = change_record(inputs(), "card", epoch=card_epoch)
    args = change_record(args, "review", epoch=review_epoch)
    with pytest.raises(PromptPlanError, match=ERROR): plan_prompt_attempt(**args)


def test_nonzero_card_epoch_and_immediate_successor_are_valid():
    args = change_record(inputs(), "card", epoch=8)
    args = change_record(args, "review", epoch=9)
    assert plan_prompt_attempt(**args).epoch == 9


@pytest.mark.parametrize("name,field,value", [("card", "delivered_at", NOW + 1),
    ("card", "delivered_at", NOW - 5), ("review", "requested_at", NOW + 1),
    ("card", "expires_at", NOW), ("review", "expires_at", NOW),
    ("card", "delivered_at", NOW - 1800), ("review", "requested_at", True),
    ("card", "delivered_at", 0), ("review", "expires_at", 2**32)])
def test_chronology_and_expiry_refuse_stale_future_or_wrong_types(name, field, value):
    with pytest.raises(PromptPlanError, match=ERROR):
        plan_prompt_attempt(**change_record(inputs(), name, **{field: value}))


@pytest.mark.parametrize("now", [None, True, 0, 2**32, float(NOW)])
def test_observation_time_must_be_bounded_integer(now):
    with pytest.raises(PromptPlanError, match=ERROR):
        plan_prompt_attempt(**dict(inputs(), now=now))


@pytest.mark.parametrize("review_expiry,card_expiry,expected", [
    (NOW + 100, NOW + 200, NOW + 100),
    (NOW + 300, NOW + 200, NOW + 200),
    (NOW + 5000, NOW + 4000, NOW + 1770),
])
def test_attempt_expiry_is_minimum_never_extended(review_expiry, card_expiry, expected):
    args = change_record(inputs(), "review", expires_at=review_expiry)
    args = change_record(args, "card", expires_at=card_expiry)
    assert plan_prompt_attempt(**args).expires_at == expected


@pytest.mark.parametrize("field,value", [("registration_id", uid(30)),
    ("packet_receipt_sha256", "e" * 64), ("card_receipt_sha256", "f" * 64),
    ("message_binding", "8" * 64), ("delivered_at", NOW - 31),
    ("expires_at", NOW + 601)])
def test_parent_hash_binds_exact_control_card_and_delivery_lineage(field, value):
    args = inputs()
    original = plan_prompt_attempt(**args)
    if field == "registration_id":
        args = change_record(args, "review", card_registration_id=value)
    changed = plan_prompt_attempt(**change_record(args, "card", **{field: value}))
    assert changed.packet_receipt_sha256 != original.packet_receipt_sha256


@pytest.mark.parametrize("part_index", [0, 1, 2])
@pytest.mark.parametrize("field", ["message_binding", "payload_sha256"])
def test_parent_hash_binds_each_part_payload_and_message(part_index, field):
    args = inputs()
    original = plan_prompt_attempt(**args)
    parts = list(args["card"].parts)
    parts[part_index] = replace(parts[part_index], **{field: "9" * 64})
    changed = plan_prompt_attempt(**change_record(args, "card", parts=tuple(parts)))
    assert changed.packet_receipt_sha256 != original.packet_receipt_sha256


@pytest.mark.parametrize("identity", ["bot", "room", "human"])
def test_consistently_rebound_identity_changes_correct_pinned_fields(identity):
    args = inputs()
    old = plan_prompt_attempt(**args)
    if identity == "bot":
        args["bot_id"] = BOT + 1
        args = change_record(args, "card", bot_binding=BINDINGS.digest("bot", BOT + 1),
            room_binding=BINDINGS.digest("room", BOT + 1, CHAT))
        args = change_record(args, "review", human_binding=BINDINGS.digest("human", BOT + 1, HUMAN))
    if identity == "room":
        args["chat_id"] = CHAT - 1
        args = change_record(args, "card", room_binding=BINDINGS.digest("room", BOT, CHAT - 1))
    if identity == "human":
        args["human_id"] = HUMAN + 1
        args = change_record(args, "review", human_binding=BINDINGS.digest("human", BOT, HUMAN + 1))
    changed = plan_prompt_attempt(**args)
    if identity != "human": assert changed.packet_receipt_sha256 != old.packet_receipt_sha256
    else: assert changed.human_id != old.human_id


@pytest.mark.parametrize("action", [None, True, "approve", "publish", "hold", "edit", "edit_X", ""])
def test_instruction_supports_only_fixed_channel_edit_actions(action):
    with pytest.raises(PromptPlanError, match=ERROR): prompt_instruction(action)
    with pytest.raises(PromptPlanError, match=ERROR):
        plan_prompt_attempt(**change_record(inputs(), "review", action=action))


@pytest.mark.parametrize("action", ["edit_telegram", "edit_x"])
def test_pure_planner_to_response_validator_matches_text_and_parent_digest(action):
    args = inputs(action=action)
    attempt = plan_prompt_attempt(**args)
    response = {"ok": True, "result": {"message_id": 21, "date": NOW,
        "chat": {"id": CHAT, "type": "supergroup"},
        "from": {"id": BOT, "is_bot": True}, "text": prompt_instruction(action)}}
    accepted = validate_prompt_response(enabled=True, attempt=attempt, http_status=200,
        raw_response=json.dumps(response).encode(), bindings=BINDINGS, observed_at=NOW)
    changed_parent = replace(attempt, packet_receipt_sha256="9" * 64)
    other = validate_prompt_response(enabled=True, attempt=changed_parent, http_status=200,
        raw_response=json.dumps(response).encode(), bindings=BINDINGS, observed_at=NOW)
    assert accepted.receipt_sha256 != other.receipt_sha256
    assert accepted.id == attempt.attempt_id and accepted.outcome == "sent"
    response["result"]["text"] += " altered"
    with pytest.raises(PromptReceiptError, match="^prompt_receipt_unconfirmed$"):
        validate_prompt_response(enabled=True, attempt=attempt, http_status=200,
            raw_response=json.dumps(response).encode(), bindings=BINDINGS, observed_at=NOW)
