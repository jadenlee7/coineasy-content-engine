"""Fake transaction only; no connection to production or local Postgres."""
import asyncio
from unittest.mock import Mock

import pytest

from core.content_ops.private_review_card_owner import (
    PostgresPrivateCardOwner, PrivateCardOwnerError,
)


R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"
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
    ("reserve_part", dict(review_id=R, card_id=C, part_index=0,
                          payload_sha256=SHA)),
    ("confirm_part", dict(review_id=R, card_id=C, part_index=0,
                          payload_sha256=SHA, message_binding=SHA,
                          response_sha256=SHA, observed_at=AT)),
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
            part_index=0, payload_sha256=SHA, message_binding=SHA,
            response_sha256=SHA, observed_at=AT)
        return reserved, confirmed

    assert asyncio.run(run()) == tuple(receipts)
    assert len(connections) == 2
    assert "reserve_content_ops_button_card_send" in connections[0].cursor_value.statements[1][0]
    assert "confirm_content_ops_button_card_send" in connections[1].cursor_value.statements[1][0]
    assert connections[0].cursor_value.statements[1][1] == (R, C, 0, SHA)


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
