from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import httpx
import pytest

from core.content_ops.publication_route_verification import _digest
from core.publications.models import ClaimedTelegramPublication, StoredPng, TelegramReceipt
from core.publications.repository import PublicationRepositoryError, SupabasePublicationRepository
from core.publications.settings import PUBLICATION_CLIENTS, PUBLICATION_TELEGRAM_USERNAMES, parse_publication_allowed_clients
from core.publications.telegram_route import telegram_route_binding
from core.publications.worker import ExactTelegramPublicationWorker
from core.publishers.telegram_exact import ExactTelegramPublisher, TelegramExactConfig, TelegramExactError
from scripts.run_telegram_publications import _validate_only

W = "11111111-1111-4111-8111-111111111111"
D = "22222222-2222-4222-8222-222222222222"
SHA = "f" * 40
BOT = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghijk12345"
CHANNEL = -1001234567890
PNG = b"\x89PNG\r\n\x1a\nsynthetic"


def binding(client="squid"):
    return telegram_route_binding(workspace_id=W, client_id=client,
        release_sha=SHA, channel_id=CHANNEL, bot_id=123456789,
        username=PUBLICATION_TELEGRAM_USERNAMES[client])


def claim(client="squid"):
    return ClaimedTelegramPublication(D, D, D, D, D, client, 1, 3,
        "worker:test-1234", datetime.now(timezone.utc), "Exact Korean",
        StoredPng(D, "content-studio", f"{W}/{client}/{D}/news-card.png",
            "image/png", len(PNG), hashlib.sha256(PNG).hexdigest(), 1080, 1080),
        "telegram_principal", D, binding(client), SHA)


@pytest.mark.parametrize("raw", ["", "all", "unknown", "squid,yellow", "yellow,yellow",
    "yellow,", " yellow", "yellow, squid", "yellow,null", "S quid"])
def test_allowlist_rejects_noncanonical_or_implicit_expansion(raw):
    with pytest.raises(ValueError):
        parse_publication_allowed_clients(raw)


@pytest.mark.parametrize("client", PUBLICATION_CLIENTS)
def test_single_client_subset_and_final_route_digest_parity(client):
    assert parse_publication_allowed_clients(client) == (client,)
    assert binding(client) == _digest({"schema": "content-ops-publication-route@1",
        "workspace_id": W, "client_id": client, "release_sha": SHA,
        "provider": "telegram", "action": "official_channel_post",
        "channel_id": CHANNEL, "bot_id": 123456789,
        "username": PUBLICATION_TELEGRAM_USERNAMES[client]})


@pytest.mark.asyncio
@pytest.mark.parametrize("clients", [("squid",), ("yellow",), PUBLICATION_CLIENTS])
async def test_repository_selects_subset_before_claim_not_after(clients):
    observed = []
    def handle(req):
        observed.append(req)
        return httpx.Response(200, content=b"null", headers={"content-type":"application/json"})
    repo = SupabasePublicationRepository(supabase_url="https://ci.supabase.co",
        service_role_key="s" * 40, workspace_id=W, allowed_clients=clients,
        transport=httpx.MockTransport(handle))
    assert await repo.claim(worker_id="worker:test-1234", lease_seconds=180) is None
    path = observed[0].url.path
    payload = json.loads(observed[0].content)
    if clients == ("squid",):
        assert path.endswith("/claim_exact_telegram_publication_job")
        assert "target_allowed_clients" not in payload
    else:
        assert path.endswith("/claim_exact_telegram_publication_job_v2")
        assert payload["target_allowed_clients"] == list(clients)
    assert len(observed) == 1


class Repository:
    def __init__(self, value):
        self.value = value
        self.calls = []
    async def claim(self, **kwargs): return self.value
    async def download_asset(self, value): self.calls.append("download"); return PNG
    async def mark_attempt(self, *args): self.calls.append("mark")
    async def complete(self, *args, **kwargs): self.calls.append("complete")
    async def fail(self, *args, **kwargs): self.calls.append("fail"); return "failed"


class Publisher:
    def __init__(self, client):
        self.config = TelegramExactConfig(client, PUBLICATION_TELEGRAM_USERNAMES[client],
            "@" + PUBLICATION_TELEGRAM_USERNAMES[client], BOT)
        self.calls = []
    async def verify_content_ops_route(self, **kwargs):
        assert kwargs == {"workspace_id": W, "release_sha": SHA}
        self.calls.append("verify")
        return binding(self.config.client_id)
    async def preflight(self): raise AssertionError("principal may not use legacy preflight")
    async def send_photo_once(self, **kwargs):
        self.calls.append("send")
        assert kwargs == {"image_bytes": PNG, "caption": "Exact Korean"}
        return TelegramReceipt(42, self.config.public_username, datetime.now(timezone.utc))


@pytest.mark.asyncio
@pytest.mark.parametrize("client", PUBLICATION_CLIENTS)
async def test_principal_exact_route_fences_each_client_before_attempt(client):
    r = Repository(claim(client)); p = Publisher(client)
    worker = ExactTelegramPublicationWorker(repository=r, publisher_factory=lambda _: p,
        allowed_clients=(client,), workspace_id=W, release_sha=SHA)
    result = await worker.run_once()
    assert result.status == "published"
    assert p.calls == ["verify", "send"]
    assert r.calls == ["download", "mark", "complete"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"content_ops_decision_id": None}, {"content_ops_decision_id": "bad"},
    {"telegram_route_binding": "a" * 64}, {"content_ops_release_sha": "a" * 40},
    {"approval_reviewer_source": "studio_session"},
])
async def test_missing_or_changed_principal_pin_never_marks_or_sends(change):
    r = Repository(replace(claim(), **change)); p = Publisher("squid")
    result = await ExactTelegramPublicationWorker(repository=r, publisher_factory=lambda _: p,
        workspace_id=W, release_sha=SHA).run_once()
    assert result.error == "telegram_publication_route_mismatch"
    assert r.calls == ["fail"]
    assert "send" not in p.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("client", PUBLICATION_CLIENTS)
async def test_provider_route_pins_numeric_channel_and_exact_receipt(client):
    username = PUBLICATION_TELEGRAM_USERNAMES[client]; calls = []
    def handle(req):
        method = req.url.path.rsplit("/", 1)[-1]; calls.append(method)
        if method == "getMe": result = {"id": 123456789, "is_bot": True}
        elif method == "getChat": result = {"id": CHANNEL, "type": "channel", "username": username}
        elif method == "getChatMember":
            assert json.loads(req.content) == {"chat_id": CHANNEL, "user_id": 123456789}
            result = {"status": "administrator", "can_post_messages": True,
                "user": {"id": 123456789, "is_bot": True}}
        else:
            assert method == "sendPhoto"
            assert str(CHANNEL).encode() in req.content
            assert ("@" + username).encode() not in req.content
            result = {"message_id": 42, "date": 1_800_000_000,
                "chat": {"id": CHANNEL, "type": "channel", "username": username},
                "caption": "Exact Korean"}
        return httpx.Response(200, json={"ok": True, "result": result})
    p = ExactTelegramPublisher(TelegramExactConfig(client, username, "@" + username, BOT),
        transport=httpx.MockTransport(handle))
    assert await p.verify_content_ops_route(workspace_id=W, release_sha=SHA) == binding(client)
    assert (await p.send_photo_once(image_bytes=PNG, caption="Exact Korean")).message_id == 42
    assert calls == ["getMe", "getChat", "getChatMember", "sendPhoto"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["bot", "channel", "rights", "receipt_channel", "receipt_copy"])
async def test_provider_route_or_receipt_drift_fails_closed_without_retry(bad):
    calls=[]
    def handle(req):
        method=req.url.path.rsplit("/",1)[-1]; calls.append(method)
        if method=="getMe": result={"id": 999999999 if bad=="bot" else 123456789, "is_bot": True}
        elif method=="getChat": result={"id": CHANNEL, "type": "channel", "username": "otherchannel" if bad=="channel" else "squid_kor_update"}
        elif method=="getChatMember": result={"status":"administrator","can_post_messages":bad!="rights","user":{"id":123456789,"is_bot":True}}
        else: result={"message_id":42,"date":1_800_000_000,"chat":{"id": CHANNEL-1 if bad=="receipt_channel" else CHANNEL,"type":"channel","username":"squid_kor_update"},"caption":"wrong" if bad=="receipt_copy" else "Exact Korean"}
        return httpx.Response(200,json={"ok":True,"result":result})
    p=ExactTelegramPublisher(TelegramExactConfig("squid","squid_kor_update","@squid_kor_update",BOT),transport=httpx.MockTransport(handle))
    if bad.startswith("receipt"):
        await p.verify_content_ops_route(workspace_id=W,release_sha=SHA)
        with pytest.raises(TelegramExactError,match="delivery_unknown"):
            await p.send_photo_once(image_bytes=PNG,caption="Exact Korean")
        assert calls.count("sendPhoto")==1
    else:
        with pytest.raises(TelegramExactError): await p.verify_content_ops_route(workspace_id=W,release_sha=SHA)
        assert "sendPhoto" not in calls


def test_validate_only_selects_all_four_without_constructing_clients(tmp_path, monkeypatch):
    env={"TELEGRAM_PUBLICATION_ENABLED":"false","TELEGRAM_PUBLICATION_ALLOWED_CLIENTS":",".join(PUBLICATION_CLIENTS),
        "TELEGRAM_PUBLICATION_LEASE_SECONDS":"180","SUPABASE_URL":"https://ci.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY":"s"*40,"CONTENT_STUDIO_WORKSPACE_ID":W,
        "RAILWAY_GIT_COMMIT_SHA":SHA,"TELEGRAM_PUBLICATION_RELEASE_SHA":SHA}
    for client in PUBLICATION_CLIENTS:
        d=tmp_path/client; d.mkdir()
        username=PUBLICATION_TELEGRAM_USERNAMES[client]
        (d/"config.yaml").write_text(f"publishing:\n  telegram:\n    active: true\n    public_channel: '@{username}'\n    bot_env: TELEGRAM_BOT_TOKEN_{client.upper()}\n    channel_env: TELEGRAM_CHANNEL_{client.upper()}\n")
        env[f"TELEGRAM_BOT_TOKEN_{client.upper()}"]=BOT
        env[f"TELEGRAM_CHANNEL_{client.upper()}"]="@"+username
    def forbidden(*args,**kwargs): raise AssertionError("I/O in validate-only")
    monkeypatch.setattr(httpx,"AsyncClient",forbidden)
    result=_validate_only(env,clients_dir=tmp_path)
    assert result["allowed_clients"]==list(PUBLICATION_CLIENTS)
    assert result["provider_calls"] is result["database_calls"] is False
    assert BOT not in json.dumps(result) and W not in json.dumps(result)
