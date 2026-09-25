"""Offline provider-journal tests. No live API, Telegram or production DB."""
import asyncio
import base64
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from core.content_ops.banner_regeneration import (
    BannerError, BannerJournal, BannerRequest, MODEL, OpenAIImageEditor,
    STYLES, inspect_png, prompt_for, run_banner_once,
)
from core.content_ops.review_buttons import (
    ButtonReviewError, ButtonSigner, VerifiedCallback, handle_review_callback,
    review_messages,
)
from test_content_ops_review_buttons import snapshot, FakeOwner, NOW, ROOM
from core.content_ops.squid_bundle import build_client_bundle, build_squid_bundle
from core.content_ops.worker import ReviewClaim, ReviewError, APP_ORIGIN
from test_content_ops_squid_bundle import fixture
from test_content_ops_worker import NOW as BUNDLE_NOW


def png(size=(1536, 1024)):
    out = io.BytesIO()
    Image.new("RGB", size, "purple").save(out, format="PNG")
    return out.getvalue()


LOGO = png((64, 64))
RESULT = png()


def request(client="yellow", **changes):
    s = replace(snapshot(client), source_published_at="2026-09-13T18:00:00Z")
    return replace(BannerRequest(str(uuid4()), hashlib.sha256(uuid4().bytes).hexdigest(), s,
        "공식 업데이트", "새로운 소식을 확인하세요", "더 밝게, 제목을 크게",
        hashlib.sha256(LOGO).hexdigest()), **changes)


# An aware clock corresponding to the synthetic source above.
TIME = 1789326000


class Owner:
    def __init__(self):
        self.current = True
        self.saves = 0
        self.fail = False
    def is_current_edit(self, _request):
        return self.current
    def save_banner_revision(self, req, data):
        self.saves += 1
        if self.fail:
            raise RuntimeError("simulated lost commit ACK")
        assert self.current
        return {"content_version_id": str(uuid4()), "banner_sha256": hashlib.sha256(data).hexdigest(),
                "rereview_required": True, "execution_authorized": False}


class Provider:
    def __init__(self, error=None):
        self.calls = 0
        self.error = error
    async def edit(self, req, logo):
        self.calls += 1
        if self.error:
            raise self.error
        return RESULT


def run(journal, owner, provider, **kw):
    kw.setdefault("now", TIME)
    return asyncio.run(run_banner_once(enabled=True, journal=journal, owner=owner, provider=provider, **kw))


@pytest.fixture
def journal(tmp_path):
    return BannerJournal(tmp_path / "jobs.sqlite3")


@pytest.mark.parametrize("client", list(STYLES))
def test_four_clients_generate_then_commit_after_restart(journal, client):
    req, owner, provider = request(client), Owner(), Provider()
    journal.enqueue(req, LOGO, now=TIME)
    assert run(journal, owner, provider)["status"] == "result_ready"
    assert owner.saves == 0
    restarted = BannerJournal(journal.path)
    result = run(restarted, owner, provider)
    assert result == {"status": "revision_saved", "provider_called": False, "public_send_attempted": False}
    assert provider.calls == owner.saves == 1
    assert run(restarted, owner, provider)["status"] == "idle"
    assert restarted.status(req.job_id)["state"] == "complete"
    assert STYLES[client] in prompt_for(req)


@pytest.mark.parametrize("enabled", [False, None, "true", 1])
def test_default_off_never_touches_dependencies(enabled):
    result = asyncio.run(run_banner_once(enabled=enabled))
    assert result == {"status": "disabled", "provider_called": False, "public_send_attempted": False}


def test_same_click_reuses_job_and_changed_body_conflicts(journal):
    req = request()
    assert journal.enqueue(req, LOGO, now=TIME)["reused"] is False
    assert journal.enqueue(req, LOGO, now=TIME)["reused"] is True
    with pytest.raises(BannerError, match="replay_conflict"):
        journal.enqueue(replace(req, instruction="다른 요청"), LOGO, now=TIME)
    with pytest.raises(BannerError, match="pending_or_unknown"):
        journal.enqueue(request(), LOGO, now=TIME)


def test_eight_claims_one_provider_winner(journal):
    journal.enqueue(request(), LOGO, now=TIME)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: journal.take(), range(8)))
    assert sum(row is not None for row in results) == 1
    assert BannerJournal(journal.path).take() is None


def test_eight_enqueues_same_click_one_job(journal):
    req = request()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: journal.enqueue(req, LOGO, now=TIME), range(8)))
    assert sum(not r["reused"] for r in results) == 1


def test_provider_unknown_never_retries_even_after_restart(journal):
    req, provider = request(), Provider(httpx.ReadTimeout("private provider detail"))
    journal.enqueue(req, LOGO, now=TIME)
    assert run(journal, Owner(), provider)["status"] == "provider_unknown"
    assert run(BannerJournal(journal.path), Owner(), provider)["status"] == "idle"
    assert provider.calls == 1
    with pytest.raises(BannerError, match="pending_or_unknown"):
        journal.enqueue(request(), LOGO, now=TIME)


def test_lost_commit_never_regenerates_or_recommits(journal):
    req, owner, provider = request(), Owner(), Provider()
    journal.enqueue(req, LOGO, now=TIME)
    run(journal, owner, provider)
    owner.fail = True
    assert run(journal, owner, provider)["status"] == "commit_unknown"
    assert run(BannerJournal(journal.path), owner, provider)["status"] == "idle"
    assert (provider.calls, owner.saves) == (1, 1)


@pytest.mark.parametrize("after_generation", [False, True])
def test_stale_version_stops_provider_or_commit(journal, after_generation):
    req, owner, provider = request(), Owner(), Provider()
    journal.enqueue(req, LOGO, now=TIME)
    if after_generation:
        run(journal, owner, provider)
    owner.current = False
    assert run(journal, owner, provider)["status"] == "obsolete"
    assert provider.calls == int(after_generation)
    assert owner.saves == 0


@pytest.mark.parametrize("after_generation", [False, True])
def test_source_expiring_in_queue_stops_provider_or_commit(journal, after_generation):
    req, owner, provider = request(), Owner(), Provider()
    journal.enqueue(req, LOGO, now=TIME)
    if after_generation:
        run(journal, owner, provider)
    assert run(journal, owner, provider, now=TIME+86400)["status"] == "obsolete"
    assert provider.calls == int(after_generation)
    assert owner.saves == 0


def test_transparent_output_rejected_but_transparent_logo_allowed():
    out = io.BytesIO()
    Image.new("RGBA", (1536, 1024), (0, 0, 0, 0)).save(out, format="PNG")
    assert inspect_png(out.getvalue()) == (1536, 1024)
    with pytest.raises(BannerError, match="banner_image_invalid"):
        inspect_png(out.getvalue(), opaque=True)


def test_budget_counts_rejected_calls(journal):
    owner, provider = Owner(), Provider(BannerError("banner_provider_rejected"))
    for _ in range(2):
        journal.enqueue(request(), LOGO, now=TIME)
        assert run(journal, owner, provider)["status"] == "rejected"
    with pytest.raises(BannerError, match="budget_exhausted"):
        journal.enqueue(request(), LOGO, now=TIME)


def test_daily_limit_across_items(tmp_path):
    j = BannerJournal(tmp_path / "jobs.sqlite3", daily_limit=1)
    j.enqueue(request(), LOGO, now=TIME)
    other = request()
    other = replace(other, snapshot=replace(other.snapshot, content_item_id=str(uuid4())))
    with pytest.raises(BannerError, match="budget_exhausted"):
        j.enqueue(other, LOGO, now=TIME)


def test_queued_job_cannot_start_paid_call_after_kst_day_rollover(journal):
    before = int(datetime(2026, 9, 14, 14, 59, 59, tzinfo=timezone.utc).timestamp())
    req = request()
    req = replace(req, snapshot=replace(req.snapshot,
        source_published_at="2026-09-14T14:00:00Z"))
    journal.enqueue(req, LOGO, now=before)
    owner, provider = Owner(), Provider()
    assert run(BannerJournal(journal.path), owner, provider, now=before + 1) == {
        "status": "obsolete", "provider_called": False,
        "public_send_attempted": False}
    assert provider.calls == owner.saves == 0
    assert journal.status(req.job_id)["state"] == "obsolete"


def test_generated_result_can_commit_after_kst_day_rollover(journal):
    before = int(datetime(2026, 9, 14, 14, 59, 59, tzinfo=timezone.utc).timestamp())
    req = request()
    req = replace(req, snapshot=replace(req.snapshot,
        source_published_at="2026-09-14T14:00:00Z"))
    journal.enqueue(req, LOGO, now=before)
    owner, provider = Owner(), Provider()
    assert run(journal, owner, provider, now=before)["status"] == "result_ready"
    assert run(journal, owner, provider, now=before + 1)["status"] == "revision_saved"
    assert provider.calls == owner.saves == 1


@pytest.mark.parametrize("field,value", [("headline", "sk-" + "a"*45),
    ("instruction", "t.me/+private"), ("subtitle", "\ud800"), ("instruction", ""),
    ("operation_key", "arbitrary"), ("job_id", "not-uuid")])
def test_private_or_invalid_input_rejected(journal, field, value):
    with pytest.raises(BannerError):
        journal.enqueue(request(**{field: value}), LOGO, now=TIME)


def test_stale_source_and_wrong_logo_reject(journal):
    req = request()
    with pytest.raises(BannerError, match="source_stale"):
        journal.enqueue(req, LOGO, now=TIME+86400)
    with pytest.raises(BannerError, match="logo_mismatch"):
        journal.enqueue(req, png((32, 32)), now=TIME)


@pytest.mark.parametrize("status", [200, 429, 500, 302])
def test_real_adapter_contract_no_retries_and_pinned_model(status):
    calls = []
    def transport(req):
        calls.append(req)
        assert str(req.url) == "https://api.openai.com/v1/images/edits"
        assert req.method == "POST"
        assert MODEL.encode() in req.content and b"official-logo.png" in req.content
        return httpx.Response(status, json={"data": [{"b64_json": base64.b64encode(RESULT).decode()}]})
    provider = OpenAIImageEditor("sk-" + "a"*48, transport=httpx.MockTransport(transport))
    if status == 200:
        assert asyncio.run(provider.edit(request(), LOGO)) == RESULT
    else:
        with pytest.raises(BannerError, match="rejected" if status == 429 else "unknown"):
            asyncio.run(provider.edit(request(), LOGO))
    assert len(calls) == 1


@pytest.mark.parametrize("body", [{"data": [{"url": "https://evil.invalid/image"}]},
    {"data": []}, {"data": [{"b64_json": "bad"}]},
    {"data": [{"b64_json": base64.b64encode(LOGO).decode()}]}])
def test_invalid_provider_result_never_follows_urls(body):
    calls = []
    def transport(req):
        calls.append(req)
        return httpx.Response(200, json=body)
    provider = OpenAIImageEditor("sk-"+"a"*48, transport=httpx.MockTransport(transport))
    with pytest.raises(BannerError, match="unknown"):
        asyncio.run(provider.edit(request(), LOGO))
    assert len(calls) == 1


@pytest.mark.parametrize("client,handle", [("yellow","Yellow"),("babylon","babylonlabs_io"),
                                         ("origintrail","origin_trail"),("squid","SquidRouter")])
def test_four_client_bundles_preserve_copy_without_widening_legacy_send(client, handle):
    c, d, _ = fixture(client_id=client, source_url=f"https://x.com/{handle}/status/123")
    bundle = build_client_bundle(ReviewClaim.parse(c, BUNDLE_NOW), d, APP_ORIGIN, BUNDLE_NOW)
    assert bundle.parts[1].text.endswith(d["claim"]["telegram_copy"])
    assert bundle.parts[2].text.endswith(d["claim"]["x_copy"])
    if client != "squid":
        with pytest.raises(ReviewError):
            build_squid_bundle(ReviewClaim.parse(c, BUNDLE_NOW), d, APP_ORIGIN, BUNDLE_NOW)


def test_private_buttons_hide_and_reject_old_publication_tokens():
    s, signer = snapshot(), ButtonSigner(b"k"*32)
    messages = review_messages(s, signer, ROOM, now=NOW, private_only=True)
    buttons = sum(messages["controls"]["reply_markup"]["inline_keyboard"], [])
    assert len(buttons) == 6
    assert "다시 만들기" in buttons[2]["text"]
    assert not any("게시" in b["text"] for b in buttons)
    event = VerifiedCallback("old-click", "reviewer-one", False, ROOM, "fixture-message",
                             signer.issue(s, "a", ROOM, now=NOW, expires_at=NOW+1800))
    owner = FakeOwner(s)
    with pytest.raises(ButtonReviewError, match="private_publication_forbidden"):
        handle_review_callback(event, enabled=True, signer=signer, owner=owner,
            allowed_reviewers=frozenset({"reviewer-one"}), room_binding=ROOM, now=NOW, private_only=True)
    assert owner.applies == 0 and not owner.outbox
