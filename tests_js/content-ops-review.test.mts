import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { createContentOpsReviewHandler } from "../netlify/functions/_shared/content-ops-review.mts";

const SHA = "a".repeat(40);
const TOKEN = "test-only-dedicated-gateway-token-123456";
const ID = "11111111-1111-4111-8111-111111111111";
const SCOPE = { mode: "daily", content_version_id: null };
const ENV = {
  CONTENT_OPS_GATEWAY_ENABLED: "true", CONTENT_OPS_REVIEW_RELEASE_SHA: SHA,
  CONTENT_OPS_GATEWAY_TOKEN: TOKEN, CONTEXT: "production",
  SUPABASE_URL: "https://example.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "test-database-authority",
  CONTENT_STUDIO_WORKSPACE_ID: ID,
  CONTENT_OPS_REVIEW_MODE: "daily",
};
function request(body: unknown = { action: "reconcile" }, overrides: RequestInit = {}) {
  return new Request("https://coineasy-newscard.netlify.app/.netlify/functions/content-ops-review", {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${TOKEN}`, "x-content-ops-expected-release-sha": SHA, "x-content-ops-mode": "daily" },
    ...overrides,
  });
}
function harness(env = ENV, result: unknown = 0, fail = false) {
  const calls: Array<{ url: string; body: any }> = [];
  const handler = createContentOpsReviewHandler({
    getEnv: (name) => env[name], releaseSha: () => SHA,
    fetcher: async (url, init) => {
      calls.push({ url: String(url), body: JSON.parse(String(init?.body)) });
      if (fail) throw new Error("private-provider-response-must-not-leak");
      return Response.json(result);
    },
  });
  return { handler, calls };
}

test("disabled gate reads no secrets, release, request body or network", async () => {
  const reads: string[] = [];
  const handler = createContentOpsReviewHandler({
    getEnv: (name) => { reads.push(name); return undefined; },
    releaseSha: () => { throw Error("must not read"); },
    fetcher: async () => { throw Error("must not fetch"); },
  });
  assert.equal((await handler(request())).status, 503);
  assert.deepEqual(reads, ["CONTENT_OPS_GATEWAY_ENABLED"]);
});

test("auth, release, origin, context gates prevent all RPCs", async () => {
  for (const env of [
    { ...ENV, CONTENT_OPS_GATEWAY_ENABLED: "TRUE" },
    { ...ENV, CONTENT_OPS_REVIEW_RELEASE_SHA: "b".repeat(40) },
    { ...ENV, CONTEXT: "deploy-preview" },
    { ...ENV, API_SECRET: TOKEN },
  ]) {
    const { handler, calls } = harness(env);
    assert.notEqual((await handler(request())).status, 200);
    assert.equal(calls.length, 0);
  }
  const { handler, calls } = harness();
  assert.equal((await handler(request({}, { headers: {} }))).status, 503);
  const badAuth = request(); badAuth.headers.set("authorization", "Bearer wrong");
  assert.equal((await handler(badAuth)).status, 401);
  const wrongHost = new Request("https://preview.example/.netlify/functions/content-ops-review", request());
  assert.equal((await handler(wrongHost)).status, 421);
  assert.equal(calls.length, 0);
});

test("exact action schema disallows caller workspace, arbitrary destinations and RPCs", async () => {
  const { handler, calls } = harness();
  for (const body of [
    { action: "publish" }, { action: "claim", claim_token: "bad" },
    { action: "reconcile", workspace_id: ID }, { action: "reconcile", destination: "external" },
    { action: "finish", claim_token: ID, outbox_id: ID, outcome: "sent", message_id: null },
    { action: "finish", claim_token: ID, outbox_id: ID, outcome: "delivery_unknown", message_id: 4 },
  ]) assert.equal((await handler(request(body))).status, 400);
  assert.equal((await handler(request({ action: "reconcile", value: "x".repeat(4096) }))).status, 400);
  assert.equal(calls.length, 0);
});

test("dedicated gateway token cannot alias another execution principal", async () => {
  for (const name of [
    "STUDIO_AUTOMATION_TOKEN", "PUBLICATION_WORKER_TOKEN", "CONTENT_KPI_SYNC_TOKEN",
    "GROK_QA_CONNECTOR_TOKEN", "GROK_QA_DISPATCH_TOKEN", "BUZZ_DELIVERY_WORKER_TOKEN",
    "BUZZ_REVIEW_WORKER_TOKEN", "BUZZ_SHADOW_TOKEN", "SUPABASE_CONTENT_QA_KEY",
    "SUPABASE_BUZZ_DELIVERY_KEY", "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", "TYPEFULLY_API_KEY",
  ]) {
    const { handler, calls } = harness({ ...ENV, [name]: TOKEN });
    assert.equal((await handler(request())).status, 503, name);
    assert.equal(calls.length, 0, name);
  }
});

test("reconcile pins server workspace and projects only bounded count", async () => {
  const { handler, calls } = harness(ENV, 4);
  assert.deepEqual(await (await handler(request())).json(), { ok: true, release_sha: SHA, scope: SCOPE, queued: 4 });
  assert.match(calls[0].url, /\/rpc\/content_ops_reconcile_daily$/);
  assert.deepEqual(calls[0].body, { target_workspace_id: ID, target_content_version_id: null });
  assert.equal(calls.length, 1);
});

test("claim returns exact bounded card only", async () => {
  const claim = {
    outbox_id: ID, claim_token: ID, client_id: "yellow", kst_date: "2026-09-06",
    content_item_id: ID, content_version_id: ID, source_item_id: ID, generate_job_id: ID,
    banner_sha256: "a".repeat(64), title: "Review", telegram_copy: "Draft", x_copy: "Draft",
    source_url: "https://x.com/Yellow/status/123", source_published_at: "2026-09-06T00:00:00Z",
  };
  const body = { action: "claim", claim_token: ID };
  const good = harness(ENV, claim);
  assert.deepEqual((await (await good.handler(request(body))).json()).claim, claim);
  for (const bad of [
    { ...claim, provider_response: "must not leak" }, { ...claim, title: "x".repeat(241) },
    { ...claim, source_url: "https://x.com/wrong/status/123" }, { ...claim, client_id: "other" },
    { ...claim, banner_sha256: "bad" }, { ...claim, claim_token: "22222222-2222-4222-8222-222222222222" },
  ]) {
    const response = await harness(ENV, bad).handler(request(body));
    assert.equal(response.status, 503);
    assert.doesNotMatch(await response.text(), /must not leak/);
  }
});

test("begin and finish bind exact claim; internal receipt fields never escape", async () => {
  const { handler, calls } = harness(ENV, { accepted: true, private_data: "do-not-relay" });
  const begin = await handler(request({ action: "begin", claim_token: ID, outbox_id: ID, packet_sha256: "b".repeat(64) }));
  assert.deepEqual(await begin.json(), { ok: true, release_sha: SHA, scope: SCOPE, accepted: true });
  assert.equal(calls[0].body.target_packet_sha256, "b".repeat(64));
  const finish = await handler(request({ action: "finish", claim_token: ID, outbox_id: ID, outcome: "sent", message_id: 123 }));
  assert.equal(finish.status, 200);
  assert.equal(calls[1].body.target_message_id, 123);
});

test("lost RPC acknowledgement is never retried or exposed", async () => {
  const { handler, calls } = harness(ENV, null, true);
  const response = await handler(request());
  assert.equal(response.status, 503);
  assert.doesNotMatch(await response.text(), /private-provider/);
  assert.equal(calls.length, 1);
});

const CANARY_ENV = { ...ENV, CONTENT_OPS_REVIEW_MODE: "canary", CONTENT_OPS_REVIEW_CANARY_VERSION_ID: ID };
function canaryRequest(body: unknown = { action: "reconcile" }) {
  const req = request(body);
  req.headers.set("x-content-ops-mode", "canary");
  req.headers.set("x-content-ops-version-id", ID);
  return req;
}

test("scope must be explicit and coherent before any RPC", async () => {
  for (const env of [
    { ...ENV, CONTENT_OPS_REVIEW_MODE: "" },
    { ...ENV, CONTENT_OPS_REVIEW_MODE: "CANARY" },
    { ...ENV, CONTENT_OPS_REVIEW_MODE: "canary" },
    { ...ENV, CONTENT_OPS_REVIEW_CANARY_VERSION_ID: ID },
    { ...CANARY_ENV, CONTENT_OPS_REVIEW_CANARY_VERSION_ID: "bad" },
    { ...CANARY_ENV, CONTENT_OPS_REVIEW_CANARY_VERSION_ID: "00000000-0000-0000-0000-000000000000" },
    { ...CANARY_ENV, CONTENT_OPS_REVIEW_CANARY_VERSION_ID: "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA" },
  ]) {
    const { handler, calls } = harness(env);
    assert.equal((await handler(request())).status, 503);
    assert.equal(calls.length, 0);
  }
});

test("this gateway supports link cards only, never bundle delivery", async () => {
  for (const env of [ENV, CANARY_ENV]) {
    for (const packetMode of ["squid_bundle_v1", "all_client_bundle_v1", "", "LINK_CARD"]) {
      const { handler, calls } = harness({ ...env, CONTENT_OPS_REVIEW_PACKET_MODE: packetMode });
      const req = env === CANARY_ENV ? canaryRequest() : request();
      assert.equal((await handler(req)).status, 503);
      assert.equal(calls.length, 0);
    }
    const good = harness({ ...env, CONTENT_OPS_REVIEW_PACKET_MODE: "link_card" });
    assert.equal((await good.handler(env === CANARY_ENV ? canaryRequest() : request())).status, 200);
    assert.equal(good.calls.length, 1);
    for (const action of ["bundle_prepare", "bundle_image", "bundle_begin", "bundle_part_begin", "bundle_part_finish", "bundle_status"]) {
      const denied = harness(env);
      const req = env === CANARY_ENV ? canaryRequest({ action }) : request({ action });
      assert.equal((await denied.handler(req)).status, 400);
      assert.equal(denied.calls.length, 0);
    }
    const headerDenied = harness(env);
    const req = env === CANARY_ENV ? canaryRequest() : request();
    req.headers.set("x-content-ops-packet-mode", "squid_bundle_v1");
    assert.equal((await headerDenied.handler(req)).status, 409);
    assert.equal(headerDenied.calls.length, 0);
  }
});

test("button-card gateway is default OFF, canary-only and exact-version fenced", async () => {
  const buttonEnv = { ...CANARY_ENV, CONTENT_OPS_REVIEW_PACKET_MODE: "button_card_v1" };
  for (const env of [buttonEnv,
    { ...buttonEnv, CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "false" },
    { ...buttonEnv, CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "TRUE" },
    { ...buttonEnv, CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "true", CONTENT_OPS_REVIEW_MODE: "daily" },
    { ...buttonEnv, CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "true", CONTENT_OPS_REVIEW_CANARY_VERSION_ID: "" },
  ]) {
    const { handler, calls } = harness(env);
    const req = canaryRequest();
    req.headers.set("x-content-ops-packet-mode", "button_card_v1");
    assert.equal((await handler(req)).status, 503);
    assert.equal(calls.length, 0);
  }
  const active = { ...buttonEnv, CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "true" };
  for (const wrongHeader of [null, "link_card", "squid_bundle_v1", "BUTTON_CARD_V1"]) {
    const { handler, calls } = harness(active);
    const req = canaryRequest();
    if (wrongHeader !== null) req.headers.set("x-content-ops-packet-mode", wrongHeader);
    assert.equal((await handler(req)).status, 409);
    assert.equal(calls.length, 0);
  }
  const { handler, calls } = harness(active, 1);
  const req = canaryRequest();
  req.headers.set("x-content-ops-packet-mode", "button_card_v1");
  assert.deepEqual(await (await handler(req)).json(), {
    ok: true, release_sha: SHA,
    scope: { mode: "canary", content_version_id: ID, packet_mode: "button_card_v1" },
    queued: 1,
  });
  assert.deepEqual(calls[0].body, { target_workspace_id: ID, target_content_version_id: ID });
  const finish = canaryRequest({ action: "finish", claim_token: ID, outbox_id: ID,
    outcome: "sent", message_id: 123 });
  finish.headers.set("x-content-ops-packet-mode", "button_card_v1");
  assert.equal((await handler(finish)).status, 400);
  assert.equal(calls.length, 1);
});

test("button-card gateway exposes claim and one-shot begin but no alternate actions", async () => {
  const env = { ...CANARY_ENV, CONTENT_OPS_REVIEW_PACKET_MODE: "button_card_v1",
    CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "true" };
  const claim = {
    outbox_id: ID, claim_token: ID, client_id: "yellow", kst_date: "2026-09-06",
    content_item_id: ID, content_version_id: ID, source_item_id: ID, generate_job_id: ID,
    banner_sha256: "a".repeat(64), title: "Review", telegram_copy: "Draft", x_copy: "Draft",
    source_url: "https://x.com/Yellow/status/123", source_published_at: "2026-09-06T00:00:00Z",
  };
  const makeRequest = (body: unknown) => {
    const req = canaryRequest(body);
    req.headers.set("x-content-ops-packet-mode", "button_card_v1");
    return req;
  };
  const claimed = harness(env, claim);
  assert.deepEqual((await (await claimed.handler(makeRequest({ action: "claim", claim_token: ID }))).json()).claim, claim);
  assert.equal(claimed.calls.length, 1);
  const begun = harness(env, { accepted: true, private_data: "never expose" });
  assert.deepEqual(await (await begun.handler(makeRequest({ action: "begin", claim_token: ID,
    outbox_id: ID, packet_sha256: "b".repeat(64) }))).json(), {
      ok: true, release_sha: SHA,
      scope: { mode: "canary", content_version_id: ID, packet_mode: "button_card_v1" },
      accepted: true,
    });
  assert.equal(begun.calls.length, 1);
  assert.equal(begun.calls[0].body.target_packet_sha256, "b".repeat(64));
  for (const action of ["prepare", "bind", "approve", "publish", "bundle_image"]) {
    assert.equal((await begun.handler(makeRequest({ action }))).status, 400);
  }
  assert.equal(begun.calls.length, 1);
});

test("button-card image needs an exact claimed locator and returns verified bytes only", async () => {
  const env = { ...CANARY_ENV, CONTENT_OPS_REVIEW_PACKET_MODE: "button_card_v1",
    CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "true" };
  const bytes = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
    1, 2, 3, 4, 5, 6]);
  const hash = createHash("sha256").update(bytes).digest("hex");
  const locator = { status: "ready", outbox_id: ID, client_id: "yellow",
    content_item_id: ID, content_version_id: ID, asset_id: ID,
    bucket: "content-studio", path: `${ID}/yellow/${ID}/news-card.png`,
    sha256: hash, byte_size: bytes.byteLength, execution_authorized: false };
  const body = { action: "image", outbox_id: ID, claim_token: ID };
  const imageRequest = () => {
    const req = canaryRequest(body);
    req.headers.set("x-content-ops-packet-mode", "button_card_v1");
    return req;
  };
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const handler = createContentOpsReviewHandler({
    getEnv: (name) => env[name], releaseSha: () => SHA,
    fetcher: async (url, init) => {
      calls.push({ url: String(url), init: init! });
      return calls.length === 1 ? Response.json(locator)
        : new Response(bytes, { headers: { "Content-Type": "image/png",
            "Content-Length": String(bytes.byteLength) } });
    },
  });
  const response = await handler(imageRequest());
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "image/png");
  assert.equal(response.headers.get("x-content-ops-banner-sha256"), hash);
  assert.equal(response.headers.get("x-content-ops-version-id"), ID);
  assert.equal(response.headers.get("x-content-ops-outbox-id"), ID);
  assert.deepEqual(new Uint8Array(await response.arrayBuffer()), bytes);
  assert.equal(calls.length, 2);
  assert.match(calls[0].url, /\/rpc\/content_ops_button_card_image_locator$/);
  assert.deepEqual(JSON.parse(String(calls[0].init.body)), {
    target_workspace_id: ID, target_outbox_id: ID,
    target_claim_token: ID, target_content_version_id: ID });
  assert.equal(calls[1].url,
    `https://example.supabase.co/storage/v1/object/content-studio/${ID}/yellow/${ID}/news-card.png`);
  assert.equal(calls[1].init.redirect, "error");

  const link = harness(ENV);
  assert.equal((await link.handler(request(body))).status, 400);
  assert.equal(link.calls.length, 0);
});

test("button-card image fails closed on locator or Storage mismatch without leaking paths", async () => {
  const env = { ...CANARY_ENV, CONTENT_OPS_REVIEW_PACKET_MODE: "button_card_v1",
    CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "true" };
  const body = { action: "image", outbox_id: ID, claim_token: ID };
  const bytes = Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1]);
  const locator = { status: "ready", outbox_id: ID, client_id: "yellow",
    content_item_id: ID, content_version_id: ID, asset_id: ID,
    bucket: "content-studio", path: `${ID}/yellow/${ID}/news-card.png`,
    sha256: createHash("sha256").update(bytes).digest("hex"),
    byte_size: bytes.byteLength, execution_authorized: false };
  for (const invalid of [null, { ...locator, path: "other/private.png" },
    { ...locator, content_version_id: "22222222-2222-4222-8222-222222222222" },
    { ...locator, provider_response: "never relay" }]) {
    let calls = 0;
    const handler = createContentOpsReviewHandler({ getEnv: (name) => env[name],
      releaseSha: () => SHA, fetcher: async () => { calls++; return Response.json(invalid); } });
    const req = canaryRequest(body);
    req.headers.set("x-content-ops-packet-mode", "button_card_v1");
    const response = await handler(req);
    assert.equal(response.status, 503);
    assert.equal(calls, 1);
    assert.doesNotMatch(await response.text(), /private\.png|never relay/);
  }
  let calls = 0;
  const handler = createContentOpsReviewHandler({ getEnv: (name) => env[name],
    releaseSha: () => SHA, fetcher: async () => {
      calls++;
      return calls === 1 ? Response.json(locator)
        : new Response(bytes, { headers: { "Content-Type": "image/png",
            "Content-Length": "10000001" } });
    } });
  const req = canaryRequest(body);
  req.headers.set("x-content-ops-packet-mode", "button_card_v1");
  assert.equal((await handler(req)).status, 503);
  assert.equal(calls, 2);
});

test("worker cannot widen scope or proceed after operator scope changes", async () => {
  const canary = harness(CANARY_ENV);
  for (const req of [request(), canaryRequest()]) {
    if (req.headers.get("x-content-ops-mode") === "canary") req.headers.delete("x-content-ops-version-id");
    assert.equal((await canary.handler(req)).status, 409);
  }
  const wrongVersion = canaryRequest();
  wrongVersion.headers.set("x-content-ops-version-id", "22222222-2222-4222-8222-222222222222");
  assert.equal((await canary.handler(wrongVersion)).status, 409);
  assert.equal(canary.calls.length, 0);
  const daily = harness(ENV);
  assert.equal((await daily.handler(canaryRequest())).status, 409);
  const emptyHeader = request(); emptyHeader.headers.set("x-content-ops-version-id", "");
  assert.equal((await daily.handler(emptyHeader)).status, 409);
  assert.equal(daily.calls.length, 0);
});

test("all four canary RPCs receive server-owned version scope, never caller scope", async () => {
  const bodies = [
    { action: "reconcile" }, { action: "claim", claim_token: ID },
    { action: "begin", claim_token: ID, outbox_id: ID, packet_sha256: "a".repeat(64) },
    { action: "finish", claim_token: ID, outbox_id: ID, outcome: "sent", message_id: 5 },
  ];
  for (const [i, body] of bodies.entries()) {
    const { handler, calls } = harness(CANARY_ENV, [1, null, { accepted: true }, { accepted: true }][i]);
    const result = await handler(canaryRequest(body));
    assert.equal(result.status, 200);
    assert.deepEqual((await result.json()).scope, { mode: "canary", content_version_id: ID });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].body.target_content_version_id, ID);
    for (const injected of [{ ...body, target_content_version_id: ID }, { ...body, mode: "daily" }]) {
      assert.equal((await handler(canaryRequest(injected))).status, 400);
    }
    assert.equal(calls.length, 1);
  }
});

test("canary rejects oversized reconcile and cross-version claim receipts", async () => {
  assert.equal((await harness(CANARY_ENV, 2).handler(canaryRequest())).status, 503);
  const claim = {
    outbox_id: ID, claim_token: ID, client_id: "yellow", kst_date: "2026-09-06",
    content_item_id: ID, content_version_id: ID, source_item_id: ID, generate_job_id: ID,
    banner_sha256: "a".repeat(64), title: "Review", telegram_copy: "Draft", x_copy: "Draft",
    source_url: "https://x.com/Yellow/status/123", source_published_at: "2026-09-06T00:00:00Z",
  };
  const body = { action: "claim", claim_token: ID };
  const good = await harness(CANARY_ENV, claim).handler(canaryRequest(body));
  assert.equal(good.status, 200);
  const bad = await harness(CANARY_ENV, {
    ...claim, content_version_id: "22222222-2222-4222-8222-222222222222",
  }).handler(canaryRequest(body));
  assert.equal(bad.status, 503);
  assert.equal((await bad.json()).error, "content_ops_invalid_receipt");
});
