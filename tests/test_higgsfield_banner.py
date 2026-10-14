"""Offline reference-quality contract tests. No API key or live network."""
import asyncio
import hashlib
import io
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from core.automation.higgsfield_banner import (
    BannerReference, HiggsfieldQualityBanner, QualityBannerError,
    QualityBannerRequest, canonical_png, quality_prompt,
)

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
WORKSPACE_ID = "296bfec3-7975-4f64-8acb-8f9333e94555"
SOURCE_ID = "443379ea-36aa-4466-87c9-e716b59f59e3"
PROVIDER_ID = "663b7c4f-e8af-4700-bbe1-7f3d1ff36caa"
REF_HOSTS = frozenset({"pbs.twimg.com", "assets.example.com"})
OUT_HOSTS = frozenset({"output.example.com"})
SUBMITTED = {"status": "queued", "request_id": PROVIDER_ID,
             "status_url": "https://api.higgsfield.ai/requests/" + PROVIDER_ID + "/status"}


def reference_bytes():
    out = io.BytesIO()
    Image.new("RGB", (64, 64), "yellow").save(out, format="PNG")
    return out.getvalue()


REFERENCE_BYTES = reference_bytes()
REFERENCE_SHA = hashlib.sha256(REFERENCE_BYTES).hexdigest()


def request(**changes):
    value = QualityBannerRequest(operation_id=str(uuid4()), workspace_id=WORKSPACE_ID,
        client_id="yellow", source_item_id=SOURCE_ID, source_url="https://x.com/Yellow/status/1234",
        source_sha256="a" * 64, source_published_at="2026-10-12T09:00:00Z",
        headline="공식 업데이트", subtitle="새로운 소식을 확인하세요", design_feedback="넉넉한 행간",
        references=(BannerReference("official_source", "https://pbs.twimg.com/media/image.png", REFERENCE_SHA),
                    BannerReference("official_logo", "https://assets.example.com/logo.png", REFERENCE_SHA)))
    return replace(value, **changes)


@pytest.mark.parametrize("client,positive,negative", [
    ("yellow", "spacious headline leading", "weight 600"),
    ("babylon", "weight 600", "-0.01em"),
    ("origintrail", "-0.01em", "weight 600"),
])
def test_designer_feedback_is_client_specific_and_digest_bound(client, positive, negative, monkeypatch):
    from core.automation import higgsfield_banner as module
    req = request(client_id=client)
    prompt = quality_prompt(req)
    assert positive in prompt and negative not in prompt
    assert "official-reference-sunburst-max@2" == module.POLICY
    digest = req.digest()
    monkeypatch.setitem(module.TYPOGRAPHY, client, module.TYPOGRAPHY[client] + " changed")
    assert req.digest() != digest


def image_bytes(fmt="PNG", size=(2880, 2880), mode="RGB"):
    out = io.BytesIO()
    color = (80, 20, 120, 0) if mode == "RGBA" else (80, 20, 120)
    Image.new(mode, size, color).save(out, format=fmt)
    return out.getvalue()


class Owner:
    def __init__(self):
        self.started = {}
        self.ids = {}
        self.results = {}
        self.fail_commit = False

    def begin_submit(self, operation_id, digest, *, workspace_id, client_id, source_item_id):
        assert (workspace_id, client_id, source_item_id) == (WORKSPACE_ID, "yellow", SOURCE_ID)
        if operation_id in self.started:
            return False
        self.started[operation_id] = digest
        return True

    def record_submitted(self, operation_id, digest, request_id):
        assert self.started[operation_id] == digest
        if self.fail_commit:
            raise OSError("private DB lost acknowledgment")
        self.ids[operation_id] = request_id

    def read_request_id(self, operation_id, digest):
        assert self.started[operation_id] == digest
        return self.ids[operation_id]

    def record_result(self, operation_id, digest, request_id, png):
        assert self.started[operation_id] == digest and self.ids[operation_id] == request_id
        self.results[operation_id] = hashlib.sha256(png).hexdigest()


def adapter(owner, handler, **changes):
    reference_handler = changes.pop("reference_handler", None)
    def route(http):
        if http.url.host in REF_HOSTS:
            if reference_handler is not None:
                return reference_handler(http)
            assert http.method == "GET" and "authorization" not in http.headers
            return httpx.Response(200, content=REFERENCE_BYTES)
        return handler(http)
    values = dict(key_id="test-key-id", key_secret="test-key-secret", owner=owner,
                  reference_hosts=REF_HOSTS, output_hosts=OUT_HOSTS,
                  transport=httpx.MockTransport(route), clock=lambda: NOW,
                  sleep=lambda delay: asyncio.sleep(0))
    values.update(changes)
    return HiggsfieldQualityBanner(**values)


def run(awaitable):
    return asyncio.run(awaitable)


def test_submit_then_get_only_collect_and_normalize_webp():
    owner, req, calls = Owner(), request(), []
    raw = image_bytes("WEBP")
    def handler(http):
        calls.append(http)
        if http.method == "POST":
            assert owner.started[req.operation_id] == req.digest()
            assert http.headers["authorization"] == "Key test-key-id:test-key-secret"
            assert http.headers["idempotency-key"] == req.operation_id
            import json
            body = json.loads(http.content)
            assert body["quality"] == "max" and body["resolution"] == "4k"
            assert body["aspect_ratio"] == "1:1" and body["enhance_prompt"] is False
            assert body["image_urls"] == [ref.url for ref in req.references]
            return httpx.Response(200, json=SUBMITTED)
        if http.url.host == "api.higgsfield.ai":
            assert owner.ids[req.operation_id] == PROVIDER_ID
            return httpx.Response(200, json={"status": "completed", "request_id": PROVIDER_ID,
                "images": [{"url": "https://output.example.com/result.webp"}]})
        assert "authorization" not in http.headers
        return httpx.Response(200, content=raw)
    runner = adapter(owner, handler)
    assert run(runner.submit_once(req)) == PROVIDER_ID
    png = run(runner.collect(req))
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(png)) as decoded:
        assert decoded.size == (2880, 2880) and decoded.mode == "RGB"
    assert owner.results[req.operation_id] == hashlib.sha256(png).hexdigest()
    assert [http.method for http in calls] == ["POST", "GET", "GET"]
    with pytest.raises(QualityBannerError, match="already_reserved"):
        run(adapter(owner, handler).submit_once(req))
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["timeout", "body", "commit"])
def test_unknown_submit_never_reposts_in_recreated_adapter(failure):
    owner, req, calls = Owner(), request(), []
    def handler(http):
        calls.append(http)
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive provider error")
        if failure == "body":
            return httpx.Response(200, content=b"not json: secret")
        return httpx.Response(200, json=SUBMITTED)
    owner.fail_commit = failure == "commit"
    with pytest.raises(QualityBannerError) as caught:
        run(adapter(owner, handler).submit_once(req))
    assert "secret" not in str(caught.value)
    with pytest.raises(QualityBannerError, match="already_reserved"):
        run(adapter(owner, handler).submit_once(req))
    assert len(calls) == 1


@pytest.mark.parametrize("status_url", [None, "https://evil.example/status",
    "https://api.higgsfield.ai/requests/00000000-0000-4000-8000-000000000001/status"])
def test_returned_status_link_must_match_id_before_recording(status_url):
    owner, req, calls = Owner(), request(), []
    def handler(http):
        calls.append(http)
        return httpx.Response(200, json={**SUBMITTED, "status_url": status_url})
    with pytest.raises(QualityBannerError, match="provider_unknown"):
        run(adapter(owner, handler).submit_once(req))
    assert len(calls) == 1 and not owner.ids
    with pytest.raises(QualityBannerError, match="already_reserved"):
        run(adapter(owner, handler).submit_once(req))
    assert len(calls) == 1


def test_pending_poll_is_bounded_no_cancel_or_resubmit():
    owner, req, methods = Owner(), request(), []
    owner.started[req.operation_id], owner.ids[req.operation_id] = req.digest(), PROVIDER_ID
    def handler(http):
        methods.append(http.method)
        return httpx.Response(200, json={"status": "queued", "request_id": PROVIDER_ID})
    with pytest.raises(QualityBannerError, match="quality_banner_pending"):
        run(adapter(owner, handler).collect(req, max_polls=3))
    assert methods == ["GET"] * 3


@pytest.mark.parametrize("status", ["failed", "nsfw", "canceled", "unexpected"])
def test_terminal_or_unknown_status_never_posts(status):
    owner, req, methods = Owner(), request(), []
    owner.started[req.operation_id], owner.ids[req.operation_id] = req.digest(), PROVIDER_ID
    def handler(http):
        methods.append(http.method)
        return httpx.Response(200, json={"status": status, "request_id": PROVIDER_ID})
    with pytest.raises(QualityBannerError):
        run(adapter(owner, handler).collect(req))
    assert methods == ["GET"]


@pytest.mark.parametrize("changes", [
    {"source_published_at": "2026-10-11T12:00:00Z"},
    {"source_published_at": "2026-10-12T12:01:00Z"},
    {"source_published_at": "2026-10-12T12:00:00"},
    {"source_url": "https://x.com/foreign/status/1234"},
    {"workspace_id": "bad"}, {"source_item_id": "bad"},
    {"workspace_id": "00000000-0000-0000-0000-000000000000"},
    {"source_item_id": "00000000-0000-0000-0000-000000000000"},
    {"client_id": "squid"}, {"source_sha256": "bad"},
    {"headline": "Authorization: token"},
    {"references": ()},
])
def test_invalid_request_never_reserves_or_calls(changes):
    owner = Owner()
    def handler(http):
        pytest.fail("unexpected network")
    with pytest.raises(QualityBannerError):
        run(adapter(owner, handler).submit_once(request(**changes)))
    assert not owner.started


@pytest.mark.parametrize("url", ["https://127.0.0.1/output", "http://output.example.com/a",
    "https://output.example.com.evil/a", "https://user:secret@output.example.com/a"])
def test_untrusted_output_url_never_downloaded(url):
    owner, req, calls = Owner(), request(), []
    owner.started[req.operation_id], owner.ids[req.operation_id] = req.digest(), PROVIDER_ID
    def handler(http):
        calls.append(http)
        return httpx.Response(200, json={"status": "completed", "request_id": PROVIDER_ID,
            "images": [{"url": url}]})
    with pytest.raises(QualityBannerError):
        run(adapter(owner, handler).collect(req))
    assert len(calls) == 1 and not owner.results


@pytest.mark.parametrize("size,mode", [((1024, 1024), "RGB"), ((2880, 2000), "RGB"),
                                     ((2880, 2880), "RGBA")])
def test_no_upscale_non_square_or_transparency(size, mode):
    with pytest.raises(QualityBannerError, match="image_invalid"):
        canonical_png(image_bytes(size=size, mode=mode))


def test_quality_policy_is_fact_neutral_and_digest_covers_exact_reference():
    req = request()
    prompt = quality_prompt(req)
    assert "28px" in prompt and "18px" in prompt and "7%" in prompt
    assert "10/8" not in prompt and "HashKey" not in prompt and "SCAN" not in prompt
    assert req.digest() != replace(req, headline="수정된 공지").digest()
    assert req.digest() != replace(req, workspace_id=str(uuid4())).digest()


@pytest.mark.parametrize("deny_at,expected_http,owner_started", [
    (1, 0, False),  # first reference GET
    (2, 1, False),  # second reference GET
    (3, 2, False),  # durable submit reservation
    (4, 2, True),   # provider generation POST
])
def test_deadline_guard_runs_at_every_submit_dispatch(deny_at, expected_http, owner_started):
    owner, req, calls, checks = Owner(), request(), [], []
    def guard():
        checks.append(True)
        if len(checks) == deny_at:
            raise ValueError("trial_deadline_closed")
    def route(http):
        calls.append(http)
        return httpx.Response(200, json=SUBMITTED)
    with pytest.raises(QualityBannerError):
        run(adapter(owner, route, before_effect=guard,
            reference_handler=lambda http: (calls.append(http), httpx.Response(200, content=REFERENCE_BYTES))[1]
        ).submit_once(req))
    assert len(calls) == expected_http
    assert (req.operation_id in owner.started) is owner_started


def test_deadline_guard_stops_estimate_and_each_collect_http():
    owner, req, calls = Owner(), request(), []
    def route(http):
        calls.append(http)
        if http.url.host == "api.higgsfield.ai":
            return httpx.Response(200, json={"status": "completed", "request_id": PROVIDER_ID,
                "images": [{"url": "https://output.example.com/result.webp"}]})
        return httpx.Response(200, content=image_bytes("WEBP"))
    def deny():
        raise ValueError("trial_deadline_closed")
    with pytest.raises(QualityBannerError):
        run(adapter(owner, route, before_effect=deny).estimate(req,
            provider_account_sha256="a" * 64))
    assert calls == []
    owner.started[req.operation_id], owner.ids[req.operation_id] = req.digest(), PROVIDER_ID
    with pytest.raises(QualityBannerError):
        run(adapter(owner, route, before_effect=deny).collect(req))
    assert calls == []
    checks = []
    def deny_image():
        checks.append(True)
        if len(checks) == 2:
            raise ValueError("trial_deadline_closed")
    with pytest.raises(QualityBannerError):
        run(adapter(owner, route, before_effect=deny_image).collect(req))
    assert len(calls) == 1 and calls[0].url.host == "api.higgsfield.ai"
    assert req.digest() != replace(req, source_item_id=str(uuid4())).digest()
    assert req.digest() != replace(req, client_id="babylon").digest()
    revised = replace(req.references[0], sha256="d" * 64)
    assert req.digest() != replace(req, references=(revised, req.references[1])).digest()


def concrete_journal(tmp_path, *, source_id=SOURCE_ID, workspace_id=WORKSPACE_ID,
                     client_id="yellow"):
    from core.content_ops.higgsfield_banner_journal import HiggsfieldBannerJournal
    tmp_path.chmod(0o700)
    return HiggsfieldBannerJournal(tmp_path / "attempts.sqlite3",
        workspace_id=workspace_id, client_id=client_id,
        source_item_id=source_id, daily_limit=3, clock=lambda: NOW.timestamp())


def test_concrete_owner_restart_resumes_get_only(tmp_path):
    source_id = str(uuid4())
    req, methods = request(source_item_id=source_id), []
    raw = image_bytes()
    def handler(http):
        methods.append(http.method)
        if http.method == "POST":
            return httpx.Response(200, json=SUBMITTED)
        if http.url.host == "api.higgsfield.ai":
            return httpx.Response(200, json={"status": "completed", "request_id": PROVIDER_ID,
                "images": [{"url": "https://output.example.com/result.png"}]})
        return httpx.Response(200, content=raw)
    owner = concrete_journal(tmp_path, source_id=source_id)
    assert run(adapter(owner, handler).submit_once(req)) == PROVIDER_ID
    restarted = concrete_journal(tmp_path, source_id=source_id)
    with pytest.raises(QualityBannerError, match="already_reserved"):
        run(adapter(restarted, handler).submit_once(req))
    png = run(adapter(restarted, handler).collect(req))
    assert methods == ["POST", "GET", "GET"]
    assert png.startswith(b"\x89PNG")
    # Same source + fresh operation UUID must not evade durable source dedupe.
    with pytest.raises(QualityBannerError, match="already_reserved"):
        run(adapter(restarted, handler).submit_once(replace(req, operation_id=str(uuid4()))))
    assert methods == ["POST", "GET", "GET"]


def test_concrete_owner_unknown_submit_is_terminal_after_restart(tmp_path):
    source_id = str(uuid4())
    req, methods = request(source_item_id=source_id), []
    def handler(http):
        methods.append(http.method)
        raise httpx.ReadTimeout("ambiguous first write")
    owner = concrete_journal(tmp_path, source_id=source_id)
    with pytest.raises(QualityBannerError, match="submit_unknown"):
        run(adapter(owner, handler).submit_once(req))
    restarted = concrete_journal(tmp_path, source_id=source_id)
    with pytest.raises(QualityBannerError, match="already_reserved"):
        run(adapter(restarted, handler).submit_once(req))
    with pytest.raises(QualityBannerError, match="collect_unknown"):
        run(adapter(restarted, handler).collect(req))
    assert methods == ["POST"]


@pytest.mark.parametrize("changes", [
    {"workspace_id": "55555555-5555-4555-8555-555555555555"},
    {"client_id": "babylon", "source_url": "https://x.com/babylonlabs_io/status/1234"},
    {"source_item_id": "66666666-6666-4666-8666-666666666666"},
])
def test_concrete_owner_scope_mismatch_never_posts_or_reserves(tmp_path, changes):
    req, methods = request(), []
    def handler(http):
        methods.append(http.method)
        return httpx.Response(200, json=SUBMITTED)
    journal = concrete_journal(tmp_path)
    with pytest.raises(QualityBannerError, match="submit_unknown"):
        run(adapter(journal, handler).submit_once(replace(req, **changes)))
    assert methods == []
    # Same operation, matching immutable context succeeds; neither the
    # mismatched digest nor wrong-scope attempt consumed an initial reservation.
    assert run(adapter(concrete_journal(tmp_path), handler).submit_once(req)) == PROVIDER_ID
    assert methods == ["POST"]
    with pytest.raises(QualityBannerError, match="already_reserved"):
        run(adapter(concrete_journal(tmp_path), handler).submit_once(
            replace(req, operation_id=str(uuid4()))))
    assert methods == ["POST"]


@pytest.mark.parametrize("changes", [
    {"workspace_id": "55555555-5555-4555-8555-555555555555"},
    {"client_id": "babylon", "source_url": "https://x.com/babylonlabs_io/status/1234"},
    {"source_item_id": "66666666-6666-4666-8666-666666666666"},
])
def test_collect_changed_scope_cannot_read_provider_id(tmp_path, changes):
    req, methods = request(), []
    def handler(http):
        methods.append(http.method)
        return httpx.Response(200, json=SUBMITTED)
    journal = concrete_journal(tmp_path)
    assert run(adapter(journal, handler).submit_once(req)) == PROVIDER_ID
    changed = replace(req, **changes)
    with pytest.raises(QualityBannerError, match="collect_unknown"):
        run(adapter(concrete_journal(tmp_path), handler).collect(changed))
    # Even reconstructing the owner for that changed scope cannot read the
    # original operation, whose digest and stored scope are both immutable.
    changed_owner = concrete_journal(tmp_path, workspace_id=changed.workspace_id,
        client_id=changed.client_id, source_id=changed.source_item_id)
    with pytest.raises(QualityBannerError, match="collect_unknown"):
        run(adapter(changed_owner, handler).collect(changed))
    assert methods == ["POST"]


def test_expired_source_can_reconcile_but_cannot_submit():
    owner, req, methods = Owner(), request(), []
    raw = image_bytes()
    owner.started[req.operation_id], owner.ids[req.operation_id] = req.digest(), PROVIDER_ID
    def handler(http):
        methods.append(http.method)
        if http.url.host == "api.higgsfield.ai":
            return httpx.Response(200, json={"status": "completed", "request_id": PROVIDER_ID,
                "images": [{"url": "https://output.example.com/result.png"}]})
        return httpx.Response(200, content=raw)
    late = adapter(owner, handler, clock=lambda: NOW + timedelta(days=2))
    assert run(late.collect(req)).startswith(b"\x89PNG")
    with pytest.raises(QualityBannerError, match="source_stale"):
        run(late.submit_once(replace(req, operation_id=str(uuid4()))))
    assert methods == ["GET", "GET"]


def test_reference_reads_verify_bytes_before_reserve_without_credentials():
    owner, req, calls = Owner(), request(), []
    def reference_handler(http):
        assert not owner.started
        assert http.method == "GET"
        assert "authorization" not in http.headers and "idempotency-key" not in http.headers
        calls.append(str(http.url))
        return httpx.Response(200, content=REFERENCE_BYTES,
                              headers={"Content-Type": "application/octet-stream"})
    def handler(http):
        assert calls == [ref.url for ref in req.references]
        assert owner.started[req.operation_id] == req.digest()
        return httpx.Response(200, json=SUBMITTED)
    assert run(adapter(owner, handler, reference_handler=reference_handler).submit_once(req)) == PROVIDER_ID


@pytest.mark.parametrize("index", [0, 1])
def test_wrong_reference_hash_never_reserves_or_posts(index):
    owner, req, reads = Owner(), request(), []
    references = list(req.references)
    references[index] = replace(references[index], sha256="f" * 64)
    req = replace(req, references=tuple(references))
    def reference_handler(http):
        reads.append(http)
        return httpx.Response(200, content=REFERENCE_BYTES)
    def handler(http):
        pytest.fail("reference mismatch reached provider")
    with pytest.raises(QualityBannerError, match="reference_invalid"):
        run(adapter(owner, handler, reference_handler=reference_handler).submit_once(req))
    assert len(reads) == index + 1 and not owner.started


@pytest.mark.parametrize("status", [301, 302, 307, 404, 500])
def test_reference_redirect_or_error_is_not_followed_or_reserved(status):
    owner, calls = Owner(), []
    def reference_handler(http):
        calls.append(http)
        return httpx.Response(status, headers={"Location": "https://evil.example/private"})
    def handler(http):
        pytest.fail("unverified reference reached provider or redirect host")
    with pytest.raises(QualityBannerError, match="reference_unavailable"):
        run(adapter(owner, handler, reference_handler=reference_handler).submit_once(request()))
    assert len(calls) == 1 and not owner.started


@pytest.mark.parametrize("url", ["https://pbs.twimg.com.evil/image.png",
    "http://pbs.twimg.com/image.png", "https://127.0.0.1/image.png",
    "https://user:password@pbs.twimg.com/image.png"])
def test_untrusted_reference_host_never_reads_reserves_or_posts(url):
    owner, req = Owner(), request()
    req = replace(req, references=(replace(req.references[0], url=url), req.references[1]))
    def forbidden(http):
        pytest.fail("invalid reference must fail before HTTP")
    with pytest.raises(QualityBannerError):
        run(adapter(owner, forbidden, reference_handler=forbidden).submit_once(req))
    assert not owner.started


def test_hash_matching_non_image_reference_is_rejected_before_reserve():
    raw = b"not a decodable image" * 4
    req, owner = request(), Owner()
    req = replace(req, references=(replace(req.references[0], sha256=hashlib.sha256(raw).hexdigest()),
                                  req.references[1]))
    def handler(http):
        pytest.fail("invalid reference reached provider")
    with pytest.raises(QualityBannerError, match="reference_invalid"):
        run(adapter(owner, handler, reference_handler=lambda http: httpx.Response(200, content=raw))
            .submit_once(req))
    assert not owner.started


def test_reference_byte_limit_and_source_expiry_do_not_consume_reservation(monkeypatch):
    import core.automation.higgsfield_banner as module
    owner, req = Owner(), request()
    def handler(http):
        pytest.fail("invalid reference reached provider")
    monkeypatch.setattr(module, "MAX_BYTES", len(REFERENCE_BYTES) - 1)
    with pytest.raises(QualityBannerError, match="reference_invalid"):
        run(adapter(owner, handler).submit_once(req))
    assert not owner.started
    monkeypatch.setattr(module, "MAX_BYTES", 24 * 1024 * 1024)
    times = iter((NOW, NOW + timedelta(days=1)))
    with pytest.raises(QualityBannerError, match="source_stale"):
        run(adapter(owner, handler, clock=lambda: next(times)).submit_once(req))
    assert not owner.started


def test_reference_pixel_bound_rejects_before_reserve(monkeypatch):
    import core.automation.higgsfield_banner as module
    owner = Owner()
    monkeypatch.setattr(module, "MAX_REFERENCE_PIXELS", 64 * 64 - 1)
    def forbidden(http):
        pytest.fail("oversized reference must not reach paid provider")
    with pytest.raises(QualityBannerError, match="reference_invalid"):
        run(adapter(owner, forbidden).submit_once(request()))
    assert not owner.started


def test_restart_returns_exact_staged_result_without_any_http(tmp_path):
    req = request()
    journal = concrete_journal(tmp_path)
    assert journal.begin_submit(req.operation_id, req.digest(), workspace_id=req.workspace_id,
                                client_id=req.client_id, source_item_id=req.source_item_id)
    journal.record_submitted(req.operation_id, req.digest(), PROVIDER_ID)
    png = image_bytes()
    journal.record_result(req.operation_id, req.digest(), PROVIDER_ID, png)
    def forbidden(http):
        pytest.fail("staged result must not access provider or reference host")
    restarted = adapter(concrete_journal(tmp_path), forbidden, reference_handler=forbidden,
                        clock=lambda: NOW + timedelta(days=2))
    assert run(restarted.collect(req)) == png
    with pytest.raises(QualityBannerError, match="collect_unknown"):
        run(restarted.collect(replace(req, headline="변경된 문안")))


@pytest.mark.parametrize("staged", [b"not png", image_bytes("WEBP"),
    image_bytes(size=(1024, 1024)), image_bytes(mode="RGBA")])
def test_invalid_cached_result_does_not_fall_back_to_http(staged):
    class CachedOwner(Owner):
        def read_result(self, operation_id, digest):
            return staged
    def forbidden(http):
        pytest.fail("invalid stored result must not fall back to HTTP")
    with pytest.raises(QualityBannerError, match="image_invalid"):
        run(adapter(CachedOwner(), forbidden, reference_handler=forbidden).collect(request()))
