import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import sharp from "sharp";
import { admitTrialGeneration, TrialGenerationError } from "../netlify/functions/_shared/content-ops-trial-generation.mts";
import { createNewsCardHandler } from "../netlify/functions/news-card.mts";
import { QUALITY_BANNER_POLICY } from "../netlify/functions/_shared/quality-banner.mts";

const id = (n: number) => `${String(n).padStart(8,"0")}-1111-4111-8111-111111111111`;
const NOW = Date.parse("2026-10-14T00:20:00Z"), RELEASE = "a".repeat(40), PLAN = "b".repeat(64);
const API = "https://coineasy-content-engine-production.up.railway.app";
const URL = "https://coineasy-newscard.netlify.app/api/news-card/yellow";
const TEXT = "Yellow explains infrastructure execution with its official source creative.";
const SOURCE = "https://x.com/Yellow/status/1234567890";
const IMAGE = "https://pbs.twimg.com/media/official-source.jpg?format=jpg&name=orig";
const sha = (v: string | Buffer) => createHash("sha256").update(v).digest("hex");
const quality = { job_id: id(1), request_id: id(2), workspace_id: id(3), client_id: "yellow",
  source_item_id: id(4), source_published_at: "2026-10-13T23:00:00Z", source_sha256: sha(TEXT),
  source_content: TEXT, source_url: SOURCE, source_image_url: IMAGE };
const payload = { source_content: TEXT, source_type: "tweet", source_url: SOURCE, source_image_url: IMAGE,
  mock_mode: false, template_style: "remix", style_references: [], style_reference_pack_hash: "",
  brand_review_guidance: null, quality_context: quality };
const body = { source_content: TEXT, source_type: "tweet", source_url: SOURCE, source_image_url: IMAGE,
  mock_mode: false, template_style: "remix", trial_payload: payload };
const ENV: Record<string,string> = {
  API_SECRET: "fixture-admin-secret", RAILWAY_API_URL: API,
  STUDIO_AUTOMATION_TOKEN: "fixture-automation-token-at-least-32-chars",
  SUPABASE_URL: "https://fixture.supabase.co", SUPABASE_SERVICE_ROLE_KEY: "fixture-service-key",
  CONTENT_STUDIO_WORKSPACE_ID: id(3), QUALITY_BANNER_ENABLED: "false", NEWS_CARD_IDEMPOTENCY_ENABLED: "false",
  YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "true", YBO_TRIAL_ID: id(5), YBO_TRIAL_PLAN_SHA256: PLAN,
  YBO_TRIAL_RELEASE_SHA: RELEASE,
};
const runtime = { params: { clientId: "yellow" }, deploy: { context: "production", published: true, id: "fixture-deploy" } };
function request(b: any = body, headerChanges: Record<string,string> = {}, url = URL) {
  return new Request(url, { method: "POST", headers: { "Content-Type": "application/json",
    "X-Studio-Automation-Key": ENV.STUDIO_AUTOMATION_TOKEN, "Idempotency-Key": id(2),
    "X-Studio-Expected-Release-Sha": RELEASE, "X-YBO-Trial-ID": id(5),
    "X-YBO-Trial-Plan-Sha256": PLAN, ...headerChanges }, body: JSON.stringify(b) });
}
function receipt(changes: any = {}) {
  return { authorized: true, trial_id: id(5), plan_sha256: PLAN, release_sha: RELEASE,
    client_id: "yellow", request_id: id(2), observed_at: new Date(NOW).toISOString(), ...changes };
}
async function gate(options: any = {}) {
  const calls: any[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    calls.push([String(input), init]);
    return options.response?.(calls.length) ?? Response.json(receipt());
  };
  const args = { req: request(), runtime, body, clientId: "yellow", requestId: id(2), automation: true,
    env: (k: string) => ({ ...ENV, ...options.env })[k], release: RELEASE, clock: () => NOW,
    fetcher, ...options.args };
  return { admit: () => admitTrialGeneration(args), calls };
}

test("trial sends only the exact immutable payload to the fixed authenticated API before other HTTP", async () => {
  const h = await gate(), admission = await h.admit();
  assert.equal(h.calls.length, 1);
  const [url, init] = h.calls[0];
  assert.equal(url, `${API}/internal/trial/generation-authorize`);
  assert.equal(init.redirect, "error"); assert.equal(new Headers(init.headers).get("X-API-Key"), ENV.API_SECRET);
  assert.deepEqual(JSON.parse(init.body), { trial_id: id(5), plan_sha256: PLAN, release_sha: RELEASE,
    client_id: "yellow", request_id: id(2), payload });
  assert.deepEqual(admission.payload, payload);
  assert.equal(admission.collectOnly, false);
  await admission.fetcher("https://fixture.supabase.co/rest/v1/rpc/read", { method: "POST" });
  assert.equal(h.calls.length, 3); assert.equal(h.calls[1][0], `${API}/internal/trial/generation-authorize`);
  assert.equal(h.calls[2][0], "https://fixture.supabase.co/rest/v1/rpc/read");
  assert.equal(h.calls[2][1].redirect, "error");
});

for (const env of [{ YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "false" }, { QUALITY_BANNER_ENABLED: "true" },
  { NEWS_CARD_IDEMPOTENCY_ENABLED: "true" }, { YBO_TRIAL_PLAN_SHA256: "c".repeat(64) },
  { YBO_TRIAL_RELEASE_SHA: "c".repeat(40) }, { YBO_TRIAL_ID: id(6) },
  { RAILWAY_API_URL: "https://attacker.invalid" }, { API_SECRET: "" }]) {
  test(`invalid config ${Object.keys(env)[0]} denies before any HTTP`, async () => {
    const h = await gate({ env }); await assert.rejects(h.admit, TrialGenerationError); assert.equal(h.calls.length, 0);
  });
}
for (const args of [{ automation: false }, { clientId: "squid" }, { runtime: {} },
  { runtime: { deploy: { ...runtime.deploy, published: false } } }, { release: null },
  { req: request(body, { "X-YBO-Trial-ID": `${id(5)},${id(5)}` }) },
  { req: request(body, { "X-YBO-Trial-Collect-Only": "false" }) },
  { req: request(body, { "X-YBO-Trial-Collect-Only": "true,true" }) },
  { req: request(body, { "X-YBO-Trial-Collect-Only": "TRUE" }) },
  { req: request(body, {}, URL.replace("coineasy-newscard", "preview")) },
  { clock: () => Date.parse("2026-10-13T23:59:59Z") }, { clock: () => Date.parse("2026-10-14T02:00:00Z") },
  { body: { ...body, source_content: TEXT + "changed" } },
  { body: { ...body, trial_payload: { ...payload, brand_review_guidance: {} } } },
  { body: { ...body, trial_payload: { ...payload, extra: true } } }]) {
  test(`untrusted scope/payload ${JSON.stringify(Object.keys(args))} gives zero HTTP`, async () => {
    const h = await gate({ args }); await assert.rejects(h.admit, TrialGenerationError); assert.equal(h.calls.length, 0);
  });
}
for (const changes of [{ authorized: false }, { request_id: id(9) }, { client_id: "babylon" },
  { plan_sha256: "c".repeat(64) }, { release_sha: "c".repeat(40) }, { extra: true },
  { observed_at: "2026-10-14T00:19:54Z" }, { observed_at: "2026-10-14T00:20:01Z" }]) {
  test(`invalid trusted receipt ${Object.keys(changes)[0]} never reaches downstream`, async () => {
    const h = await gate({ response: () => Response.json(receipt(changes)) });
    await assert.rejects(h.admit, TrialGenerationError); assert.equal(h.calls.length, 1);
  });
}
test("owner closing after initial authorization denies a subsequent write without retry", async () => {
  const h = await gate({ response: (n: number) => Response.json(receipt({ authorized: n === 1 })) });
  const admission = await h.admit();
  await assert.rejects(() => admission.fetcher(`${API}/clients/yellow/generate/news-card`, { method: "POST" }), TrialGenerationError);
  assert.equal(h.calls.length, 2); assert.ok(h.calls.every(([url]) => url.endsWith("/generation-authorize")));
});
test("ambiguous/redirect/oversize authorization is not retried and never reflected", async () => {
  for (const response of [() => { throw Error("secret-provider-body"); },
    () => new Response("secret-provider-body", { status: 302, headers: { Location: "https://attacker.invalid" } }),
    () => new Response("x".repeat(4097), { headers: { "content-type": "application/json" } })]) {
    const h = await gate({ response }); await assert.rejects(h.admit, /^Error: trial_generation_blocked$/);
    assert.equal(h.calls.length, 1);
  }
});

const png = await sharp({ create: { width: 2880, height: 2880, channels: 3, background: "#ffee00" } }).png().toBuffer();
async function integration(options: any = {}) {
  const oldFetch = globalThis.fetch, oldNow = Date.now;
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const calls: string[] = []; let paid = 0, stored = 0, auth = 0, downstream = 0, clock = NOW;
  const env = { ...ENV, ...options.env };
  Object.defineProperty(globalThis, "Netlify", { configurable: true, value: { env: { get: (k: string) => env[k] } } });
  Date.now = () => clock;
  globalThis.fetch = async (input, init) => {
    const url = String(input); calls.push(url);
    if (url.endsWith("/generation-authorize")) {
      auth++; assert.deepEqual(JSON.parse(String(init?.body)).payload, payload);
      return Response.json(receipt({ authorized: !options.denied && !(options.denyAfterGeneration && paid > 0)
        && !(options.denyAfterUpload && stored > 0) }));
    }
    // Initial authorization plus one per HTTP. Read-only bucket/scope probes
    // run concurrently, so valid authorization receipts can interleave.
    downstream++; assert.ok(auth > downstream);
    if (url.endsWith("/storage/v1/bucket/content-studio")) return Response.json({ id: "content-studio", public: false });
    if (url.includes("/rest/v1/workspace_clients")) return Response.json([{ workspace_id: id(3), client_id: "yellow", active: true }]);
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/rest/v1/rpc/get_quality_banner_generation_context")) return Response.json(
      options.contextDrift ? { ...quality, job_id: id(9) } : quality);
    if (url.includes("cdn.syndication.twimg.com")) return Response.json({ id_str: "1234567890", text: TEXT,
      user: { screen_name: "Yellow", id_str: "2651" }, photos: [{ url: IMAGE }] });
    if (url.endsWith("/clients/yellow/generate/news-card")) {
      paid++; const headers = new Headers(init?.headers);
      assert.equal(headers.get("X-YBO-Trial-ID"), id(5)); assert.equal(headers.get("Idempotency-Key"), id(2));
      assert.equal(headers.get("X-YBO-Trial-Collect-Only"), options.collectOnly ? "true" : null);
      assert.deepEqual(JSON.parse(String(init?.body)), payload);
      if (options.expireAfterGeneration) clock = Date.parse("2026-10-14T02:00:00Z");
      return Response.json({ client_id: "yellow", content_type: "news_card",
        spec: { headline: "공식 인프라 업데이트", body_lines: ["공식 소식의 핵심 내용을 확인하세요"], source_url: SOURCE,
          render_strategy: "reference_first_quality", channel_profile: "x_square", template_version: QUALITY_BANNER_POLICY,
          asset_pack_version: "official-reference-source-logo@1", brand_profile_policy_version: "official-reference-quality@1",
          quality_banner: { policy: QUALITY_BANNER_POLICY, request_sha256: "a".repeat(64), png_sha256: sha(png), width: 2880, height: 2880 } },
        png_path: `/app/output/yellow/news_request_${id(2)}/news_card_quality.png`, template_style: "remix",
        requested_template_style: "remix", source_image_used: true, source_image_url: IMAGE, source_image_sha256: "c".repeat(64),
        source_visual_path: null, figma_template: null, manifest_path: "/app/output/manifest.json", duration_ms: 1 });
    }
    if (url.includes("/files/yellow/")) return new Response(png);
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") { stored++; return Response.json({ Key: "stored" }); }
    if (url.endsWith("/rest/v1/rpc/record_generated_content")) {
      const data = JSON.parse(String(init?.body));
      assert.deepEqual(data.target_content.quality_context, quality);
      assert.equal(data.target_generation_meta.brand_review_guidance_available, false);
      return Response.json({ content_item_id: id(2), content_version_id: id(7), asset_ids: [data.target_asset.asset_id] });
    }
    throw Error("unexpected fixture HTTP");
  };
  try {
    const response = await createNewsCardHandler(() => RELEASE)(options.request ?? request(), runtime as never);
    return { status: response.status, body: await response.json(), paid, stored, auth, calls };
  } finally {
    globalThis.fetch = oldFetch; Date.now = oldNow;
    if (descriptor) Object.defineProperty(globalThis, "Netlify", descriptor); else Reflect.deleteProperty(globalThis, "Netlify");
  }
}
test("actual route with globals OFF authorizes exact payload and persists one immutable quality result", async () => {
  const r = await integration(); assert.equal(r.status, 200, JSON.stringify(r.body));
  assert.equal(r.paid, 1); assert.equal(r.stored, 1); assert.equal(r.body.content_item_id, id(2));
  assert.equal(r.body.image_data_url, ""); assert.ok(r.auth > 8);
  assert.ok(!r.calls.some(url => /brand_review|telegram|typefully/.test(url)));
});
test("collect-only is forwarded once for a trusted trial without changing the pinned payload", async () => {
  const r = await integration({ collectOnly: true, request: request(body, { "X-YBO-Trial-Collect-Only": "true" }) });
  assert.equal(r.status, 200, JSON.stringify(r.body)); assert.equal(r.paid, 1);
  assert.equal(r.calls.filter(url => url.endsWith("/clients/yellow/generate/news-card")).length, 1);
});
test("collect-only with no full trial authority is denied before downstream HTTP", async () => {
  const req = request(body, { "X-YBO-Trial-Collect-Only": "true" });
  req.headers.delete("X-YBO-Trial-ID"); req.headers.delete("X-YBO-Trial-Plan-Sha256");
  const untagged = new Request(req.url, { method: "POST", headers: req.headers,
    body: JSON.stringify({ ...body, trial_payload: undefined }) });
  const r = await integration({ request: untagged, env: { YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "false" } });
  assert.equal(r.status, 503); assert.equal(r.calls.length, 0);
});
test("actual route rejects invalid authority or current DB context before paid generation", async () => {
  for (const options of [{ denied: true }, { contextDrift: true }, { env: { YBO_TRIAL_GENERATION_GATEWAY_ENABLED: "false" } }]) {
    const r = await integration(options); assert.ok(r.status >= 400); assert.equal(r.paid, 0); assert.equal(r.stored, 0);
  }
});
test("revoked/expired authority after generation prevents file download and all catalog/storage writes", async () => {
  for (const options of [{ denyAfterGeneration: true }, { expireAfterGeneration: true }]) {
    const r = await integration(options); assert.ok(r.status >= 400); assert.equal(r.paid, 1); assert.equal(r.stored, 0);
    assert.ok(!r.calls.some(url => url.includes("/files/") || url.includes("/storage/v1/object/")));
  }
});
test("authority closing after PNG upload blocks catalog RPC without compensating DELETE or retry", async () => {
  const r = await integration({ denyAfterUpload: true });
  assert.ok(r.status >= 400); assert.equal(r.paid, 1); assert.equal(r.stored, 1);
  assert.equal(r.calls.filter(url => url.includes("/storage/v1/object/")).length, 1);
  assert.ok(!r.calls.some(url => url.endsWith("/record_generated_content")));
});
test("untagged YBO cannot fall back to ordinary generation while trial gateway is mounted", async () => {
  const req = request(); req.headers.delete("X-YBO-Trial-ID"); req.headers.delete("X-YBO-Trial-Plan-Sha256");
  const untagged = new Request(req.url, { method: "POST", headers: req.headers,
    body: JSON.stringify({ ...body, trial_payload: undefined }) });
  const r = await integration({ request: untagged }); assert.equal(r.status, 503); assert.equal(r.calls.length, 0);
});
