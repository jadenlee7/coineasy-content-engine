import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import grokQaHandler from "../netlify/functions/grok-qa-mcp.mts";
import type { ContentLibraryDetail } from "../netlify/functions/_shared/content-catalog.mts";
import {
  buildGrokQaReviewPackage,
  claimGrokQaVerdict,
  finalizeGrokQaVerdict,
  grokQaBannerImage,
  grokQaConnectorConfig,
  hasGrokQaConnectorAccess,
  type GrokQaVerdict,
} from "../netlify/functions/_shared/grok-qa.mts";

const TOKEN = "grok-qa-test-token-that-is-long-enough";
const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const ITEM_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const SOURCE_URL = "https://x.com/squidrouter/status/2083266484789514640";
const CREATED_AT = "2026-08-13T08:00:00.000Z";

function detail(): ContentLibraryDetail {
  return {
    content_item_id: ITEM_ID,
    client_id: "squid",
    content_kind: "daily_news",
    title: "Squid가 Telegram에서 열렸어요",
    status: "needs_review",
    current_version_id: VERSION_ID,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    current_version: {
      content_version_id: VERSION_ID,
      version_number: 1,
      prompt_version: "news-card@3",
      locale: "ko-KR",
      title: "Squid가 Telegram에서 열렸어요",
      content: {
        request_hash: "a".repeat(64),
        spec: { headline: "텔레그램에서도 Squid를 만나보세요" },
        source: {
          submitted_content: "private raw source must not reach Grok",
          resolved_content: "private resolved source must not reach Grok",
          url: SOURCE_URL,
          image_url: "https://private.example/signed-source.png?token=secret",
        },
        render: { template_style: "remix" },
      },
      channel_copy: { telegram: "공식 Telegram 소식을 확인해 보세요.", x: "Squid Telegram" },
      deliverables: {},
      qa: { status: "needs_review" },
      generation_meta: {
        mock_mode: false,
        brand_qa: { status: "pass", score: 100 },
        fact_check: { status: "review", human_review_required: true },
      },
      created_at: CREATED_AT,
    },
    assets: [{
      asset_id: "44444444-4444-4444-8444-444444444444",
      asset_kind: "png",
      filename: "news-card.png",
      mime_type: "image/png",
      byte_size: 1024,
      sha256: "b".repeat(64),
      width: 1080,
      height: 1080,
      url: "https://project.supabase.co/storage/v1/object/sign/content-studio/private.png?token=secret",
      expires_in: 60,
    }],
    figma_links: [],
  };
}

const verdict: GrokQaVerdict = {
  decision: "PASS",
  summary: "공식 원문과 한국어 문구, Squid 브랜드 표현이 모두 일치합니다.",
  fact_check: {
    status: "PASS",
    checks: ["공식 X 원문의 Telegram 공개 사실을 확인했습니다."],
    source_urls: [SOURCE_URL],
  },
  brand_check: {
    status: "PASS",
    checks: ["Squid 공식 명칭과 절제된 문장 구조를 확인했습니다."],
  },
  issues: [],
  next_action: "ready_for_human_approval",
};

async function withNetlifyEnvironment(
  values: Record<string, string | undefined>,
  run: () => Promise<void>,
): Promise<void> {
  const original = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: (name: string) => values[name] } },
  });
  try {
    await run();
  } finally {
    if (original) Object.defineProperty(globalThis, "Netlify", original);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

function mcpRequest(body: Record<string, unknown>, token = TOKEN): Request {
  return new Request("https://coineasy-newscard.netlify.app/api/grok-qa/mcp", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "MCP-Protocol-Version": "2025-06-18",
    },
    body: JSON.stringify(body),
  });
}

function mcpRequestAt(url: string, body: Record<string, unknown>, token = TOKEN): Request {
  return new Request(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
      "MCP-Protocol-Version": "2025-06-18",
    },
    body: JSON.stringify(body),
  });
}

async function sseJson(response: Response): Promise<Record<string, any>> {
  const text = await response.text();
  const data = text.split("\n").find((line) => line.startsWith("data: "));
  assert.ok(data);
  return JSON.parse(data.slice(6));
}

test("Grok review package exposes generated QA evidence but never raw source or signed URLs", () => {
  const item = detail();
  item.title = "mutable item title must not reach Grok";
  const reviewPackage = buildGrokQaReviewPackage(item);
  assert.equal(reviewPackage.title, "Squid가 Telegram에서 열렸어요");
  assert.deepEqual(reviewPackage.source_urls, [SOURCE_URL]);
  assert.equal(reviewPackage.banner.available, true);
  assert.equal(reviewPackage.generated_content.spec.headline, "텔레그램에서도 Squid를 만나보세요");
  assert.equal(reviewPackage.brand_contract.profile_version, "squid/brand-review@1");
  assert.match(reviewPackage.brand_contract.banner_rule, /final composition/);
  const serialized = JSON.stringify(reviewPackage);
  assert.doesNotMatch(serialized, /private raw source|private resolved source/);
  assert.doesNotMatch(serialized, /signed-source|storage\/v1\/object\/sign|token=secret/);
  assert.doesNotMatch(serialized, /request_hash/);
});

test("Grok connector requires its own bounded constant-time bearer", () => {
  assert.deepEqual(grokQaConnectorConfig((name) => (
    name === "GROK_QA_CONNECTOR_TOKEN" ? TOKEN : undefined
  )), { token: TOKEN });
  assert.equal(grokQaConnectorConfig(() => "short"), null);
  assert.equal(grokQaConnectorConfig((name) => (
    ["GROK_QA_CONNECTOR_TOKEN", "API_SECRET"].includes(name)
      ? TOKEN
      : undefined
  )), null);
  assert.equal(grokQaConnectorConfig((name) => (
    ["GROK_QA_CONNECTOR_TOKEN", "GROK_QA_OAUTH_SIGNING_SECRET"].includes(name)
      ? TOKEN
      : undefined
  )), null);
  assert.equal(grokQaConnectorConfig((name) => (
    ["GROK_QA_CONNECTOR_TOKEN", "GROK_QA_DISPATCH_TOKEN"].includes(name)
      ? TOKEN
      : undefined
  )), null);
  assert.equal(hasGrokQaConnectorAccess(new Request("https://example.com", {
    headers: { Authorization: `Bearer ${TOKEN}` },
  }), TOKEN), true);
  assert.equal(hasGrokQaConnectorAccess(new Request("https://example.com", {
    headers: { Authorization: "Bearer wrong-token-that-is-still-long-enough" },
  }), TOKEN), false);
});

test("Grok verdict receipt claims once and finalizes only the returned payload hash", async () => {
  const calls: Request[] = [];
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    calls.push(request);
    if (request.url.endsWith("/rpc/claim_grok_qa_verdict")) {
      return Response.json({
        claimed: true,
        status: "claimed",
        payload_sha256: "c".repeat(64),
        decision: "PASS",
      });
    }
    if (request.url.endsWith("/rpc/finalize_grok_qa_verdict")) {
      return Response.json({
        status: "sent",
        payload_sha256: "c".repeat(64),
        decision: "PASS",
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  const config = {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
  };
  const receipt = await claimGrokQaVerdict(config, ITEM_ID, VERSION_ID, verdict, fetcher);
  assert.equal(receipt.claimed, true);
  await finalizeGrokQaVerdict(config, VERSION_ID, receipt.payload_sha256!, "sent", null, fetcher);
  assert.equal(calls.length, 2);
  assert.match(calls[0].url, /claim_grok_qa_verdict$/);
  assert.match(calls[1].url, /finalize_grok_qa_verdict$/);
  assert.equal(calls[0].headers.get("authorization"), "Bearer server-only-service-role");
  const claimBody = await calls[0].json() as Record<string, unknown>;
  assert.deepEqual(claimBody.target_payload, verdict);
  assert.doesNotMatch(JSON.stringify(claimBody), /TELEGRAM|chat_id|publish/);
});

test("Grok banner preview requires the exact stored PNG hash", async () => {
  const png = new Uint8Array([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00,
  ]);
  const item = detail();
  item.assets[0]!.byte_size = png.byteLength;
  item.assets[0]!.sha256 = createHash("sha256").update(png).digest("hex");
  const exact = await grokQaBannerImage(item, async () => new Response(png, {
    headers: { "Content-Type": "image/png" },
  }));
  assert.equal(exact?.mimeType, "image/png");
  assert.equal(exact?.data, Buffer.from(png).toString("base64"));

  item.assets[0]!.sha256 = "d".repeat(64);
  const changed = await grokQaBannerImage(item, async () => new Response(png, {
    headers: { "Content-Type": "image/png" },
  }));
  assert.equal(changed, null);
});

test("MCP advertises exactly the bounded review tools and rejects a wrong bearer", async () => {
  await withNetlifyEnvironment({ GROK_QA_CONNECTOR_TOKEN: TOKEN }, async () => {
    const unauthorized = await grokQaHandler(
      mcpRequest({ jsonrpc: "2.0", id: 1, method: "tools/list", params: {} }, `${TOKEN}-wrong`),
      {} as never,
    );
    assert.equal(unauthorized.status, 401);
    assert.equal(
      unauthorized.headers.get("www-authenticate"),
      'Bearer realm="coineasy-grok-qa"',
    );

    const response = await grokQaHandler(
      mcpRequest({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }),
      {} as never,
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    const payload = await sseJson(response);
    const names = payload.result.tools.map((tool: Record<string, unknown>) => tool.name);
    assert.deepEqual(names, [
      "coineasy_list_needs_review",
      "coineasy_get_review_package",
      "coineasy_submit_qa_verdict",
    ]);
    assert.doesNotMatch(names.join(" "), /approve|publish|typefully/);
    assert.equal(payload.result.tools[0].annotations.readOnlyHint, true);
    assert.equal(payload.result.tools[2].annotations.idempotentHint, true);
  });
});

test("MCP accepts only the exact Netlify deploy-preview prime URL in deploy-preview context", async () => {
  const preview = "https://deploy-preview-139--coineasy-newscard.netlify.app";
  const body = { jsonrpc: "2.0", id: 21, method: "tools/list", params: {} };
  await withNetlifyEnvironment({
    GROK_QA_CONNECTOR_TOKEN: TOKEN,
    CONTEXT: "deploy-preview",
    DEPLOY_PRIME_URL: preview,
  }, async () => {
    const exact = await grokQaHandler(mcpRequestAt(`${preview}/api/grok-qa/mcp`, body), {} as never);
    assert.equal(exact.status, 200);
    const wrongPreview = await grokQaHandler(
      mcpRequestAt("https://deploy-preview-140--coineasy-newscard.netlify.app/api/grok-qa/mcp", body),
      {} as never,
    );
    assert.equal(wrongPreview.status, 421);
    const customHost = await grokQaHandler(
      mcpRequestAt("https://preview.attacker.example/api/grok-qa/mcp", body),
      {} as never,
    );
    assert.equal(customHost.status, 421);
  });
  await withNetlifyEnvironment({
    GROK_QA_CONNECTOR_TOKEN: TOKEN,
    CONTEXT: "production",
    DEPLOY_PRIME_URL: preview,
  }, async () => {
    const productionContext = await grokQaHandler(
      mcpRequestAt(`${preview}/api/grok-qa/mcp`, body),
      {} as never,
    );
    assert.equal(productionContext.status, 421);
  });
});

test("MCP advertises protected-resource discovery only with a complete OAuth config", async () => {
  const values = {
    GROK_QA_CONNECTOR_TOKEN: TOKEN,
    GROK_QA_OAUTH_ENABLED: "true",
    GROK_QA_OAUTH_ISSUER: "https://coineasy-newscard.netlify.app",
    GROK_QA_OAUTH_ALLOWED_REDIRECT_ORIGINS: "https://grok.com",
    GROK_QA_OAUTH_OPERATOR_SECRET: "operator-" + "o".repeat(40),
    GROK_QA_OAUTH_SIGNING_SECRET: "signing-" + "s".repeat(40),
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "project-" + "p".repeat(40),
    SUPABASE_GROK_QA_OAUTH_KEY: "scoped-" + "q".repeat(40),
  };
  await withNetlifyEnvironment(values, async () => {
    const unauthorized = await grokQaHandler(
      mcpRequest({ jsonrpc: "2.0", id: 3, method: "tools/list", params: {} }, `${TOKEN}-wrong`),
      {} as never,
    );
    assert.equal(unauthorized.status, 401);
    assert.equal(
      unauthorized.headers.get("www-authenticate"),
      'Bearer realm="coineasy-grok-qa", resource_metadata="https://coineasy-newscard.netlify.app/.well-known/oauth-protected-resource/api/grok-qa/mcp"',
    );
  });
});

test("MCP submit rejects a missing banner before claiming a durable receipt", async () => {
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  let receiptCalls = 0;
  const item = detail();
  const raw = {
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    client_id: item.client_id,
    content_kind: item.content_kind,
    title: "mutable item title",
    status: item.status,
    created_at: item.created_at,
    updated_at: item.updated_at,
    current_version: item.current_version,
    assets: [],
    figma_links: [],
  };
  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/rest/v1/rpc/get_content_library_item")) {
        return Response.json(raw);
      }
      if (url.endsWith("/rest/v1/rpc/claim_grok_qa_verdict")) {
        receiptCalls += 1;
        return Response.json({ claimed: true });
      }
      throw new Error(`unexpected request ${url}`);
    },
  });
  try {
    await withNetlifyEnvironment({
      GROK_QA_CONNECTOR_TOKEN: TOKEN,
      GROK_QA_RELAY_TOKEN: "dedicated-relay-token-that-is-long-enough",
      RAILWAY_API_URL: "https://content-engine.example",
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "service-role-key-for-private-tests",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await grokQaHandler(mcpRequest({
        jsonrpc: "2.0",
        id: 3,
        method: "tools/call",
        params: {
          name: "coineasy_submit_qa_verdict",
          arguments: {
            content_item_id: ITEM_ID,
            content_version_id: VERSION_ID,
            verdict,
          },
        },
      }), {} as never);
      assert.equal(response.status, 200);
      assert.match(await response.text(), /qa_banner_unavailable/);
    });
  } finally {
    if (originalFetch) Object.defineProperty(globalThis, "fetch", originalFetch);
    else Reflect.deleteProperty(globalThis, "fetch");
  }
  assert.equal(receiptCalls, 0);
});
