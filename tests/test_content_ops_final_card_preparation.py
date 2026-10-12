"""Mock owner/transport boundaries, not deployed or real delivery evidence."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from core.content_ops.final_card_courier import FinalCardCourier
from core.content_ops.final_card_preparation import (
    FinalPreparationHook, FinalPreparationWorker, PostgresFinalPreparationOwner, FinalPreparationError,
    PostgresPreparedFinalCardOwner,
)
from core.content_ops.final_publication_confirmation import FinalConfirmationSigner
from core.content_ops.publication_route_verification import VerifiedPublicationRoutes
from core.content_ops.review_edit_ingress import EditBindings
import test_content_ops_final_card_courier as fixtures

E, B, P = fixtures.RELEASE, fixtures.CALLBACK_RELEASE, fixtures.PAIR


def projection(bindings):
    s = fixtures.prepared().snapshot
    return dict(status="preparation_claimed", execution_authorized=False, intent_id=fixtures.D,
        workspace_id=fixtures.W, review_id=fixtures.R, parent_card_id=fixtures.C,
        review_epoch=0, reviewer_id=fixtures.A, version_fingerprint=s.version_fingerprint,
        bot_binding=bindings.digest("bot", fixtures.BOT),
        room_binding=bindings.digest("room", fixtures.BOT, fixtures.ROOM),
        human_binding=bindings.digest("human", fixtures.BOT, fixtures.HUMAN),
        release_sha=E, callback_release_sha=B, release_pair_sha256=P,
        telegram_route_binding=s.telegram_route_binding, typefully_route_binding=s.typefully_route_binding,
        candidate=dict(client_id="yellow", kst_date="2026-09-25", content_item_id=fixtures.I,
            content_version_id=fixtures.V, source_item_id=fixtures.I, generate_job_id=fixtures.V,
            banner_asset_id=fixtures.I, banner_sha256=s.review.banner_sha256, title="검수",
            telegram_copy=s.review.telegram_copy, x_copy=s.review.x_copy,
            source_url=s.review.source_url, source_published_at=s.review.source_published_at))


class ClaimOwner:
    pair = (E, B, P)
    def __init__(self, data): self.data, self.calls, self.claimed = data, 0, False
    async def claim(self, intent_id):
        self.calls += 1
        if self.claimed:
            return dict(status="outcome_unknown", execution_authorized=False)
        self.claimed = True
        return deepcopy(self.data)


@pytest.fixture
def setup():
    bindings = EditBindings(b"e"*32)
    class MockPreparedLedger(fixtures.FakeOwner, PostgresPreparedFinalCardOwner):
        pass
    sender, ledger = fixtures.FakeSender(), MockPreparedLedger()
    courier = FinalCardCourier(ledger, sender, FinalConfirmationSigner(b"f"*32), bindings,
                              runtime_release_sha=E, clock=lambda: fixtures.NOW)
    data = projection(bindings)
    owner = ClaimOwner(data)
    reads = []
    async def png(*args):
        assert owner.claimed
        reads.append(("png", args))
        return fixtures.PNG
    async def routes(*args):
        assert owner.claimed
        reads.append(("routes", args))
        return VerifiedPublicationRoutes(fixtures.W, "yellow", E, "d"*64, "e"*64,
            "@yellowkorea_ann", "@Yellow__Korea", datetime.fromtimestamp(fixtures.NOW,timezone.utc))
    worker = FinalPreparationWorker(owner, courier, bindings=bindings, bot_id=fixtures.BOT,
        chat_id=fixtures.ROOM, reviewers={fixtures.A:fixtures.HUMAN}, png_reader=png, route_reader=routes,
        runtime_engine_release_sha=E, callback_release_sha=B, release_pair_sha256=P,
        clock=lambda: fixtures.NOW)
    return worker, owner, sender, ledger, reads


def run(worker, **kwargs):
    return asyncio.run(worker.run(fixtures.D, **kwargs))


def test_default_off_has_no_db_storage_route_or_send(setup):
    worker, owner, sender, ledger, reads = setup
    assert run(worker)==dict(status="disabled",public_send_attempted=False)
    assert (owner.calls, ledger.reserve_calls, sender.calls, reads)==(0,0,[],[])


def test_one_claim_reuses_existing_four_part_courier_and_never_queues_public(setup):
    worker, owner, sender, ledger, reads = setup
    assert run(worker, enabled=True)==dict(status="card_registered",confirmed_parts=4,public_send_attempted=False)
    assert len(sender.calls)==4 and ledger.registered and len(reads)==2
    assert ledger.reservation["decision_mode"]=="telegram_and_typefully_draft"
    assert tuple(ledger.reservation[k] for k in ("release_sha","callback_release_sha","release_pair_sha256"))==(E,B,P)
    assert run(worker,enabled=True)["status"]=="outcome_unknown"
    assert len(sender.calls)==4 and len(reads)==2


@pytest.mark.parametrize("mutation", ["actor","room","pair","parent","copy","banner","route"])
def test_incorrect_or_changed_projection_never_sends(setup, mutation):
    worker, owner, sender, ledger, reads = setup
    if mutation=="actor": owner.data["reviewer_id"]=fixtures.I
    elif mutation=="room": owner.data["room_binding"]="0"*64
    elif mutation=="pair": owner.data["release_pair_sha256"]="0"*64
    elif mutation=="parent": owner.data["parent_card_id"]=fixtures.D
    elif mutation=="copy": owner.data["candidate"]["telegram_copy"]="a"*1025
    elif mutation=="banner": owner.data["candidate"]["banner_sha256"]="0"*64
    else: owner.data["telegram_route_binding"]="0"*64
    assert run(worker,enabled=True)["status"] in {"blocked","outcome_unknown"}
    assert sender.calls==[] and ledger.reserve_calls==0
    assert run(worker,enabled=True)["status"]=="outcome_unknown"


def test_unknown_private_delivery_is_not_retried_after_worker_restart(setup):
    worker, owner, sender, ledger, reads = setup
    sender.fail_at=1
    assert run(worker,enabled=True)["status"]=="delivery_unknown"
    assert len(sender.calls)==2
    # Durable owner, not the worker's in-memory state, quarantines the attempt.
    worker._courier._attempted.clear()
    assert run(worker,enabled=True)["status"]=="outcome_unknown"
    assert len(sender.calls)==2 and not ledger.registered


def test_engine_worker_rejects_bot_release_as_engine(setup):
    worker, owner, sender, ledger, reads = setup
    with pytest.raises(FinalPreparationError):
        FinalPreparationWorker(owner, worker._courier,bindings=worker._bindings,
            bot_id=fixtures.BOT,chat_id=fixtures.ROOM,reviewers={fixtures.A:fixtures.HUMAN},
            png_reader=worker._png,route_reader=worker._routes,
            runtime_engine_release_sha=B,callback_release_sha=B,release_pair_sha256=P)
    assert owner.calls==0 and reads==[]


def test_hook_default_off_and_runtime_callback_fence():
    hook=FinalPreparationHook(release_sha=E,callback_release_sha=B,release_pair_sha256=P,
                             runtime_callback_release_sha=B)
    cursor=Mock(side_effect=AssertionError("no DB"))
    assert hook.in_transaction(cursor,review_id=None,parent_card_id=None,actor_id=None,
        version_fingerprint=None,bot_binding=None,room_binding=None,human_binding=None)["status"]=="disabled"
    cursor.execute.assert_not_called()
    with pytest.raises(FinalPreparationError):
        FinalPreparationHook(release_sha=E,callback_release_sha=B,release_pair_sha256=P,
                             runtime_callback_release_sha=E,enabled=True)


@pytest.mark.parametrize("reused",[False,True])
def test_hook_passes_only_verified_metadata_and_returns_original_intent(reused):
    hook=FinalPreparationHook(release_sha=E,callback_release_sha=B,release_pair_sha256=P,
                             runtime_callback_release_sha=B,enabled=True)
    cursor=Mock()
    receipt=dict(status="preparation_recorded",intent_id=fixtures.D,reused=reused,execution_authorized=False)
    cursor.fetchone.return_value=(receipt,)
    assert hook.in_transaction(cursor,review_id=fixtures.R,parent_card_id=fixtures.C,actor_id=fixtures.A,
        version_fingerprint="b"*64,bot_binding="1"*64,room_binding="2"*64,human_binding="3"*64)==receipt
    args=cursor.execute.call_args.args[1]
    assert args[1:4]==(fixtures.R,fixtures.C,fixtures.A) and args[-3:]==(E,B,P)


class Connection:
    autocommit=False
    def __init__(self,result,fail_commit=False):
        self.result,self.fail_commit,self.calls,self.committed=result,fail_commit,[],False
    def __enter__(self): return self
    def __exit__(self,*args):
        if self.fail_commit: raise OSError("private database response")
        self.committed=True
    def cursor(self): return self
    def execute(self,query,args=()): self.calls.append((query,args))
    def fetchone(self): return ("5000","2000") if "set_config" in self.calls[-1][0] else (self.result,)


def test_postgres_owner_off_and_commit_ack_gate(setup):
    _, fixture, _, _, _=setup
    factory=Mock()
    owner=PostgresFinalPreparationOwner(factory,release_sha=E,callback_release_sha=B,release_pair_sha256=P)
    assert asyncio.run(owner.claim(fixtures.D))["status"]=="disabled"
    factory.assert_not_called()
    conn=Connection(fixture.data)
    owner=PostgresFinalPreparationOwner(lambda:conn,release_sha=E,callback_release_sha=B,
                                      release_pair_sha256=P,enabled=True)
    assert asyncio.run(owner.claim(fixtures.D))==fixture.data and conn.committed
    assert conn.calls[-1][1]==(fixtures.D,E,B,P)
    conn.fail_commit=True
    with pytest.raises(FinalPreparationError,match="^final_card_preparation_outcome_unknown$"):
        asyncio.run(owner.claim(fixtures.D))


def test_prepared_transport_owner_replaces_admission_not_receipt_rpcs(monkeypatch):
    from core.content_ops.final_card_owner import PostgresFinalCardOwner
    calls=[]
    monkeypatch.setattr(PostgresFinalCardOwner,"_call",lambda self,q,a,expected_keys: calls.append((q,a,expected_keys)))
    owner=PostgresPreparedFinalCardOwner(lambda:None)
    owner._call("select private.reserve_content_ops_final_card_delivery(%s)",("fixture",),expected_keys={"status"})
    owner._call("select private.begin_content_ops_final_card_part(%s)",("fixture",),expected_keys={"status"})
    owner._call("select private.confirm_content_ops_final_card_part(%s)",("fixture",),expected_keys={"status"})
    owner._call("select private.read_content_ops_final_card_terminal(%s)",("fixture",),expected_keys={"status"})
    assert calls[0][0]=="select private.reserve_prepared_content_ops_final_card_delivery(%s)"
    assert calls[1][0]=="select private.begin_prepared_content_ops_final_card_part(%s)"
    assert calls[2][0]=="select private.confirm_content_ops_final_card_part(%s)"
    assert calls[3][0]=="select private.read_content_ops_final_card_terminal(%s)"
    assert all(call[1:]==calls[0][1:] for call in calls)
