import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import gatewayHandler from "../netlify/functions/content-ops-review.mts";
import { createTrialManifestResolver, createTrialDeliveryConfirmation, TrialManifestNotReady } from "../netlify/functions/_shared/content-ops-trial-resolver.mts";
import { createTrialStateHandler } from "../netlify/functions/_shared/content-ops-trial-state.mts";
import { admitTrial, trialManifestHash } from "../netlify/functions/_shared/content-ops-trial.mts";

const SHA = "a".repeat(40), PLAN = "b".repeat(64), POLICY = "c".repeat(64);
const ID = "d346ceea-4ea9-479f-a5cf-05097b50ce48", VERSION = "22222222-1111-4111-8111-111111111111";
const NOW = Date.parse("2026-10-14T15:25:51Z"), API_ORIGIN = "https://fixture-api.up.railway.app";
const SECRET = "synthetic-only-api-secret-32-characters";
const env: Record<string,string> = { CONTENT_OPS_TRIAL_GATEWAY_ENABLED: "true",
  CONTENT_OPS_TRIAL_NOT_BEFORE: "2026-10-14T15:10:51Z", CONTENT_OPS_TRIAL_DEADLINE: "2026-10-14T17:10:51Z",
  CONTENT_OPS_GATEWAY_ENABLED: "false", CONTENT_OPS_REVIEW_ENABLED: "false",
  CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED: "false", CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED: "false",
  CONTENT_OPS_REVIEW_RELEASE_SHA: SHA, CONTENT_OPS_TRIAL_ID: ID,
  CONTENT_OPS_TRIAL_PLAN_SHA256: PLAN, CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256: POLICY,
  RAILWAY_API_URL: API_ORIGIN, API_SECRET: SECRET };
const identity = { trial_id: ID, plan_sha256: PLAN };
const manifest = { contract: "ybo-trial-courier-manifest@1", trial_id: ID, plan_sha256: PLAN,
  release_sha: SHA, not_before: "2026-10-14T15:10:51Z", deadline: "2026-10-14T17:10:51Z",
  destination_policy_sha256: POLICY, entries: [{ client_id: "babylon",
    job_id: "33333333-1111-4111-8111-111111111111", request_id: "44444444-1111-4111-8111-111111111111",
    source_item_id: "55555555-1111-4111-8111-111111111111", source_sha256: "d".repeat(64),
    content_item_id: "44444444-1111-4111-8111-111111111111", content_version_id: VERSION,
    banner_sha256: "e".repeat(64), telegram_copy_sha256: "f".repeat(64), x_copy_sha256: "0".repeat(64) }] };
const HASH = trialManifestHash(manifest);
const value = { active: true, observed_at: new Date(NOW).toISOString(), manifest, manifest_sha256: HASH };
const ERROR = { message: "content_ops_trial_resolver_unavailable" };
function setup(opts: any = {}) {
  const current = { ...env, ...opts.env }, calls: any[] = [], reads: string[] = [];
  const resolver = createTrialManifestResolver({
    getEnv: key => { reads.push(key); return current[key]; }, releaseSha: () => opts.release ?? SHA,
    clock: opts.clock ?? (() => NOW), fetcher: async (url, init) => {
      calls.push({ url, init });
      if (opts.throw) throw Error(`private-url/${SECRET}/private-provider-body`);
      opts.afterFetch?.(current);
      return opts.response?.() ?? Response.json(value);
    } });
  return { current, calls, reads, resolver };
}
test("resolver is default OFF, reads only flag, and never constructs HTTP or reads secrets", () => {
  for (const flag of [undefined, "false", "TRUE", ""]) {
    const h = setup({ env: { CONTENT_OPS_TRIAL_GATEWAY_ENABLED: flag } });
    assert.equal(h.resolver, undefined); assert.deepEqual(h.reads, ["CONTENT_OPS_TRIAL_GATEWAY_ENABLED"]);
    assert.equal(h.calls.length, 0);
  }
});
test("ON construction remains pure; exact existing API credentials/body/path and no retry", async () => {
  const h = setup(); assert.deepEqual(h.reads, ["CONTENT_OPS_TRIAL_GATEWAY_ENABLED"]);
  assert.equal(h.calls.length, 0); assert.deepEqual(await h.resolver!(identity), value);
  assert.equal(h.calls.length, 1);
  const { url, init } = h.calls[0], headers = new Headers(init.headers);
  assert.equal(url, API_ORIGIN + "/internal/trial/manifest"); assert.equal(init.method, "POST");
  assert.equal(init.redirect, "error"); assert.equal(init.cache, "no-store"); assert.ok(init.signal instanceof AbortSignal);
  assert.equal(headers.get("x-api-key"), SECRET); assert.equal(headers.get("authorization"), null);
  assert.deepEqual(JSON.parse(init.body), identity); assert.doesNotMatch(init.body, /secret|manifest|http/i);
});
test("resolver integrates with admission freshness/canonical manifest and resolves again", async () => {
  const h = setup(); const req = new Request("https://example.invalid", { headers: {
    "x-content-ops-mode": "trial", "x-content-ops-packet-mode": "trial_delivery_only_v1",
    "x-content-ops-version-id": VERSION, "x-content-ops-trial-id": ID,
    "x-content-ops-trial-plan-sha256": PLAN, "x-content-ops-trial-manifest-sha256": HASH } });
  const admitted = await admitTrial(req, k => env[k], SHA, () => NOW, h.resolver);
  assert.equal(admitted.entry.content_version_id, VERSION); assert.equal(h.calls.length, 1);
  await admitted.assertActive(); assert.equal(h.calls.length, 2);
});

test("anchored window includes exact start but excludes early time and exact deadline", async () => {
  const start = Date.parse("2026-10-14T15:10:51Z"), end = Date.parse("2026-10-14T17:10:51Z");
  assert.equal(end - start, 2 * 3600_000);
  const fresh = {...value, observed_at: new Date(start).toISOString()};
  const accepted = setup({clock: () => start, response: () => Response.json(fresh)});
  assert.deepEqual(await accepted.resolver!(identity), fresh);
  for (const time of [Date.parse("2026-10-14T00:15:00Z"), Date.parse("2026-10-14T06:37:17Z"), start - 1, end, end + 1]) {
    const closed = setup({clock: () => time});
    await assert.rejects(closed.resolver!(identity), ERROR);
    assert.equal(closed.calls.length, 0);
  }
});
for (const [key, changed] of [
  ["CONTENT_OPS_GATEWAY_ENABLED", "true"], ["CONTENT_OPS_REVIEW_ENABLED", "true"],
  ["CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED", "true"], ["CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED", "true"],
  ["CONTENT_OPS_REVIEW_RELEASE_SHA", "b".repeat(40)], ["CONTENT_OPS_TRIAL_ID", "invalid"],
  ["CONTENT_OPS_TRIAL_PLAN_SHA256", "x"], ["CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256", "x"],
  ["API_SECRET", "short"], ["API_SECRET", `bad\n${SECRET}`],
]) {
  test(`invalid ${key} blocks before fetch`, async () => {
    const h = setup({ env: { [key]: changed } }); await assert.rejects(h.resolver!(identity), ERROR);
    assert.equal(h.calls.length, 0);
  });
}
for (const origin of [undefined, "http://api.up.railway.app", "https://user:pass@api.up.railway.app",
  "https://api.up.railway.app/path", "https://api.up.railway.app/?q=x", "https://api.up.railway.app/#fragment",
  "https://api.up.railway.app:444", "https://127.0.0.1", "https://localhost", "https://host.internal",
  "https://[::1]", " https://api.up.railway.app"]) {
  test(`invalid API origin ${origin} blocks before fetch`, async () => {
    const h = setup({ env: { RAILWAY_API_URL: origin } }); await assert.rejects(h.resolver!(identity), ERROR);
    assert.equal(h.calls.length, 0);
  });
}
test("caller cannot select origin, credential, manifest or other trial", async () => {
  for (const delta of [{ trial_id: VERSION }, { plan_sha256: "d".repeat(64) }, { url: API_ORIGIN }, { manifest }]) {
    const h = setup(); await assert.rejects(h.resolver!({ ...identity, ...delta } as any), ERROR);
    assert.equal(h.calls.length, 0);
  }
});
test("window gate rejects early/expired and config is rechecked after HTTP", async () => {
  for (const time of [NOW - 3600_000, Date.parse("2026-10-14T17:10:51Z")]) {
    const h = setup({ clock: () => time }); await assert.rejects(h.resolver!(identity), ERROR); assert.equal(h.calls.length,0);
  }
  const h = setup({ afterFetch: e => { e.CONTENT_OPS_TRIAL_GATEWAY_ENABLED = "false"; } });
  await assert.rejects(h.resolver!(identity), ERROR); assert.equal(h.calls.length, 1);
});
test("immutable configured origin/credential/release/plan cannot drift between resolutions", async () => {
  for (const [key,v] of [["RAILWAY_API_URL", "https://other-api.up.railway.app"], ["API_SECRET", SECRET+"new"],
    ["CONTENT_OPS_TRIAL_PLAN_SHA256", "d".repeat(64)], ["CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256", "d".repeat(64)]]) {
    const h = setup(); await h.resolver!(identity); h.current[key] = v;
    await assert.rejects(h.resolver!(identity), ERROR); assert.equal(h.calls.length, 1);
  }
});
test("resolver rejects malformed or replaced anchor and pins full valid window between requests", async () => {
  for (const changed of [{ CONTENT_OPS_TRIAL_NOT_BEFORE: undefined }, { CONTENT_OPS_TRIAL_DEADLINE: undefined },
    { CONTENT_OPS_TRIAL_NOT_BEFORE: "2026-10-14T15:10:51.000Z" },
    { CONTENT_OPS_TRIAL_DEADLINE: "2026-10-14T17:10:52Z" },
    { CONTENT_OPS_TRIAL_NOT_BEFORE: "2026-10-14T14:10:51Z", CONTENT_OPS_TRIAL_DEADLINE: "2026-10-14T16:10:51Z" },
    { CONTENT_OPS_TRIAL_ID: VERSION }]) {
    const h = setup({ env: changed });
    await assert.rejects(h.resolver!({ ...identity, ...(changed.CONTENT_OPS_TRIAL_ID ? { trial_id: VERSION } : {}) }), ERROR);
    assert.equal(h.calls.length, 0);
  }
  const h = setup(); await h.resolver!(identity);
  Object.assign(h.current, { CONTENT_OPS_TRIAL_NOT_BEFORE: "2026-10-14T15:11:51Z", CONTENT_OPS_TRIAL_DEADLINE: "2026-10-14T17:11:51Z" });
  await assert.rejects(h.resolver!(identity), ERROR); assert.equal(h.calls.length, 1);
});
for (const response of [
  () => Response.json(value, { status: 201 }), () => Response.json({ error: SECRET }, { status: 401 }),
  () => new Response(null, { status: 302, headers: { location: "https://other.invalid" } }),
  () => new Response("not-json-provider-body", { headers: { "Content-Type": "text/plain" } }),
  () => new Response("malformed", { headers: { "Content-Type": "application/json" } }),
  () => Response.json(value, { headers: { "Content-Encoding": "gzip" } }),
  () => Response.json(value, { headers: { "Content-Range": "bytes 0-1/20" } }),
  () => Response.json(value, { headers: { "Content-Length": "20000" } }),
  () => Response.json(value, { headers: { "Content-Length": "2" } }),
  () => Response.json({ ...value, raw_provider_response: SECRET }),
  () => Response.json({ ...value, active: "true" }),
  () => Response.json({ ...value, manifest_sha256: "x" }),
  () => new Response(" ".repeat(16_385), { headers: { "Content-Type": "application/json" } }),
]) {
  test("invalid or unbounded upstream response is fixed-error once", async () => {
    const h = setup({ response }); await assert.rejects(h.resolver!(identity), ERROR); assert.equal(h.calls.length, 1);
  });
}
test("lost acknowledgement/timeout does not retry or expose credential/provider exception", async () => {
  const h = setup({ throw: true }); await assert.rejects(h.resolver!(identity), ERROR); assert.equal(h.calls.length, 1);
});
test("production composition keeps import and disabled request network-free", async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "Netlify"), oldFetch = globalThis.fetch;
  const reads: string[] = []; let calls = 0;
  Object.defineProperty(globalThis, "Netlify", { configurable: true, value: { env: { get: (k: string) => {
    reads.push(k); return "false"; } } } });
  globalThis.fetch = async () => { calls++; throw Error("no network permitted"); };
  try {
    const request = new Request("https://coineasy-newscard.netlify.app/.netlify/functions/content-ops-review", {
      method: "POST", headers: { "x-content-ops-mode": "trial" } });
    assert.equal((await gatewayHandler(request)).status, 503); assert.equal(calls, 0);
    assert.deepEqual(reads, ["CONTENT_OPS_TRIAL_GATEWAY_ENABLED", "CONTENT_OPS_TRIAL_GATEWAY_ENABLED"]);
    const source = readFileSync(new URL("../netlify/functions/content-ops-review.mts", import.meta.url), "utf8");
    assert.match(source, /createTrialManifestResolver\(\{ getEnv, releaseSha \}\)/);
    assert.match(source, /createContentOpsReviewHandler\(\{ getEnv, releaseSha, trialResolver, trialDeliveryConfirm \}\)/);
  } finally {
    globalThis.fetch = oldFetch;
    if (previous) Object.defineProperty(globalThis, "Netlify", previous); else delete (globalThis as any).Netlify;
  }
});
test("only exact API409 not-ready is distinct from unknown and never retried by resolver", async () => {
  const h = setup({ response: () => Response.json({ error: "trial_manifest_not_ready" }, { status: 409 }) });
  await assert.rejects(h.resolver!(identity), TrialManifestNotReady); assert.equal(h.calls.length, 1);
  const bad = setup({ response: () => Response.json({ error: "expired" }, { status: 409 }) });
  await assert.rejects(bad.resolver!(identity), ERROR);
});
test("delivery confirmation is bounded close-only exact receipt, not authorization", async () => {
  const calls: any[] = [];
  const confirm = createTrialDeliveryConfirmation({ getEnv: k => env[k], releaseSha: () => SHA, clock: () => NOW,
    fetcher: async (url, init) => { calls.push({url,init}); return Response.json({recorded:true}); } });
  const receipt = { ...identity, manifest_sha256: HASH, content_version_id: VERSION,
    card_id: "66666666-1111-4111-8111-111111111111", outbox_id: "77777777-1111-4111-8111-111111111111",
    confirmed_parts: 4 as const, status: "sent" as const };
  await confirm!(receipt); assert.equal(calls.length,1);
  assert.equal(calls[0].url, API_ORIGIN+"/internal/trial/delivery-confirm");
  assert.deepEqual(JSON.parse(calls[0].init.body),receipt);
  for (const delta of [{confirmed_parts:3}, {status:"unknown"}, {claim_token:ID}]) {
    await assert.rejects(confirm!({...receipt,...delta} as any),ERROR);
  }
  assert.equal(calls.length,1);
});
test("state endpoint authenticates native production, exact release and body before resolver; no database", async () => {
  const token = "fixture-dedicated-gateway-token-32-characters";
  let calls = 0;
  const local = {...env,CONTENT_OPS_GATEWAY_TOKEN:token};
  const handler = createTrialStateHandler({getEnv:k=>local[k],releaseSha:()=>SHA,clock:()=>NOW,
    resolver:async input=>{ calls++; assert.deepEqual(input,identity); return value; }});
  const native = {deploy:{context:"production",id:"fixture-published",published:true}};
  const request = (body: unknown = identity) => new Request("https://coineasy-newscard.netlify.app/.netlify/functions/content-ops-trial-state",{
    method:"POST",body:JSON.stringify(body),headers:{"Content-Type":"application/json",Authorization:`Bearer ${token}`,
      "x-content-ops-expected-release-sha":SHA}});
  const r = await handler(request(),native);
  assert.equal(r.status,200); assert.deepEqual(await r.json(),value); assert.equal(calls,1);
  assert.equal(r.headers.get("x-content-ops-release-sha"),SHA);
  assert.equal((await handler(request())).status,421);
  const badAuth=request();badAuth.headers.set("Authorization","Bearer wrong");
  assert.equal((await handler(badAuth,native)).status,401);
  assert.equal((await handler(request({...identity,manifest}),native)).status,503);
  assert.equal(calls,1);
});
test("state endpoint returns409 only for typed not-ready, terminal/stale state closes", async () => {
  const token="fixture-dedicated-gateway-token-32-characters",local={...env,CONTENT_OPS_GATEWAY_TOKEN:token};
  const req=()=>new Request("https://coineasy-newscard.netlify.app/.netlify/functions/content-ops-trial-state",{
    method:"POST",body:JSON.stringify(identity),headers:{"Content-Type":"application/json",Authorization:`Bearer ${token}`,
      "x-content-ops-expected-release-sha":SHA}});
  const native={deploy:{context:"production",id:"fixture-published",published:true}};
  for (const [resolve,expected] of [
    [async()=>{throw new TrialManifestNotReady();},409],
    [async()=>{throw Error("provider-secret");},503],
    [async()=>({...value,active:false}),503],
    [async()=>({...value,observed_at:"2026-10-14T15:24:51Z"}),503],
  ] as const) {
    const handler=createTrialStateHandler({getEnv:k=>local[k],releaseSha:()=>SHA,clock:()=>NOW,resolver:resolve});
    const r=await handler(req(),native);assert.equal(r.status,expected);assert.doesNotMatch(await r.text(),/provider-secret/);
  }
});
test("state defaultOFF and expired paths do not resolve or read a caller manifest", async () => {
  const handler=createTrialStateHandler({getEnv:()=>"false",releaseSha:()=>{throw Error("release forbidden");},
    resolver:async()=>{throw Error("resolver forbidden");}});
  const r=await handler(new Request("https://coineasy-newscard.netlify.app/.netlify/functions/content-ops-trial-state",{method:"POST"}));
  assert.equal(r.status,503);
});
