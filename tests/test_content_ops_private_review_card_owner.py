"""Fake transaction only; no connection to production or local Postgres."""
import asyncio
from unittest.mock import Mock

import pytest

from core.content_ops.private_review_card_owner import (
    PostgresPrivateCardOwner, PrivateCardOwnerError,
)


W = "11111111-1111-4111-8111-111111111111"
V = "33333333-3333-4333-8333-333333333333"
R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"
O = "66666666-6666-4666-8666-666666666666"
T = "77777777-7777-4777-8777-777777777777"
SHA = "a" * 64
AT = "2026-09-23T07:00:03+00:00"


class Cursor:
    def __init__(self, receipt):
        self.receipt = receipt
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, args=()):
        self.statements.append((sql, args))

    def fetchone(self):
        if len(self.statements) == 1:
            return ("5000", "10000")
        return (self.receipt,)


class Connection:
    autocommit = False

    def __init__(self, receipt, *, fail_commit=False):
        self.cursor_value = Cursor(receipt)
        self.fail_commit = fail_commit

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        if self.fail_commit:
            raise OSError("uncertain commit with sensitive data")
        return False

    def cursor(self):
        return self.cursor_value


@pytest.mark.parametrize("method,kwargs", [
    ("prepare_review", dict(workspace_id=W, outbox_id=O, claim_token=T,
                            content_version_id=V, review_id=R)),
    ("bind_outbox", dict(review_id=R, outbox_id=O, claim_token=T,
                         packet_sha256=SHA)),
    ("reserve_part", dict(review_id=R, card_id=C, part_index=0,
                          payload_sha256=SHA)),
    ("confirm_part", dict(review_id=R, card_id=C, part_index=0,
                          payload_sha256=SHA, message_id=123,
                          message_binding=SHA,
                          response_sha256=SHA, observed_at=AT)),
    ("read_terminal", dict(review_id=R, card_id=C, outbox_id=O)),
    ("register_card", dict(evidence={
        "target_review_id": R, "target_card_id": C,
        "expected_fingerprint": SHA, "target_epoch": 0,
        "target_bindings": {"message": SHA},
        "target_parts": [{"kind": "image"}, {"kind": "telegram"},
                         {"kind": "x"}],
        "controls_payload_sha256": SHA,
        "response_sha256s": [SHA] * 4,
        "delivered": AT, "expires": "2026-09-23T07:30:00+00:00",
    })),
])
def test_default_off_never_connects(method, kwargs):
    factory = Mock(side_effect=AssertionError("unexpected DB I/O"))
    owner = PostgresPrivateCardOwner(factory)
    with pytest.raises(PrivateCardOwnerError, match="private_card_owner_disabled"):
        asyncio.run(getattr(owner, method)(**kwargs))
    factory.assert_not_called()


def test_reserve_and_confirm_use_separate_committed_transactions():
    receipts = [
        {"status": "reserved", "new_attempt": True,
         "execution_authorized": False},
        {"status": "confirmed", "new_confirmation": True,
         "execution_authorized": False},
    ]
    connections = []

    def factory():
        conn = Connection(receipts[len(connections)])
        connections.append(conn)
        return conn

    owner = PostgresPrivateCardOwner(factory, enabled=True)

    async def run():
        reserved = await owner.reserve_part(review_id=R, card_id=C,
            part_index=0, payload_sha256=SHA)
        confirmed = await owner.confirm_part(review_id=R, card_id=C,
            part_index=0, payload_sha256=SHA, message_id=123,
            message_binding=SHA,
            response_sha256=SHA, observed_at=AT)
        return reserved, confirmed

    assert asyncio.run(run()) == tuple(receipts)
    assert len(connections) == 2
    assert "reserve_content_ops_button_card_send" in connections[0].cursor_value.statements[1][0]
    assert "confirm_content_ops_button_card_send" in connections[1].cursor_value.statements[1][0]
    assert connections[0].cursor_value.statements[1][1] == (R, C, 0, SHA)
    assert connections[1].cursor_value.statements[1][1][4] == 123


def test_outbox_bind_requires_exact_owner_and_committed_receipt():
    conn = Connection({"status": "bound", "execution_authorized": False})
    owner = PostgresPrivateCardOwner(lambda: conn, enabled=True)
    assert asyncio.run(owner.bind_outbox(review_id=R, outbox_id=O,
        claim_token=T, packet_sha256=SHA)) == {
            "status": "bound", "execution_authorized": False}
    sql, args = conn.cursor_value.statements[1]
    assert "bind_content_ops_button_card_outbox" in sql
    assert args == (R, O, T, SHA)


def test_claimed_outbox_prepares_exact_review_in_one_committed_transaction():
    receipt = {"status": "review_prepared", "review_id": R,
               "version_fingerprint": SHA, "epoch": 0, "state": "active",
               "expires_at": AT,
               "execution_authorized": False}
    conn = Connection(receipt)
    owner = PostgresPrivateCardOwner(lambda: conn, enabled=True)
    assert asyncio.run(owner.prepare_review(workspace_id=W, outbox_id=O,
        claim_token=T, content_version_id=V, review_id=R)) == receipt
    sql, args = conn.cursor_value.statements[1]
    assert "prepare_content_ops_button_review_from_claim" in sql
    assert args == (W, O, T, V, R)


@pytest.mark.parametrize("change", [
    {"status": "reused"},
    {"review_id": C},
    {"version_fingerprint": "bad"},
    {"epoch": True},
    {"state": "held"},
    {"expires_at": "2026-09-23T07:00:03"},
])
def test_prepared_review_receipt_must_match_exact_identity_and_shape(change):
    receipt = {"status": "review_prepared", "review_id": R,
               "version_fingerprint": SHA, "epoch": 0, "state": "active",
               "expires_at": AT,
               "execution_authorized": False}
    receipt.update(change)
    owner = PostgresPrivateCardOwner(lambda: Connection(receipt), enabled=True)
    with pytest.raises(PrivateCardOwnerError, match="private_card_owner_outcome_unknown"):
        asyncio.run(owner.prepare_review(workspace_id=W, outbox_id=O,
            claim_token=T, content_version_id=V, review_id=R))


def test_register_uses_guarded_wrapper_and_fourth_payload_hash():
    evidence = {
        "target_review_id": R, "target_card_id": C,
        "expected_fingerprint": SHA, "target_epoch": 0,
        "target_bindings": {"message": SHA},
        "target_parts": [{"kind": "image"}, {"kind": "telegram"},
                         {"kind": "x"}],
        "controls_payload_sha256": SHA,
        "response_sha256s": [SHA] * 4,
        "delivered": AT, "expires": "2026-09-23T07:30:00+00:00",
    }
    conn = Connection({"status": "card_recorded", "card_id": C,
                       "reused": False, "execution_authorized": False})
    owner = PostgresPrivateCardOwner(lambda: conn, enabled=True)
    result = asyncio.run(owner.register_card(evidence))
    assert result["card_id"] == C
    sql, args = conn.cursor_value.statements[1]
    assert "register_content_ops_button_card_from_sends" in sql
    assert "record_content_ops_button_card(" not in sql
    assert args[6] == SHA
    assert args[7] == '["' + SHA + '","' + SHA + '","' + SHA + '","' + SHA + '"]'


def test_terminal_readback_is_bounded_and_never_grants_send():
    receipt = {"status": "sent", "card_id": C, "outbox_id": O,
               "execution_authorized": False}
    conn = Connection(receipt)
    owner = PostgresPrivateCardOwner(lambda: conn, enabled=True)
    assert asyncio.run(owner.read_terminal(review_id=R, card_id=C,
        outbox_id=O)) == receipt
    sql, args = conn.cursor_value.statements[1]
    assert "read_content_ops_button_card_terminal" in sql
    assert args == (R, C, O)


def test_unknown_commit_and_bad_arguments_never_become_send_permission():
    conn = Connection({"status": "reserved", "new_attempt": True,
                       "execution_authorized": False}, fail_commit=True)
    factory = Mock(return_value=conn)
    owner = PostgresPrivateCardOwner(factory, enabled=True)
    with pytest.raises(PrivateCardOwnerError, match="private_card_owner_outcome_unknown"):
        asyncio.run(owner.reserve_part(review_id=R, card_id=C,
                                       part_index=0, payload_sha256=SHA))
    factory.reset_mock()
    with pytest.raises(PrivateCardOwnerError, match="private_card_owner_arguments_invalid"):
        asyncio.run(owner.reserve_part(review_id=R, card_id=C,
                                       part_index=True, payload_sha256=SHA))
    factory.assert_not_called()
