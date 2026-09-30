"""Fake transactions only; no live or local database connection."""
import asyncio
from unittest.mock import Mock

import pytest

from core.content_ops.final_card_owner import (
    FinalCardOwnerError, PostgresFinalCardOwner,
)


R = "44444444-4444-4444-8444-444444444444"
C = "55555555-5555-4555-8555-555555555555"
A = "66666666-6666-4666-8666-666666666666"
D = "77777777-7777-4777-8777-777777777777"
SHA = "a" * 64
RELEASE = "b" * 40
EXPIRY = "2026-09-25T13:10:00+00:00"


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
    def __init__(self, receipt, *, fail_commit=False, autocommit=False):
        self.autocommit = autocommit
        self.cursor_value = Cursor(receipt)
        self.fail_commit = fail_commit

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        if self.fail_commit:
            raise OSError("uncertain commit; private response hidden")
        return False

    def cursor(self):
        return self.cursor_value


def reserve_args():
    return dict(delivery_id=D, review_id=R, parent_card_id=C, actor_id=A,
        version_fingerprint=SHA, bot_binding=SHA, room_binding=SHA,
        human_binding=SHA, snapshot_sha256=SHA, packet_sha256=SHA,
        release_sha=RELEASE, telegram_route_binding="b" * 64,
        typefully_route_binding="c" * 64)


@pytest.mark.parametrize("method,kwargs", [
    ("reserve_delivery", reserve_args()),
    ("begin_part", dict(delivery_id=D, part_index=0, payload_sha256=SHA)),
    ("confirm_part", dict(delivery_id=D, part_index=0, payload_sha256=SHA,
                          message_binding=SHA, response_sha256=SHA)),
    ("register_card", dict(delivery_id=D)),
    ("read_registered", dict(delivery_id=D)),
])
def test_default_off_never_opens_connection(method, kwargs):
    factory = Mock(side_effect=AssertionError("unexpected DB I/O"))
    owner = PostgresFinalCardOwner(factory)
    with pytest.raises(FinalCardOwnerError, match="final_card_owner_disabled"):
        asyncio.run(getattr(owner, method)(**kwargs))
    factory.assert_not_called()


def test_exact_reservation_commit_and_bounded_receipt():
    receipt = {"status": "delivery_reserved", "delivery_id": D,
               "expires_at": EXPIRY, "execution_authorized": False}
    conn = Connection(receipt)
    owner = PostgresFinalCardOwner(lambda: conn, enabled=True)
    assert asyncio.run(owner.reserve_delivery(**reserve_args())) == receipt
    sql, args = conn.cursor_value.statements[1]
    assert "reserve_content_ops_final_card_delivery" in sql
    assert args == (D, R, C, A, SHA, SHA, SHA, SHA, SHA, SHA, RELEASE,
                    "b" * 64, "c" * 64)


def test_part_begin_and_confirmation_use_separate_transactions():
    receipts = [
        {"status": "attempt_recorded", "reused": False,
         "execution_authorized": False},
        {"status": "confirmed", "reused": False,
         "execution_authorized": False},
    ]
    connections = []

    def factory():
        conn = Connection(receipts[len(connections)])
        connections.append(conn)
        return conn

    owner = PostgresFinalCardOwner(factory, enabled=True)

    async def run():
        begun = await owner.begin_part(delivery_id=D, part_index=0,
                                       payload_sha256=SHA)
        confirmed = await owner.confirm_part(delivery_id=D, part_index=0,
            payload_sha256=SHA, message_binding=SHA, response_sha256=SHA)
        return begun, confirmed

    assert asyncio.run(run()) == tuple(receipts)
    assert len(connections) == 2
    assert "begin_content_ops_final_card_part" in connections[0].cursor_value.statements[1][0]
    assert "confirm_content_ops_final_card_part" in connections[1].cursor_value.statements[1][0]
    assert connections[0].cursor_value.statements[1][1] == (D, 0, SHA)
    assert connections[1].cursor_value.statements[1][1] == (D, 0, SHA, SHA, SHA)


@pytest.mark.parametrize("field", ["telegram_route_binding", "typefully_route_binding"])
@pytest.mark.parametrize("value", [None, "", "account-label", "a" * 63, "a" * 64 + "\n"])
def test_invalid_destination_binding_rejected_before_connection(field, value):
    factory = Mock(side_effect=AssertionError("unexpected DB I/O"))
    owner = PostgresFinalCardOwner(factory, enabled=True)
    with pytest.raises(FinalCardOwnerError, match="arguments_invalid"):
        asyncio.run(owner.reserve_delivery(**{**reserve_args(), field: value}))
    factory.assert_not_called()


def test_reused_begin_is_readback_not_fresh_attempt():
    receipt = {"status": "delivery_unknown", "reused": True,
               "execution_authorized": False}
    owner = PostgresFinalCardOwner(lambda: Connection(receipt), enabled=True)
    assert asyncio.run(owner.begin_part(delivery_id=D, part_index=0,
                                        payload_sha256=SHA)) == receipt
    bad = {**receipt, "reused": False}
    owner = PostgresFinalCardOwner(lambda: Connection(bad), enabled=True)
    with pytest.raises(FinalCardOwnerError, match="outcome_unknown"):
        asyncio.run(owner.begin_part(delivery_id=D, part_index=0,
                                     payload_sha256=SHA))


def test_registration_and_exact_readback_are_not_approval():
    receipts = [
        {"status": "card_registered", "card_id": D, "reused": False,
         "execution_authorized": False},
        {"status": "card_registered", "card_id": D,
         "execution_authorized": False},
    ]
    connections = []

    def factory():
        conn = Connection(receipts[len(connections)])
        connections.append(conn)
        return conn

    owner = PostgresFinalCardOwner(factory, enabled=True)

    async def run():
        return (await owner.register_card(delivery_id=D),
                await owner.read_registered(delivery_id=D))

    assert asyncio.run(run()) == tuple(receipts)
    assert "register_content_ops_final_card" in connections[0].cursor_value.statements[1][0]
    assert "read_content_ops_final_card_terminal" in connections[1].cursor_value.statements[1][0]
    assert all(conn.cursor_value.statements[1][1] == (D,) for conn in connections)


def test_unknown_commit_never_returns_send_permission():
    receipt = {"status": "attempt_recorded", "reused": False,
               "execution_authorized": False}
    conn = Connection(receipt, fail_commit=True)
    owner = PostgresFinalCardOwner(lambda: conn, enabled=True)
    with pytest.raises(FinalCardOwnerError, match="outcome_unknown"):
        asyncio.run(owner.begin_part(delivery_id=D, part_index=0,
                                     payload_sha256=SHA))


def test_autocommit_and_invalid_arguments_fail_before_mutation():
    receipt = {"status": "attempt_recorded", "reused": False,
               "execution_authorized": False}
    conn = Connection(receipt, autocommit=True)
    owner = PostgresFinalCardOwner(lambda: conn, enabled=True)
    with pytest.raises(FinalCardOwnerError, match="outcome_unknown"):
        asyncio.run(owner.begin_part(delivery_id=D, part_index=0,
                                     payload_sha256=SHA))
    assert conn.cursor_value.statements == []
    factory = Mock(side_effect=AssertionError("unexpected DB I/O"))
    owner = PostgresFinalCardOwner(factory, enabled=True)
    with pytest.raises(FinalCardOwnerError, match="arguments_invalid"):
        asyncio.run(owner.begin_part(delivery_id=D, part_index=True,
                                     payload_sha256=SHA))
    factory.assert_not_called()


@pytest.mark.parametrize("bad", [
    {"status": "delivery_reserved", "delivery_id": C,
     "expires_at": EXPIRY, "execution_authorized": False},
    {"status": "delivery_reserved", "delivery_id": D,
     "expires_at": EXPIRY, "execution_authorized": True},
    {"status": "delivery_reserved", "delivery_id": D,
     "expires_at": "2026-09-25T13:10:00", "execution_authorized": False},
])
def test_bounded_reservation_cannot_change_identity_or_authority(bad):
    owner = PostgresFinalCardOwner(lambda: Connection(bad), enabled=True)
    with pytest.raises(FinalCardOwnerError, match="outcome_unknown"):
        asyncio.run(owner.reserve_delivery(**reserve_args()))
