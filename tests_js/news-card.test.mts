import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import newsCardHandler, {
  deadlineSignal as newsCardDeadlineSignal,
  MAX_NEWS_CARD_BYTES,
  newsCardRequestHash,
} from "../netlify/functions/news-card.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const REQUEST_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const ASSET_ID = "44444444-4444-4444-8444-444444444444";
const ACCESS_TOKEN = "news-card-studio-access-token";
const SOURCE = "Squid shipped a safer cross-chain routing update for integrators.";
const SOURCE_URL = "https://x.com/squidrouter/status/1234567890";

function minimalPng(width = 1080, height = 1080): Uint8Array {
  const bytes = new Uint8Array(25);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width, false);
  view.setUint32(20, height, false);
  bytes[24] = 7;
  return bytes;
}

function requestBody(): Record<string, unknown> {
  return {
    source_content: SOURCE,
    source_type: "tweet",
    source_url: SOURCE_URL,
    mock_mode: false,
    template_style: "classic",
  };
}

function studioRequest(body = requestBody(), includeIdempotency = true): Request {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
  };
  if (includeIdempotency) headers["Idempotency-Key"] = REQUEST_ID;
  return new Request("https://console.example/api/news-card/squid", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

async function withEnvironment(
  fetcher: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  globalThis.fetch = fetcher;
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          return ({
            API_SECRET: "railway-secret",
            RAILWAY_API_URL: "https://railway.example",
            STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
            SUPABASE_URL: "https://project.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
            CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
          } as Record<string, string>)[name];
        },
      },
    },
  });
  try {
    await run();
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

test("news card request hash binds every submitted generation input", () => {
  const input = {
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "classic",
  };
  assert.match(newsCardRequestHash(input), /^[a-f0-9]{64}$/);
  assert.equal(newsCardRequestHash({ ...input }), newsCardRequestHash(input));
  assert.notEqual(newsCardRequestHash({ ...input, templateStyle: "remix" }), newsCardRequestHash(input));
  assert.notEqual(newsCardRequestHash({ ...input, sourceUrl: `${SOURCE_URL}1` }), newsCardRequestHash(input));
});

test("news card Railway deadline preserves the persistence reserve", () => {
  const now = Date.now();
  assert.throws(
    () => newsCardDeadlineSignal(now + 18_000, 38_000, 18_000),
    (error: unknown) => error instanceof Error && error.message === "news_card_deadline_exceeded",
  );
  assert.equal(
    newsCardDeadlineSignal(now + 18_500, 38_000, 18_000).aborted,
    false,
  );

  const source = readFileSync(
    new URL("../netlify/functions/news-card.mts", import.meta.url),
    "utf8",
  );
  assert.match(source, /const NEWS_CARD_PERSISTENCE_RESERVE_MS = 18_000;/);
  assert.match(
    source,
    /signal:\s*deadlineSignal\(\s*requestDeadline,\s*RAILWAY_GENERATION_BUDGET_MS,\s*NEWS_CARD_PERSISTENCE_RESERVE_MS,\s*\)/,
  );
});

test("news card generation persists one immutable PNG before returning", async () => {
  const png = minimalPng();
  let railwayCalls = 0;
  let recordBody: Record<string, unknown> = {};
  let uploadUpsert = "";

  await withEnvironment(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/clients/squid/generate/news-card")) {
      railwayCalls += 1;
      return Response.json({
        client_id: "squid",
        content_type: "news_card",
        spec: {
          label: "업데이트",
          headline: "Squid 라우팅 업데이트",
          body_lines: ["원문에 근거한 업데이트"],
          source_url: SOURCE_URL,
        },
        png_path: "/app/output/squid/news_123/news_card_classic.png",
        template_style: "classic",
        requested_template_style: "classic",
        source_image_used: false,
        source_visual_path: null,
        manifest_path: "/app/output/squid/news_123/manifest.json",
        duration_ms: 1234,
      });
    }
    if (url.endsWith("/files/squid/news_123/news_card_classic.png")) {
      return new Response(png, { headers: { "content-type": "image/png" } });
    }
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") {
      uploadUpsert = new Headers(init.headers).get("x-upsert") || "";
      return Response.json({ Key: "stored" });
    }
    if (url.endsWith("/rest/v1/rpc/record_generated_content")) {
      recordBody = JSON.parse(String(init?.body));
      const asset = recordBody.target_asset as { asset_id?: string };
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        asset_ids: [asset.asset_id],
      });
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest(), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(result.content_item_id, REQUEST_ID);
    assert.equal(result.content_version_id, VERSION_ID);
    assert.equal(result.asset_ids.length, 1);
    assert.equal(result.storage_backend, "supabase");
    assert.equal(result.reused, false);
    assert.match(result.image_data_url, /^data:image\/png;base64,/);
    assert.equal(railwayCalls, 1);
    assert.equal(uploadUpsert, "false");
    assert.equal(recordBody.target_content_kind, "daily_news");
    assert.equal((recordBody.target_generation_meta as Record<string, unknown>).mock_mode, false);
  });
});

test("an exact news card retry replays verified Supabase bytes without Railway", async () => {
  const png = minimalPng();
  const sha256 = createHash("sha256").update(png).digest("hex");
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "classic",
  });
  const storagePath = `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`;
  let railwayCalls = 0;

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        client_id: "squid",
        content_kind: "daily_news",
        status: "needs_review",
        title: "Squid 라우팅 업데이트",
        content: {
          request_hash: requestHash,
          spec: { headline: "Squid 라우팅 업데이트", body_lines: ["사실"] },
          source: { mode: "provided", image_url: "" },
          render: { template_style: "classic", requested_template_style: "classic" },
        },
        channel_copy: { telegram: "텔레그램", x: "X" },
        generation_meta: { request_hash: requestHash, duration_ms: 1234, mock_mode: false },
        assets: [{
          asset_id: ASSET_ID,
          asset_kind: "png",
          storage_bucket: "content-studio",
          storage_path: storagePath,
          filename: "news-card.png",
          mime_type: "image/png",
          byte_size: png.byteLength,
          sha256,
          width: 1080,
          height: 1080,
        }],
      });
    }
    if (url.includes("/storage/v1/object/content-studio/")) return new Response(png);
    if (url.includes("railway.example")) railwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest(), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(result.reused, true);
    assert.equal(result.content_item_id, REQUEST_ID);
    assert.equal(result.asset_ids[0], ASSET_ID);
    assert.equal(railwayCalls, 0);
  });
});

test("news card generation rejects a PNG that cannot fit safely in base64 JSON", async () => {
  const png = new Uint8Array(MAX_NEWS_CARD_BYTES + 1);
  png.set(minimalPng());
  let uploadCalls = 0;

  await withEnvironment(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/clients/squid/generate/news-card")) {
      return Response.json({
        client_id: "squid",
        content_type: "news_card",
        spec: { headline: "Squid 라우팅 업데이트" },
        png_path: "/app/output/squid/news_123/news_card_classic.png",
        template_style: "classic",
        requested_template_style: "classic",
        source_image_used: false,
        source_visual_path: null,
        manifest_path: "/app/output/squid/news_123/manifest.json",
        duration_ms: 1234,
      });
    }
    if (url.endsWith("/files/squid/news_123/news_card_classic.png")) {
      return new Response(png, { headers: { "content-type": "image/png" } });
    }
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") {
      uploadCalls += 1;
      return Response.json({ Key: "unexpected" });
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest(), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "generated_image_too_large" });
    assert.equal(uploadCalls, 0);
  });
});

test("news card replay rejects an oversized stored PNG before download", async () => {
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "classic",
  });
  const storagePath = `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`;
  let assetDownloads = 0;

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        client_id: "squid",
        content_kind: "daily_news",
        status: "needs_review",
        title: "Squid 라우팅 업데이트",
        content: {
          request_hash: requestHash,
          spec: { headline: "Squid 라우팅 업데이트" },
          source: { mode: "provided", image_url: "" },
          render: { template_style: "classic", requested_template_style: "classic" },
        },
        channel_copy: { telegram: "텔레그램", x: "X" },
        generation_meta: { request_hash: requestHash, duration_ms: 1234, mock_mode: false },
        assets: [{
          asset_id: ASSET_ID,
          asset_kind: "png",
          storage_bucket: "content-studio",
          storage_path: storagePath,
          filename: "news-card.png",
          mime_type: "image/png",
          byte_size: MAX_NEWS_CARD_BYTES + 1,
          sha256: "0".repeat(64),
          width: 1080,
          height: 1080,
        }],
      });
    }
    if (url.includes("/storage/v1/object/content-studio/")) {
      assetDownloads += 1;
      return new Response(minimalPng());
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest(), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "generated_image_too_large" });
    assert.equal(assetDownloads, 0);
  });
});

test("news card generation requires a UUID idempotency key before fetching", async () => {
  let fetched = false;
  await withEnvironment(async () => {
    fetched = true;
    return Response.json({});
  }, async () => {
    const response = await newsCardHandler(studioRequest(requestBody(), false), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_news_card_idempotency_key" });
    assert.equal(fetched, false);
  });
});
