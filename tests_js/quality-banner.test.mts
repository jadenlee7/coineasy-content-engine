import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import sharp from "sharp";
import handler, { newsCardRequestHash, normalizedSourceVisualFile } from "../netlify/functions/news-card.mts";
import { QUALITY_BANNER_POLICY, getQualityGenerationContext, parseQualityGenerationContext,
  qualityBannerEnabled, qualityContextHash, validQualityBannerMetadata, validQualityBannerPng,
  validQualityBannerPixels, qualityUpstreamError } from "../netlify/functions/_shared/quality-banner.mts";
import { createStudioSessionValue, STUDIO_SESSION_COOKIE } from "../netlify/functions/_shared/studio-session.mts";

const W = "11111111-1111-4111-8111-111111111111";
const R = "22222222-2222-4222-8222-222222222222";
const V = "33333333-3333-4333-8333-333333333333";
const S = "44444444-4444-4444-8444-444444444444";
const J = "55555555-5555-4555-8555-555555555555";
const A = "66666666-6666-4666-8666-666666666666";
const TEXT = "Yellow explains infrastructure execution with its official source creative.";
const URL = "https://x.com/Yellow/status/1234567890";
const IMAGE = "https://pbs.twimg.com/media/official-source.jpg?format=jpg&name=orig";
const sha = (s: string | Buffer) => createHash("sha256").update(s).digest("hex");
const context = { job_id: J, request_id: R, workspace_id: W, client_id: "yellow",
  source_item_id: S, source_published_at: "2026-10-12T00:00:00Z", source_sha256: sha(TEXT),
  source_content: TEXT, source_url: URL, source_image_url: IMAGE };
const expected = { workspaceId: W, clientId: "yellow", requestId: R,
  sourceContent: TEXT, sourceUrl: URL, sourceImageUrl: IMAGE };
const requestBody = { source_content: TEXT, source_type: "tweet", source_url: URL,
  source_image_url: IMAGE, template_style: "remix", mock_mode: false };
const pngBuffer = await sharp({ create: { width: 2880, height: 2880, channels: 3,
  background: "#ffee00" } }).png().toBuffer();
const png = pngBuffer.buffer.slice(pngBuffer.byteOffset, pngBuffer.byteOffset + pngBuffer.byteLength) as ArrayBuffer;
function qualitySpec() {
  return { headline: "공식 인프라 업데이트", body_lines: ["공식 소식의 핵심 내용을 확인하세요"],
    source_url: URL, render_strategy: "reference_first_quality", channel_profile: "x_square",
    template_version: QUALITY_BANNER_POLICY, asset_pack_version: "official-reference-source-logo@1",
    brand_profile_policy_version: "official-reference-quality@1",
    quality_banner: { policy: QUALITY_BANNER_POLICY, request_sha256: "a".repeat(64),
      png_sha256: sha(pngBuffer), width: 2880, height: 2880 } };
}

test("quality configuration is explicit and context validates every natural identity", () => {
  assert.equal(qualityBannerEnabled(undefined), false);
  assert.equal(qualityBannerEnabled("false"), false);
  assert.equal(qualityBannerEnabled("true"), true);
  assert.throws(() => qualityBannerEnabled("1"));
  assert.deepEqual(parseQualityGenerationContext(context, expected), context);
  for (const change of [{ workspace_id: S }, { client_id: "babylon" }, { request_id: J },
    { job_id: "invalid" }, { source_item_id: "invalid" }, { source_content: TEXT + "x" },
    { source_sha256: "b".repeat(64) }, { source_url: URL.replace("Yellow", "notYellow") },
    { source_image_url: "https://private.invalid/image" }, { source_published_at: "2026-10-12" },
    { admin: true }]) assert.throws(() => parseQualityGenerationContext({ ...context, ...change }, expected));
  const old = { ...context, source_published_at: "2000-01-01T00:00:00Z" };
  assert.deepEqual(parseQualityGenerationContext(old, expected), old); // terminal replay only
});

test("request-stable protected source files retain strict client/run/path binding", () => {
  const path = `squid/news_request_${R}/source_visual_cleaned.jpg`;
  assert.equal(normalizedSourceVisualFile(`/app/output/${path}`, "squid"), path);
  assert.equal(normalizedSourceVisualFile("squid/news_123/source_visual_cleaned.jpg", "squid"),
    "squid/news_123/source_visual_cleaned.jpg");
  for (const value of [path.replace(R, "bad"), path.replace(R, "ABCDEF12-2222-4222-8222-222222222222"),
    path.replace("news_request", "news"), path.replace("/source_", "/../source_"),
    path.replace(".jpg", ".png"), path.replace("squid", "yellow")]) {
    assert.equal(normalizedSourceVisualFile(value, "squid"), null);
  }
});

test("quality policy/context form a separate request identity without changing legacy hashing", () => {
  const base = { clientId: "yellow", sourceContent: TEXT, sourceType: "tweet", sourceUrl: URL,
    mockMode: false, templateStyle: "remix", sourceImageUrl: IMAGE };
  const old = newsCardRequestHash(base);
  assert.equal(newsCardRequestHash({ ...base, qualityContext: undefined }), old);
  assert.notEqual(newsCardRequestHash({ ...base, qualityContext: context }), old);
  assert.notEqual(qualityContextHash(context), qualityContextHash({ ...context, source_item_id: A }));
});

test("quality upstream failures expose bounded fixed codes only", () => {
  for (const code of ["quality_banner_pending", "quality_banner_collect_unknown", "quality_news_configuration_invalid"])
    assert.deepEqual(qualityUpstreamError(JSON.stringify({ detail: code })), { error: code, status: 503 });
  for (const code of ["quality_banner_image_invalid", "news_generation_request_conflict"])
    assert.deepEqual(qualityUpstreamError(JSON.stringify({ detail: code })), { error: code, status: 409 });
  for (const data of ["raw secret", JSON.stringify({ detail: "provider output text" }), JSON.stringify({ detail: { secret: "x" } })])
    assert.deepEqual(qualityUpstreamError(data), { error: "quality_banner_upstream_unknown", status: 409 });
});

test("read-only RPC returns only exact context and never forwards a provider credential", async () => {
  let calls = 0;
  assert.deepEqual(await getQualityGenerationContext({ workspaceId: W,
    supabaseUrl: "https://db.invalid", serviceRoleKey: "fixture-key" }, expected,
    async (url, init) => {
      calls++; assert.equal(String(url), "https://db.invalid/rest/v1/rpc/get_quality_banner_generation_context");
      assert.deepEqual(JSON.parse(String(init?.body)), { target_workspace_id: W, target_client_id: "yellow", target_request_id: R });
      return Response.json(context);
    }, AbortSignal.timeout(1000)), context);
  assert.equal(calls, 1);
});

test("quality output rejects fake template claims, tampering, corrupt PNG and transparency", async () => {
  assert.equal(validQualityBannerMetadata(qualitySpec()), true);
  assert.equal(validQualityBannerPng(qualitySpec(), png, { width: 2880, height: 2880 }), true);
  assert.equal(await validQualityBannerPixels(png), true);
  for (const change of [{ template_version: "yellow-news-remix@1" }, { visual_design_profile_id: "fake" },
    { channel_profile: "source_native" }, { quality_banner: { ...qualitySpec().quality_banner, width: 2879 } }]) {
    assert.equal(validQualityBannerMetadata({ ...qualitySpec(), ...change }), false);
  }
  assert.equal(validQualityBannerPng(qualitySpec(), png.slice(0, -8), { width: 2880, height: 2880 }), false);
  assert.equal(await validQualityBannerPixels(png.slice(0, 32)), false);
  const transparent = await sharp({ create: { width: 2880, height: 2880, channels: 4,
    background: { r: 0, g: 0, b: 0, alpha: 0 } } }).png().toBuffer();
  assert.equal(await validQualityBannerPixels(transparent.buffer.slice(transparent.byteOffset,
    transparent.byteOffset + transparent.byteLength) as ArrayBuffer), false);
});

async function scenario({ enabled = true, automation = true, injected = false, invalidContext = false,
  badOutput = false, replay = false, imageBytes = png } = {}) {
  const originalFetch = globalThis.fetch;
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  let upstream = 0, contexts = 0, records = 0;
  let committed: Record<string, any> = {};
  const spec = qualitySpec();
  const imageHash = sha(Buffer.from(imageBytes));
  spec.quality_banner.png_sha256 = imageHash;
  if (badOutput) spec.quality_banner.png_sha256 = "b".repeat(64);
  const fact = { schema_version: "1.0", policy_version: "double-fact-check@1", content_kind: "daily_news",
    status: "review", human_review_required: true, input_sha256: "a".repeat(64), output_sha256: "b".repeat(64),
    checks: [{ id: "source_evidence", status: "review", label: "Source", detail: "Check", metrics: {} },
      { id: "output_claims", status: "pass", label: "Output", detail: "Check", metrics: {} }] };
  const hash = newsCardRequestHash({ clientId: "yellow", sourceContent: TEXT, sourceType: "tweet",
    sourceUrl: URL, mockMode: false, templateStyle: "remix", sourceImageUrl: IMAGE, qualityContext: context });
  const storagePath = `${W}/yellow/${A}/news-card.png`;
  const asset = { asset_id: A, asset_kind: "png", filename: "news-card.png", storage_bucket: "content-studio", storage_path: storagePath,
    mime_type: "image/png", byte_size: imageBytes.byteLength, sha256: imageHash, width: 2880, height: 2880 };
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) return Response.json({ id: "content-studio", public: false });
    if (url.includes("/rest/v1/workspace_clients")) return Response.json([{ workspace_id: W, client_id: "yellow", active: true }]);
    if (url.endsWith("/rest/v1/rpc/get_quality_banner_generation_context")) {
      contexts++; return Response.json(invalidContext ? null : context);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(replay ? {
      content_item_id: R, content_version_id: V, client_id: "yellow", content_kind: "daily_news", status: "needs_review", title: spec.headline,
      content: { request_hash: hash, quality_context: context, spec,
        source: { image_url: IMAGE, prepared_sha256: "c".repeat(64), media_status: "present" },
        render: { requested_template_style: "remix", template_style: "remix", source_image_used: true,
          source_visual_file: null, figma_template: null } },
      channel_copy: { telegram: "한국어 검수 내용", x: "한국어 내용" },
      generation_meta: { request_hash: hash, mock_mode: false, fact_check: fact }, assets: [asset],
    } : null);
    if (url.includes("cdn.syndication.twimg.com")) return Response.json({ id_str: "1234567890", text: TEXT,
      user: { screen_name: "Yellow", id_str: "2651" }, photos: [{ url: IMAGE }] });
    if (url.endsWith("/clients/yellow/generate/news-card")) {
      upstream++;
      assert.equal(new Headers(init?.headers).get("Idempotency-Key"), R);
      const body = JSON.parse(String(init?.body));
      assert.deepEqual(body.quality_context, enabled ? context : undefined);
      return Response.json({ client_id: "yellow", content_type: "news_card", spec,
        png_path: "/app/output/yellow/news_123/news-card.png", template_style: "remix",
        requested_template_style: "remix", source_image_used: true, source_image_url: IMAGE,
        source_image_sha256: "c".repeat(64), source_visual_path: null, figma_template: null,
        manifest_path: "/app/output/yellow/news_123/manifest.json", duration_ms: 1 });
    }
    if (url.endsWith("/files/yellow/news_123/news-card.png")
      || url.endsWith(`/storage/v1/object/authenticated/content-studio/${storagePath}`)
      || (url.endsWith(`/storage/v1/object/content-studio/${storagePath}`) && init?.method !== "POST")) return new Response(imageBytes);
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") return Response.json({ Key: "stored" });
    if (url.endsWith("/rest/v1/rpc/record_generated_content")) {
      records++; committed = JSON.parse(String(init?.body));
      return Response.json({ content_item_id: R, content_version_id: V, asset_ids: [committed.target_asset.asset_id] });
    }
    throw new Error("unexpected mocked fetch");
  };
  Object.defineProperty(globalThis, "Netlify", { configurable: true, value: { env: { get: (name: string) => ({
    API_SECRET: "fixture-admin", RAILWAY_API_URL: "https://railway.invalid", STUDIO_ACCESS_TOKEN: "fixture-studio-access-token",
    STUDIO_AUTOMATION_TOKEN: "fixture-automation-token-long-enough", SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "fixture-service", CONTENT_STUDIO_WORKSPACE_ID: W,
    QUALITY_BANNER_ENABLED: enabled ? "true" : "false",
  } as Record<string, string>)[name] } } });
  try {
    const response = await handler(new Request("https://app.invalid/api/news-card/yellow", {
      method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": R,
        cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue("fixture-studio-access-token")}`,
        ...(automation ? { "X-Studio-Automation-Key": "fixture-automation-token-long-enough" } : {}) },
      body: JSON.stringify({ ...requestBody, ...(injected ? { quality_context: context } : {}) }),
    }), { params: { clientId: "yellow" } } as never);
    return { status: response.status, body: await response.json(), upstream, contexts, records, committed };
  } finally {
    globalThis.fetch = originalFetch;
    if (descriptor) Object.defineProperty(globalThis, "Netlify", descriptor); else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

test("quality natural generation persists actual job identity/native PNG and returns receipt only", async () => {
  const r = await scenario();
  assert.equal(r.status, 200, JSON.stringify(r.body));
  assert.equal(r.body.image_data_url, ""); assert.equal(r.body.output_width, 2880);
  assert.equal(r.body.content_item_id, R); assert.equal(r.body.content_version_id, V);
  assert.equal(r.upstream, 1); assert.equal(r.contexts, 1); assert.equal(r.records, 1);
  assert.deepEqual(r.committed.target_content.quality_context, context);
  assert.equal(r.committed.target_generation_meta.renderer, "higgsfield-sunburst-max");
  assert.equal(r.committed.target_prompt_version, QUALITY_BANNER_POLICY);
  assert.equal(r.committed.target_asset.filename, "news-card.png");
});

test("quality context rejection/injection blocks provider and persistence", async () => {
  for (const config of [{ invalidContext: true }, { injected: true }]) {
    const r = await scenario(config); assert.ok(r.status >= 400);
    assert.equal(r.upstream, 0); assert.equal(r.records, 0);
  }
});

test("tampered output and legacy/manual quality forgery cannot persist", async () => {
  for (const config of [{ badOutput: true }, { enabled: false }, { automation: false }]) {
    const r = await scenario(config); assert.equal(r.status, config.automation === false ? 403 : 502, JSON.stringify(r.body));
    assert.equal(r.records, 0);
  }
});

test("terminal quality replay reconciles immutable catalog without new natural snapshot/provider", async () => {
  const r = await scenario({ replay: true });
  assert.equal(r.status, 200, JSON.stringify(r.body)); assert.equal(r.body.reused, true);
  assert.equal(r.body.image_data_url, ""); assert.equal(r.upstream, 0);
  assert.equal(r.contexts, 0); assert.equal(r.records, 0);
});

test("large native quality PNG bypasses legacy 3MB ceiling without entering JSON envelope", async () => {
  const buffer = await sharp({ create: { width: 2880, height: 2880, channels: 3,
    background: "#ffffff" } }).toColourspace("b-w").png({ compressionLevel: 0 }).toBuffer();
  assert.ok(buffer.byteLength > 5_000_000 && buffer.byteLength <= 10_000_000);
  const imageBytes = buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength) as ArrayBuffer;
  for (const replay of [false, true]) {
    const r = await scenario({ replay, imageBytes });
    assert.equal(r.status, 200, JSON.stringify(r.body));
    assert.equal(r.body.image_data_url, "");
    assert.equal(r.body.reused, replay);
    assert.ok(JSON.stringify(r.body).length < 100_000);
    assert.equal(r.contexts, replay ? 0 : 1); assert.equal(r.upstream, replay ? 0 : 1);
    if (!replay) assert.equal(r.committed.target_asset.byte_size, buffer.byteLength);
  }
});
