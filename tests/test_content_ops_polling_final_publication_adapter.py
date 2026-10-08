"""Offline existing-poller injection tests. No network or real DB connection."""
import asyncio
import copy
import hashlib
import threading
from dataclasses import replace

import pytest

from core.content_ops.final_publication_confirmation import FinalConfirmationSigner
from core.content_ops.final_publication_owner import PostgresFinalPublicationOwner
from core.content_ops.polling_final_publication_adapter import PollingFinalPublicationAdapter
from core.content_ops.review_buttons import ButtonSigner
from core.content_ops.review_edit_ingress import EditBindings
from core.content_ops.review_ingress import IngressPolicy, ReviewIngressError
from tests.test_content_ops_final_decision_owner import FakeConnection
from tests.test_content_ops_final_publication_confirmation import A, C, NOW, snapshot

PARENT = "77777777-7777-4777-8777-777777777777"
DECISION = "88888888-8888-4888-8888-888888888888"


def fixture(*, action="p", lost_commit=False):
    bindings=EditBindings(b"b"*32)
    policy=IngressPolicy("webhook-secret-32-characters-exact",101,-100999000,
        bindings.digest("room",101,-100999000),((201,A),))
    signer=FinalConfirmationSigner(b"f"*32)
    s=replace(snapshot(),decision_mode="telegram_and_typefully_draft",parent_card_id=PARENT)
    connections=[]; reads=[]
    receipt={"status":"held" if action=="h" else "channels_queued",
        "decision_id":DECISION,"approval_id":None if action=="h" else DECISION,
        "publication_id":None if action=="h" else DECISION,"job_id":None if action=="h" else DECISION,
        "typefully_slot_id":None if action=="h" else DECISION,
        "reused":False,"execution_authorized":False}
    class Connection(FakeConnection):
        def __exit__(self,*args):
            if lost_commit: raise RuntimeError("secret database response")
            return super().__exit__(*args)
    def connect():
        c=Connection(receipt);connections.append(c);return c
    def read(*args):
        reads.append(args)
        assert args == (policy.room_binding,
            bindings.digest("final-card-message@1",101,-100999000,501),
            bindings.digest("bot",101),bindings.digest("human",101,201))
        return s
    owner=PostgresFinalPublicationOwner(connect,read,release_sha=s.release_sha,enabled=True)
    adapter=PollingFinalPublicationAdapter(enabled=True,policy=policy,signer=signer,
        review_signer=ButtonSigner(b"s"*32),bindings=bindings,owner=owner,runtime_release_sha=s.release_sha)
    update={"update_id":1,"callback_query":{"id":"callback1","from":{"id":201,"is_bot":False},
        "message":{"message_id":501,"date":NOW-1,"chat":{"id":policy.chat_id,"type":"supergroup"},
            "from":{"id":policy.bot_id,"is_bot":True}},
        "data":signer.issue(s,action,policy.room_binding,now=NOW,expires_at=NOW+900)}}
    return adapter,update,connections,reads


@pytest.mark.parametrize("enabled",[False,None,"true",1])
def test_off_does_not_access_payload_keys_or_any_dependency(enabled):
    adapter=PollingFinalPublicationAdapter(enabled=enabled,policy=object(),owner=object())
    assert asyncio.run(adapter.handle_callback(object(),now=None))=={
        "status":"disabled","execution_authorized":False,"public_send_attempted":False}


@pytest.mark.parametrize("action,status",[("p","channels_queued"),("h","held")])
def test_actual_signed_confirmation_uses_final_not_parent_card_and_exact_callback_key(action,status):
    adapter,update,connections,reads=fixture(action=action)
    original=copy.deepcopy(update)
    assert asyncio.run(adapter.handle_callback(update,now=NOW))=={
        "status":status,"execution_authorized":False,"public_send_attempted":False}
    assert update==original and len(reads)==len(connections)==1
    query,args=connections[0].cursor_value.queries[1]
    assert "private.record_content_ops_final_publication" in query
    assert args[0]==C and args[0]!=PARENT
    assert args[-1]==hashlib.sha256(b"callback1").hexdigest()
    assert connections[0].committed


@pytest.mark.parametrize("mutation",["ce1","prefix","actor","actor_bot","room","publicroom","bot",
    "anonymous","forward","topic","inline","date","oversize","extra_update"])
def test_foreign_or_unsupported_updates_fail_before_owner_io(mutation):
    adapter,u,connections,reads=fixture();q=u["callback_query"];m=q["message"]
    if mutation=="ce1": q["data"]="ce1:"+q["data"][4:]
    elif mutation=="prefix": q["data"]=q["data"][4:]
    elif mutation=="actor": q["from"]["id"]=999
    elif mutation=="actor_bot": q["from"]["is_bot"]=True
    elif mutation=="room": m["chat"]["id"]=-100999001
    elif mutation=="publicroom": m["chat"]["username"]="public_room"
    elif mutation=="bot": m["from"]["id"]=102
    elif mutation=="anonymous": m["sender_chat"]={"id":-100999000}
    elif mutation=="forward": m["forward_origin"]={}
    elif mutation=="topic": m["message_thread_id"]=11
    elif mutation=="inline": q["inline_message_id"]="x"
    elif mutation=="date": m["date"]=NOW+1
    elif mutation=="oversize": m["text"]="x"*33000
    elif mutation=="extra_update": u["message"]={}
    with pytest.raises(ReviewIngressError): asyncio.run(adapter.handle_callback(u,now=NOW))
    assert reads==connections==[]


def test_lost_commit_is_unknown_and_redelivery_never_retries_same_instance():
    adapter,u,connections,reads=fixture(lost_commit=True)
    first=asyncio.run(adapter.handle_callback(u,now=NOW))
    second=asyncio.run(adapter.handle_callback(u,now=NOW))
    assert first==second=={"status":"outcome_unknown","execution_authorized":False,"public_send_attempted":False}
    assert len(connections)==len(reads)==1
    assert "secret" not in str(first)


def test_bad_signature_has_no_mutation_and_cannot_auto_retry():
    adapter,u,connections,reads=fixture();u["callback_query"]["data"]="ce2:"+"A"*51
    assert asyncio.run(adapter.handle_callback(u,now=NOW))["status"]=="outcome_unknown"
    assert asyncio.run(adapter.handle_callback(u,now=NOW))["status"]=="outcome_unknown"
    assert connections==[] and len(reads)==1


@pytest.mark.parametrize("key_source",["review","bindings"])
def test_shared_signing_key_is_rejected_before_io(key_source):
    adapter,_,connections,reads=fixture()
    with pytest.raises(ReviewIngressError,match="configuration_invalid"):
        PollingFinalPublicationAdapter(enabled=True,policy=adapter.policy,
            signer=FinalConfirmationSigner((b"s" if key_source=="review" else b"b")*32),
            review_signer=ButtonSigner(b"s"*32),bindings=adapter.bindings,
            owner=adapter.owner,runtime_release_sha=adapter.runtime_release_sha)
    assert reads==connections==[]


@pytest.mark.asyncio
async def test_concurrent_same_callback_has_only_one_owner_transaction(monkeypatch):
    import core.content_ops.polling_final_publication_adapter as module
    adapter,u,connections,reads=fixture()
    original=module.handle_final_confirmation
    started=threading.Event();release=threading.Event();calls=[]
    def slow(*args,**kwargs):
        calls.append(1);started.set()
        assert release.wait(2)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,"handle_final_confirmation",slow)
    first=asyncio.create_task(adapter.handle_callback(u,now=NOW))
    try:
        assert await asyncio.to_thread(started.wait,1)
        assert (await adapter.handle_callback(u,now=NOW))["status"]=="outcome_unknown"
    finally:
        release.set()
    assert (await first)["status"]=="channels_queued"
    assert len(calls)==len(connections)==len(reads)==1


@pytest.mark.asyncio
async def test_cancellation_quarantines_callback_while_owner_thread_may_commit(monkeypatch):
    import core.content_ops.polling_final_publication_adapter as module
    adapter,u,connections,reads=fixture()
    original=module.handle_final_confirmation
    started=threading.Event();release=threading.Event();finished=threading.Event()
    def slow(*args,**kwargs):
        started.set()
        try:
            assert release.wait(2)
            return original(*args,**kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(module,"handle_final_confirmation",slow)
    task=asyncio.create_task(adapter.handle_callback(u,now=NOW))
    try:
        assert await asyncio.to_thread(started.wait,1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert (await adapter.handle_callback(u,now=NOW))["status"]=="outcome_unknown"
    finally:
        release.set()
    assert await asyncio.to_thread(finished.wait,1)
    assert len(reads)==len(connections)==1 and connections[0].committed
    assert (await adapter.handle_callback(u,now=NOW))["status"]=="outcome_unknown"
