"""Synthetic local bundle contracts. No live Telegram, database or provider."""
import asyncio
import hashlib
import io

import httpx
import pytest
from PIL import Image

from core.content_ops.squid_bundle import build_squid_bundle, validate_bundle_image, bundle_delivery_label
from core.content_ops.worker import ReviewClaim, ReviewError, ReviewWorker, DeliveryReceipt, TelegramReviewRelay, HttpReviewGateway
from test_content_ops_worker import NOW, SHA, BOT_ID, CHAT_ID, CANARY_VERSION, APP_ORIGIN, claim, settings


def configured():
    return settings(CONTENT_OPS_REVIEW_MODE="canary", CONTENT_OPS_REVIEW_CANARY_VERSION_ID=CANARY_VERSION,
                    CONTENT_OPS_REVIEW_PACKET_MODE="squid_bundle_v1")


def fixture(**changes):
    image = io.BytesIO()
    Image.new("RGB", (32, 24), "blue").save(image, format="PNG")
    data = image.getvalue()
    c = claim(banner_sha256=hashlib.sha256(data).hexdigest(), **changes)
    detail = {"claim": {**c, "telegram_copy": "한" * 2400 + "<b>& original\n끝",
                       "x_copy": "한" * 700 + "\n끝"},
              "asset": {"sha256": c["banner_sha256"], "byte_size": len(data), "width": 32, "height": 24},
              "snapshot_sha256": "d" * 64}
    return c, detail, data


def test_bundle_preserves_full_copy_and_exact_source_version_and_image_bytes():
    c, d, data = fixture()
    b = build_squid_bundle(ReviewClaim.parse(c, NOW), d, APP_ORIGIN, NOW)
    assert len(b.parts) == 3 and [p.kind for p in b.parts] == ["image", "telegram", "x"]
    assert b.parts[1].text.endswith(d["claim"]["telegram_copy"])
    assert b.parts[2].text.endswith(d["claim"]["x_copy"])
    assert c["source_url"] in b.parts[0].text
    assert "version=" + c["content_version_id"] in b.parts[0].text
    assert all(c["content_version_id"] in p.text for p in b.parts)
    assert validate_bundle_image(data, b) is data
    assert len({p.sha256 for p in b.parts}) == 3


@pytest.mark.parametrize("field,value", [("source_item_id", "99999999-9999-4999-8999-999999999999"),
    ("content_version_id", "99999999-9999-4999-8999-999999999999"), ("banner_sha256", "f" * 64),
    ("source_url", "https://x.com/SquidRouter/status/999"), ("client_id", "yellow")])
def test_different_identity_never_replaces_claim(field, value):
    c, d, _ = fixture(); d["claim"][field] = value
    with pytest.raises(ReviewError):
        build_squid_bundle(ReviewClaim.parse(c, NOW), d, APP_ORIGIN, NOW)


@pytest.mark.parametrize("bad", [b"invalid", b"\x89PNG\r\n\x1a\n" + b"x" * 50])
def test_image_decode_and_hash_are_required(bad):
    c, d, _ = fixture(); c["banner_sha256"] = hashlib.sha256(bad).hexdigest()
    d["claim"]["banner_sha256"] = c["banner_sha256"]
    d["asset"].update(sha256=c["banner_sha256"], byte_size=max(45, len(bad)))
    b = build_squid_bundle(ReviewClaim.parse(c, NOW), d, APP_ORIGIN, NOW)
    with pytest.raises(ReviewError): validate_bundle_image(bad, b)


def test_full_or_block_no_unicode_truncation_and_no_daily_expansion():
    c, d, _ = fixture(); d["claim"]["telegram_copy"] = "😀" * 2300
    with pytest.raises(ReviewError): build_squid_bundle(ReviewClaim.parse(c, NOW), d, APP_ORIGIN, NOW)
    with pytest.raises(ValueError): settings(CONTENT_OPS_REVIEW_PACKET_MODE="squid_bundle_v1")
    assert settings().packet_mode == "link_card"
    assert configured().max_claims == 1


class Gateway:
    def __init__(self, *, fail=None, change=None, bad_image=False):
        self.c, self.detail, self.image = fixture()
        self.fail, self.change, self.bad_image = fail, change, bad_image
        self.claimed = False; self.receipts = []; self.calls = []; self.status = "pending"
    async def request(self, action, **fields):
        self.calls.append((action, fields)); index = fields.get("part_index")
        if self.fail == (action, index): raise httpx.ReadTimeout("private diagnostic")
        r = {"ok": True, "release_sha": SHA, "scope": configured().scope, "accepted": True}
        if action == "reconcile": r["queued"] = 0 if self.claimed else 1
        elif action == "claim":
            if self.claimed: r["claim"] = None
            else:
                self.claimed = True; self.c["claim_token"] = fields["claim_token"]
                self.detail["claim"]["claim_token"] = fields["claim_token"]; r["claim"] = self.c
        elif action == "bundle_prepare": r["bundle"] = self.detail
        elif action == "bundle_begin": self.status = "sending"
        elif action == "bundle_part_begin":
            r["accepted"] = self.status == "sending" and len(self.receipts) == index
        elif action == "bundle_part_finish":
            self.receipts.append({"index": index, "kind": ["image", "telegram", "x"][index],
                "outcome": fields["outcome"], "message_id": fields["message_id"]})
            self.status = ("delivery_unknown" if self.change == index or fields["outcome"] != "sent"
                           else "sent" if index == 2 else "sending")
            r.update(status=self.status, receipts=list(self.receipts))
        else: raise AssertionError(action)
        return r
    async def load_bundle_image(self, c, snapshot):
        self.calls.append(("image", {})); return b"bad" if self.bad_image else self.image


class Relay:
    def __init__(self, fail=None): self.parts = []; self.preflights = 0; self.fail = fail
    async def preflight(self): self.preflights += 1
    async def send_bundle_part(self, part, image):
        self.parts.append(part)
        if part.index == self.fail: raise httpx.ReadTimeout("no replay")
        return DeliveryReceipt("sent", 100 + part.index)


def run(g, r): return asyncio.run(ReviewWorker(configured(), g, r, now=lambda: NOW).run())


def test_complete_requires_three_durable_receipts_and_repeat_run_cannot_resend():
    g, r = Gateway(), Relay(); result = run(g, r)
    assert result["team_delivery_complete"] is True and result["sent"] == 1
    assert result["delivery_label"] == "팀 전달 완료" and result["parts_received"] == 3
    assert r.preflights == 4
    actions = [a for a, _ in g.calls]
    assert actions.index("image") < actions.index("bundle_begin")
    assert actions[-6:] == ["bundle_part_begin", "bundle_part_finish"] * 3
    assert run(g, r)["claimed"] == 0 and len(r.parts) == 3
    assert not {"begin", "finish"}.intersection(actions)


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("failure", ["send", "begin_ack", "finish_ack", "version_changed"])
def test_partial_unknown_and_version_change_never_complete_or_replay(index, failure):
    g, r = Gateway(), Relay()
    if failure == "send": r.fail = index
    if failure == "begin_ack": g.fail = ("bundle_part_begin", index)
    if failure == "finish_ack": g.fail = ("bundle_part_finish", index)
    if failure == "version_changed": g.change = index
    result = run(g, r)
    assert result["sent"] == 0 and result["team_delivery_complete"] is False
    assert result["delivery_label"] != "팀 전달 완료"
    count = len(r.parts)
    assert count == index + (failure != "begin_ack")
    run(g, r); assert len(r.parts) == count


def test_private_image_failure_blocks_every_send_before_begin():
    g, r = Gateway(bad_image=True), Relay(); result = run(g, r)
    assert result["blocked"] == 1 and not r.parts
    assert "bundle_begin" not in [a for a, _ in g.calls]


@pytest.mark.parametrize("bad", [None, "sha", "version", "snapshot", "range", "redirect", "mime", "length"])
def test_courier_image_endpoint_pinned_and_no_storage_credential_or_fallback(bad):
    c, _, data = fixture(); parsed = ReviewClaim.parse(c, NOW); calls = []
    def transport(req):
        calls.append(req)
        assert str(req.url).startswith(APP_ORIGIN + "/.netlify/functions/content-ops-review")
        assert req.headers["x-content-ops-packet-mode"] == "squid_bundle_v1"
        assert "apikey" not in req.headers
        h = {"content-type": "image/png", "content-length": str(len(data)), "x-content-ops-release-sha": SHA,
             "x-content-ops-version-id": CANARY_VERSION, "x-content-ops-snapshot-sha256": "d" * 64}
        field = {"sha": "x-content-ops-release-sha", "version": "x-content-ops-version-id",
            "snapshot": "x-content-ops-snapshot-sha256", "range": "content-range", "redirect": "location",
            "mime": "content-type", "length": "content-length"}.get(bad)
        if field: h[field] = "invalid"
        return httpx.Response(200, content=data, headers=h)
    gateway = HttpReviewGateway(configured(), transport=httpx.MockTransport(transport))
    if bad is None: assert asyncio.run(gateway.load_bundle_image(parsed, "d" * 64)) == data
    else:
        with pytest.raises(ReviewError): asyncio.run(gateway.load_bundle_image(parsed, "d" * 64))
    assert len(calls) == 1


def test_apng_and_dimension_mismatch_do_not_pass_the_courier_envelope():
    c, d, data = fixture()
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), "blue").save(stream, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (32, 24), "red")], duration=100, loop=0)
    animated = stream.getvalue()
    for value, width in [(animated, 32), (data, 31)]:
        digest = hashlib.sha256(value).hexdigest()
        c["banner_sha256"] = digest; d["claim"]["banner_sha256"] = digest
        d["asset"].update(sha256=digest, byte_size=len(value), width=width)
        b = build_squid_bundle(ReviewClaim.parse(c, NOW), d, APP_ORIGIN, NOW)
        with pytest.raises(ReviewError): validate_bundle_image(value, b)


@pytest.mark.parametrize("status,receipts", [("sent", []), ("sent", [{"outcome": "sent", "message_id": 1}]),
    ("delivery_unknown", [{"index": i, "kind": k, "outcome": "sent", "message_id": i+1}
                          for i, k in enumerate(["image", "telegram", "x"])])])
def test_ui_label_does_not_trust_parent_status_or_partial_ids(status, receipts):
    assert bundle_delivery_label(status, receipts) != "팀 전달 완료"


@pytest.mark.parametrize("kind", [0, 1, 2])
@pytest.mark.parametrize("bad", [None, "chat", "sender", "text", "timeout"])
def test_same_relay_multipart_plain_copy_identity_and_one_shot(kind, bad):
    c, d, data = fixture(); b = build_squid_bundle(ReviewClaim.parse(c, NOW), d, APP_ORIGIN, NOW)
    part = b.parts[kind]; calls = []
    def transport(req):
        calls.append(req)
        assert req.url.host == "api.telegram.org"
        if kind == 0:
            assert req.url.path.endswith("/sendPhoto") and "multipart/form-data" in req.headers["content-type"]
            assert data in req.content and b'filename="news-card.png"' in req.content
        else:
            import json
            payload = json.loads(req.content); assert payload["text"] == part.text and "parse_mode" not in payload
        if bad == "timeout": raise httpx.ReadTimeout("private")
        result = {"message_id": 12, "chat": {"id": int(CHAT_ID), "type": "supergroup"},
            "from": {"id": BOT_ID, "is_bot": True}, "photo": [{"file_id": "synthetic"}],
            "caption" if kind == 0 else "text": part.text}
        if bad == "chat": result["chat"]["id"] -= 1
        if bad == "sender": result["from"]["id"] += 1
        if bad == "text": result["caption" if kind == 0 else "text"] = "changed"
        return httpx.Response(200, json={"ok": True, "result": result})
    relay = TelegramReviewRelay(configured(), transport=httpx.MockTransport(transport)); relay._verified = True
    receipt = asyncio.run(relay.send_bundle_part(part, data))
    assert receipt.outcome == ("sent" if bad is None else "delivery_unknown")
    with pytest.raises(ReviewError): asyncio.run(relay.send_bundle_part(part, data))
    assert len(calls) == 1
