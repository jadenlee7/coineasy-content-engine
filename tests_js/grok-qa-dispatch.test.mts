import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import dispatchHandler from "../netlify/functions/grok-qa-dispatch.mts";
import type {
  ContentCatalogConfig,
  ContentLibraryDetail,
} from "../netlify/functions/_shared/content-catalog.mts";
import {
  executeGrokQaDispatchAction,
  grokQaDispatchAccessConfigured,
  GrokQaDispatchError,
  hasGrokQaDispatchAccess,
  parseGrokQaDispatchAction,
} from "../netlify/functions/_shared/grok-qa-dispatch.mts";
import {
  grokQaRelayConfig,
  sendGrokQaVerdictOutcome,
  type GrokQaVerdict,
} from "../netlify/functions/_shared/grok-qa.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const ITEM_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const OTHER_VERSION_ID = "66666666-6666-4666-8666-666666666666";
const ASSET_ID = "44444444-4444-4444-8444-444444444444";
const CREATED_AT = "2026-08-13T08:00:00.000Z";
const FRESH_SOURCE_PUBLISHED_AT = new Date(Date.now() - (60 * 60 * 1_000)).toISOString();
const STALE_SOURCE_PUBLISHED_AT = new Date(Date.now() - (25 * 60 * 60 * 1_000)).toISOString();
const FUTURE_SOURCE_PUBLISHED_AT = new Date(Date.now() + (10 * 60 * 1_000)).toISOString();
const SOURCE_URL = "https://x.com/squidrouter/status/2083266484789514640";
const DISPATCH_TOKEN = "dispatch-token-that-is-dedicated-and-long-enough";
const RELAY_TOKEN = "relay-token-that-is-dedicated-and-long-enough";
const WORKER_ID = "grok-qa-worker-01";
const VERDICT_HASH = "c".repeat(64);
const INPUT_HASH = "d".repeat(64);
const PNG = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
  0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
  0x00, 0x00, 0x04, 0x38, 0x00, 0x00, 0x04, 0x38,
]);
const BANNER_HASH = createHash("sha256").update(PNG).digest("hex");

const catalogConfig: ContentCatalogConfig = {
  supabaseUrl: "https://project.supabase.co",
  serviceRoleKey: "server-only-service-role-key",
  workspaceId: WORKSPACE_ID,
};

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

function rawDetail() {
  return {
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    client_id: "squid",
    content_kind: "daily_news",
    title: "mutable item title must not reach review or relay",
    status: "needs_review",
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    version_number: 1,
    prompt_version: "official-x-draft@1",
    locale: "ko-KR",
    content: {
      spec: { headline: "텔레그램에서도 Squid를 만나보세요" },
      source: {
        url: SOURCE_URL,
        submitted_content: "private raw source must not reach Grok",
        resolved_content: "private resolved source must not reach Grok",
        image_url: "https://private.example/source.png?token=secret",
      },
      render: {
        template_style: "remix",
        source_visual_file: "squid/private/source_visual_cleaned.jpg",
        note: "https://project.supabase.co/storage/v1/object/sign/content-studio/private.png?token=secret",
      },
      request_hash: "a".repeat(64),
    },
    channel_copy: { telegram: "공식 Telegram 소식을 확인해 보세요.", x: "Squid Telegram" },
    deliverables: {},
    qa: { status: "needs_review" },
    generation_meta: {
      mock_mode: false,
      brand_qa: { status: "pass", score: 100 },
      fact_check: { status: "review", human_review_required: true },
    },
    current_version: {
      content_version_id: VERSION_ID,
      version_number: 1,
      prompt_version: "official-x-draft@1",
      locale: "ko-KR",
      title: "Squid 한국어 뉴스",
      content: {
        spec: { headline: "텔레그램에서도 Squid를 만나보세요" },
        source: { url: SOURCE_URL },
        request_hash: "a".repeat(64),
      },
      channel_copy: {
        telegram: "공식 Telegram 소식을 확인해 보세요.",
        x: "Squid Telegram",
      },
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
      asset_id: ASSET_ID,
      asset_kind: "png",
      storage_bucket: "content-studio",
      storage_path: `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`,
      mime_type: "image/png",
      byte_size: PNG.byteLength,
      sha256: BANNER_HASH,
      width: 1080,
      height: 1080,
      metadata: {},
      created_at: CREATED_AT,
    }],
    figma_links: [],
  };
}

function detailForRelay(): ContentLibraryDetail {
  return {
    content_item_id: ITEM_ID,
    client_id: "squid",
    content_kind: "daily_news",
    title: "mutable item title must not be relayed",
    status: "needs_review",
    current_version_id: VERSION_ID,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    current_version: {
      content_version_id: VERSION_ID,
      version_number: 1,
      prompt_version: "qa@1",
      locale: "ko-KR",
      title: "Squid 한국어 뉴스",
      content: {},
      channel_copy: {},
      deliverables: {},
      qa: {},
      generation_meta: { mock_mode: false },
      created_at: CREATED_AT,
    },
    assets: [{
      asset_id: ASSET_ID,
      asset_kind: "png",
      filename: "news-card.png",
      mime_type: "image/png",
      byte_size: PNG.byteLength,
      sha256: BANNER_HASH,
      width: 1080,
      height: 1080,
      url: "https://project.supabase.co/storage/v1/object/sign/content-studio/review.png?token=short-lived",
      expires_in: 60,
    }],
    figma_links: [],
  };
}

function signedPath() {
  return `/object/sign/content-studio/${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png?token=short-lived`;
}

function claimResult(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "1.0",
    mode: "official_x_grok_qa_dispatch",
    workspace_id: WORKSPACE_ID,
    job: {
      content_item_id: ITEM_ID,
      content_version_id: VERSION_ID,
      source_item_id: "55555555-5555-4555-8555-555555555555",
      client_id: "squid",
      content_kind: "daily_news",
      source_event_id: 17,
      source_event_type: "official_x_review_draft_completed",
      source_url: SOURCE_URL,
      source_author_handle: "@SquidRouter",
      source_published_at: FRESH_SOURCE_PUBLISHED_AT,
      status: "claimed",
      attempts: 1,
      max_attempts: 3,
      lease_expires_at: "2026-08-13T08:05:00.000Z",
      verdict: null,
      verdict_sha256: null,
      model: null,
      prompt_version: null,
      input_sha256: null,
      banner_sha256: null,
      provider_attempt_started_at: null,
      provider_response_id: null,
      cost_in_usd_ticks: null,
      x_search_citations: null,
      x_search_calls: null,
      provider_call_required: true,
      claim_granted: true,
      ...overrides,
    },
  };
}

function stageAction(action: "stage" | "deliver" = "stage") {
  return parseGrokQaDispatchAction({
    action,
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    worker_id: WORKER_ID,
    verdict,
    model: "grok-4.5",
    prompt_version: "official-x-grok-qa@1",
    provider_response_id: "resp_abc123",
    input_sha256: INPUT_HASH,
    banner_sha256: BANNER_HASH,
    cost_in_usd_ticks: 100_000_000,
    x_search_citations: [SOURCE_URL],
    x_search_calls: 1,
    ...(action === "deliver" ? { verdict_sha256: VERDICT_HASH } : {}),
  });
}

function environment(overrides: Record<string, string | undefined> = {}) {
  const values: Record<string, string | undefined> = {
    GROK_QA_DISPATCH_TOKEN: DISPATCH_TOKEN,
    GROK_QA_RELAY_TOKEN: RELAY_TOKEN,
    RAILWAY_API_URL: "https://content-engine.example",
    SUPABASE_URL: catalogConfig.supabaseUrl,
    SUPABASE_SERVICE_ROLE_KEY: catalogConfig.serviceRoleKey,
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    ...overrides,
  };
  return (name: string) => values[name];
}

async function withGlobals(
  env: (name: string) => string | undefined,
  fetcher: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: env } },
  });
  Object.defineProperty(globalThis, "fetch", { configurable: true, value: fetcher });
  try {
    await run();
  } finally {
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
    if (originalFetch) Object.defineProperty(globalThis, "fetch", originalFetch);
    else Reflect.deleteProperty(globalThis, "fetch");
  }
}

function dispatchRequest(body: unknown, token = DISPATCH_TOKEN): Request {
  return new Request("https://coineasy-newscard.netlify.app/api/grok-qa/dispatch", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
}

async function catalogResponse(request: Request): Promise<Response | null> {
  if (request.url.endsWith("/rest/v1/rpc/get_content_library_item")) {
    assert.equal(request.headers.get("authorization"), `Bearer ${catalogConfig.serviceRoleKey}`);
    return Response.json(rawDetail());
  }
  if (request.method === "POST" && request.url.includes("/storage/v1/object/sign/content-studio/")) {
    return Response.json({ signedURL: signedPath() });
  }
  if (request.method === "GET" && request.url.includes("/storage/v1/object/sign/content-studio/")) {
    return new Response(PNG, {
      headers: {
        "Content-Type": "image/png",
        "Content-Length": String(PNG.byteLength),
      },
    });
  }
  return null;
}

test("dispatch authentication is dedicated, bounded, checked before parsing, and rejects secret reuse", async () => {
  assert.equal(grokQaDispatchAccessConfigured(environment()), true);
  assert.equal(grokQaDispatchAccessConfigured(environment({ API_SECRET: DISPATCH_TOKEN })), false);
  assert.equal(grokQaDispatchAccessConfigured(environment({ GROK_QA_RELAY_TOKEN: DISPATCH_TOKEN })), false);
  assert.equal(hasGrokQaDispatchAccess(dispatchRequest({}, DISPATCH_TOKEN), environment()), true);
  assert.equal(hasGrokQaDispatchAccess(dispatchRequest({}, `${DISPATCH_TOKEN}-wrong`), environment()), false);

  let fetchCalls = 0;
  await withGlobals(environment(), async () => {
    fetchCalls += 1;
    throw new Error("must not fetch");
  }, async () => {
    const unauthorized = await dispatchHandler(dispatchRequest({ action: "claim" }, "x".repeat(40)));
    assert.equal(unauthorized.status, 401);
    assert.deepEqual(await unauthorized.json(), { error: "grok_qa_dispatch_auth_required" });
  });
  assert.equal(fetchCalls, 0);

  await withGlobals(environment({ API_SECRET: DISPATCH_TOKEN }), async () => {
    fetchCalls += 1;
    throw new Error("must not fetch");
  }, async () => {
    const reused = await dispatchHandler(dispatchRequest({ action: "claim" }));
    assert.equal(reused.status, 503);
    assert.deepEqual(await reused.json(), { error: "grok_qa_dispatch_not_configured" });
  });
  assert.equal(fetchCalls, 0);
});

test("dispatch HTTP route is POST-only, production-host locked, and never cacheable", async () => {
  await withGlobals(environment(), async () => {
    throw new Error("must not fetch");
  }, async () => {
    const method = await dispatchHandler(new Request(
      "https://coineasy-newscard.netlify.app/api/grok-qa/dispatch",
    ));
    assert.equal(method.status, 405);
    assert.equal(method.headers.get("allow"), "POST");
    assert.equal(method.headers.get("cache-control"), "no-store");

    const foreign = await dispatchHandler(new Request(
      "https://attacker.example/api/grok-qa/dispatch",
      {
        method: "POST",
        headers: { Authorization: `Bearer ${DISPATCH_TOKEN}` },
        body: "{}",
      },
    ));
    assert.equal(foreign.status, 421);
    assert.deepEqual(await foreign.json(), { error: "invalid_grok_qa_dispatch_host" });
  });
});

test("dispatch parser is exact and cannot smuggle publication or arbitrary retry controls", () => {
  assert.throws(
    () => parseGrokQaDispatchAction({
      action: "claim",
      worker_id: WORKER_ID,
      lease_seconds: 300,
      allowed_clients: ["squid"],
    }),
    (error: unknown) => error instanceof GrokQaDispatchError,
  );
  assert.throws(
    () => parseGrokQaDispatchAction({
      action: "claim",
      worker_id: WORKER_ID,
      lease_seconds: 300,
      allowed_clients: ["squid"],
      canary_content_version_id: "not-a-uuid",
      max_source_age_seconds: 86_400,
    }),
    (error: unknown) => error instanceof GrokQaDispatchError,
  );
  assert.throws(
    () => parseGrokQaDispatchAction({
      action: "claim",
      worker_id: WORKER_ID,
      lease_seconds: 300,
      allowed_clients: ["squid"],
      canary_content_version_id: null,
      max_source_age_seconds: 86_400,
      publish: true,
    }),
    (error: unknown) => error instanceof GrokQaDispatchError,
  );
  assert.throws(
    () => parseGrokQaDispatchAction({
      action: "claim",
      worker_id: WORKER_ID,
      lease_seconds: 300,
      allowed_clients: ["squid", "squid"],
      canary_content_version_id: null,
      max_source_age_seconds: 86_400,
    }),
    (error: unknown) => error instanceof GrokQaDispatchError,
  );
  assert.throws(
    () => parseGrokQaDispatchAction({
      action: "claim",
      worker_id: WORKER_ID,
      lease_seconds: 300,
      allowed_clients: ["squid"],
      canary_content_version_id: null,
      max_source_age_seconds: 60,
    }),
    (error: unknown) => error instanceof GrokQaDispatchError,
  );
  assert.throws(
    () => parseGrokQaDispatchAction({
      action: "fail",
      content_item_id: ITEM_ID,
      content_version_id: VERSION_ID,
      worker_id: WORKER_ID,
      error_code: "attacker_selected_failure",
      retryable: true,
      retry_at: "2026-08-13T08:10:00.000Z",
    }),
    (error: unknown) => error instanceof GrokQaDispatchError,
  );
  assert.throws(
    () => parseGrokQaDispatchAction({
      action: "stage",
      content_item_id: ITEM_ID,
      content_version_id: VERSION_ID,
      worker_id: WORKER_ID,
      verdict: { ...verdict, next_action: "publish_now" },
      model: "grok-4.5",
      prompt_version: "official-x-grok-qa@1",
      provider_response_id: "resp_abc123",
      input_sha256: INPUT_HASH,
      banner_sha256: BANNER_HASH,
      cost_in_usd_ticks: 100_000_000,
      x_search_citations: [SOURCE_URL],
      x_search_calls: 1,
    }),
    (error: unknown) => error instanceof GrokQaDispatchError,
  );
});

test("claim returns only a sanitized review package and a hash-verified inline PNG", async () => {
  const calls: Request[] = [];
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    calls.push(request);
    if (request.url.endsWith("/rpc/claim_grok_qa_dispatch_job")) {
      assert.deepEqual(await request.clone().json(), {
        target_workspace_id: WORKSPACE_ID,
        target_worker_id: WORKER_ID,
        target_lease_seconds: 300,
        target_allowed_clients: ["squid"],
        target_max_source_age_seconds: 86_400,
        target_canary_content_version_id: null,
      });
      return Response.json(claimResult());
    }
    const catalog = await catalogResponse(request);
    if (catalog) return catalog;
    throw new Error(`unexpected request ${request.url}`);
  };
  const result = await executeGrokQaDispatchAction(catalogConfig, parseGrokQaDispatchAction({
    action: "claim",
    worker_id: WORKER_ID,
    lease_seconds: 300,
    allowed_clients: ["squid"],
    canary_content_version_id: null,
    max_source_age_seconds: 86_400,
  }), environment(), fetcher);
  assert.equal((result.review_package as Record<string, unknown>).content_item_id, ITEM_ID);
  assert.deepEqual(result.banner_image, {
    data: Buffer.from(PNG).toString("base64"),
    mime_type: "image/png",
  });
  const serialized = JSON.stringify(result);
  assert.doesNotMatch(serialized, /private raw source|private resolved source|signedURL|short-lived/);
  assert.doesNotMatch(
    serialized,
    /storage_path|source_visual_file|source_visual_cleaned|service-role|token=secret|request_hash/,
  );
  assert.ok(calls.some((request) => request.method === "GET" && request.url.includes("token=short-lived")));
});

test("normal FIFO rejects a stale source before loading its review package", async () => {
  let catalogCalls = 0;
  const action = parseGrokQaDispatchAction({
    action: "claim",
    worker_id: WORKER_ID,
    lease_seconds: 300,
    allowed_clients: ["squid"],
    canary_content_version_id: null,
    max_source_age_seconds: 86_400,
  });

  await assert.rejects(
    () => executeGrokQaDispatchAction(
      catalogConfig,
      action,
      environment(),
      async (input, init) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/rpc/claim_grok_qa_dispatch_job")) {
          return Response.json(claimResult({
            source_published_at: STALE_SOURCE_PUBLISHED_AT,
          }));
        }
        catalogCalls += 1;
        throw new Error(`unexpected request ${request.url}`);
      },
    ),
    (error: unknown) => (error as { code?: unknown }).code
      === "grok_qa_dispatch_source_stale",
  );
  assert.equal(catalogCalls, 0);
});

test("claim binds an optional canary to one exact content version and rejects a mismatched job", async () => {
  const action = parseGrokQaDispatchAction({
    action: "claim",
    worker_id: WORKER_ID,
    lease_seconds: 300,
    allowed_clients: ["squid"],
    canary_content_version_id: VERSION_ID,
    max_source_age_seconds: 86_400,
  });
  const result = await executeGrokQaDispatchAction(
    catalogConfig,
    action,
    environment(),
    async (input, init) => {
      const request = new Request(input, init);
      if (request.url.endsWith("/rpc/claim_grok_qa_dispatch_job")) {
        assert.deepEqual(await request.clone().json(), {
          target_workspace_id: WORKSPACE_ID,
          target_worker_id: WORKER_ID,
          target_lease_seconds: 300,
          target_allowed_clients: ["squid"],
          target_max_source_age_seconds: 86_400,
          target_canary_content_version_id: VERSION_ID,
        });
        return Response.json(claimResult({ source_published_at: STALE_SOURCE_PUBLISHED_AT }));
      }
      const catalog = await catalogResponse(request);
      if (catalog) return catalog;
      throw new Error(`unexpected request ${request.url}`);
    },
  );
  assert.equal((result.job as Record<string, unknown>).content_version_id, VERSION_ID);

  const mismatchedAction = parseGrokQaDispatchAction({
    action: "claim",
    worker_id: WORKER_ID,
    lease_seconds: 300,
    allowed_clients: ["squid"],
    canary_content_version_id: OTHER_VERSION_ID,
    max_source_age_seconds: 86_400,
  });
  await assert.rejects(
    () => executeGrokQaDispatchAction(
      catalogConfig,
      mismatchedAction,
      environment(),
      async () => Response.json(claimResult()),
    ),
    (error: unknown) => (error as { code?: unknown }).code
      === "grok_qa_dispatch_invalid_response",
  );
});

test("exact canary permits an expired source but rejects future clock skew", async () => {
  let catalogCalls = 0;
  const action = parseGrokQaDispatchAction({
    action: "claim",
    worker_id: WORKER_ID,
    lease_seconds: 300,
    allowed_clients: ["squid"],
    canary_content_version_id: VERSION_ID,
    max_source_age_seconds: 86_400,
  });

  await assert.rejects(
    () => executeGrokQaDispatchAction(
      catalogConfig,
      action,
      environment(),
      async (input, init) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/rpc/claim_grok_qa_dispatch_job")) {
          return Response.json(claimResult({
            source_published_at: FUTURE_SOURCE_PUBLISHED_AT,
          }));
        }
        catalogCalls += 1;
        throw new Error(`unexpected request ${request.url}`);
      },
    ),
    (error: unknown) => (error as { code?: unknown }).code
      === "grok_qa_dispatch_source_stale",
  );
  assert.equal(catalogCalls, 0);
});

test("stage accepts only verdict source URLs from the stored review package", async () => {
  let stageCalls = 0;
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const catalog = await catalogResponse(request);
    if (catalog) return catalog;
    if (request.url.endsWith("/rpc/stage_grok_qa_dispatch_verdict")) {
      stageCalls += 1;
      const body = await request.clone().json() as Record<string, unknown>;
      assert.equal(body.target_model, "grok-4.5");
      assert.equal(body.target_prompt_version, "official-x-grok-qa@1");
      assert.equal(body.target_provider_response_id, "resp_abc123");
      assert.equal(body.target_input_sha256, INPUT_HASH);
      assert.equal(body.target_banner_sha256, BANNER_HASH);
      assert.equal(body.target_cost_in_usd_ticks, 100_000_000);
      assert.deepEqual(body.target_x_search_citations, [SOURCE_URL]);
      assert.equal(body.target_x_search_calls, 1);
      return Response.json({
        schema_version: "1.0",
        content_item_id: ITEM_ID,
        content_version_id: VERSION_ID,
        status: "claimed",
        verdict_sha256: VERDICT_HASH,
        model: "grok-4.5",
        prompt_version: "official-x-grok-qa@1",
        provider_response_id: "resp_abc123",
        input_sha256: INPUT_HASH,
        banner_sha256: BANNER_HASH,
        cost_in_usd_ticks: 100_000_000,
        x_search_citations: [SOURCE_URL],
        x_search_calls: 1,
        reused: false,
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  const result = await executeGrokQaDispatchAction(
    catalogConfig,
    stageAction(),
    environment(),
    fetcher,
  );
  assert.equal(result.verdict_sha256, VERDICT_HASH);
  assert.equal(stageCalls, 1);

  const injected = structuredClone(verdict);
  injected.fact_check.source_urls.push("https://attacker.example/fabricated-proof");
  await assert.rejects(
    () => executeGrokQaDispatchAction(catalogConfig, parseGrokQaDispatchAction({
      action: "stage",
      content_item_id: ITEM_ID,
      content_version_id: VERSION_ID,
      worker_id: WORKER_ID,
      verdict: injected,
      model: "grok-4.5",
      prompt_version: "official-x-grok-qa@1",
      provider_response_id: "resp_abc123",
      input_sha256: INPUT_HASH,
      banner_sha256: BANNER_HASH,
      cost_in_usd_ticks: 100_000_000,
      x_search_citations: [SOURCE_URL],
      x_search_calls: 1,
    }), environment(), fetcher),
    (error: unknown) => (error as { code?: unknown }).code === "grok_qa_dispatch_source_mismatch",
  );
  assert.equal(stageCalls, 1);

  const injectedCitations = stageAction() as Record<string, unknown>;
  injectedCitations.x_search_citations = [
    SOURCE_URL,
    "https://x.com/attacker/status/2083266484789514640",
  ];
  await assert.rejects(
    () => executeGrokQaDispatchAction(
      catalogConfig,
      injectedCitations as never,
      environment(),
      fetcher,
    ),
    (error: unknown) => (error as { code?: unknown }).code === "grok_qa_dispatch_source_mismatch",
  );
  assert.equal(stageCalls, 1);
});

test("provider PASS cannot downgrade a stored critical brand review or reach stage and relay", async () => {
  const criticalDetail = rawDetail();
  const criticalBrandQa = {
    schema_version: "1.0",
    policy_version: "brand-qa@1",
    client_id: "squid",
    content_kind: "daily_news",
    status: "review",
    score: 50,
    human_review_required: true,
    checks: [{
      id: "visual_integrity",
      status: "review",
      severity: "critical",
      label: "Squid 원본 구도·자막",
      detail: "원문 자막 픽셀을 깔끔하게 제거하지 못했습니다.",
    }],
  };
  criticalDetail.generation_meta.brand_qa = criticalBrandQa;
  criticalDetail.current_version.generation_meta.brand_qa = criticalBrandQa;
  let stageCalls = 0;
  let receiptCalls = 0;
  let relayCalls = 0;

  await assert.rejects(
    () => executeGrokQaDispatchAction(
      catalogConfig,
      stageAction("deliver"),
      environment(),
      async (input, init) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/rest/v1/rpc/get_content_library_item")) {
          return Response.json(criticalDetail);
        }
        if (
          request.method === "POST"
          && request.url.includes("/storage/v1/object/sign/content-studio/")
        ) {
          return Response.json({ signedURL: signedPath() });
        }
        if (request.url.endsWith("/rpc/stage_grok_qa_dispatch_verdict")) {
          stageCalls += 1;
          return Response.json({});
        }
        if (request.url.endsWith("/rpc/claim_grok_qa_verdict")) {
          receiptCalls += 1;
          return Response.json({});
        }
        if (request.url === "https://content-engine.example/internal/grok-qa-verdict") {
          relayCalls += 1;
          return Response.json({ sent: true });
        }
        throw new Error(`unexpected request ${request.url}`);
      },
    ),
    (error: unknown) => (error as { code?: unknown }).code
      === "grok_qa_dispatch_brand_qa_conflict",
  );
  assert.equal(stageCalls, 0);
  assert.equal(receiptCalls, 0);
  assert.equal(relayCalls, 0);
});

test("provider attempt fence is a one-shot RPC authorization bound to the exact input hash", async () => {
  let calls = 0;
  const action = parseGrokQaDispatchAction({
    action: "mark_provider_attempt",
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    worker_id: WORKER_ID,
    input_sha256: INPUT_HASH,
    banner_sha256: BANNER_HASH,
  });
  const result = await executeGrokQaDispatchAction(
    catalogConfig,
    action,
    environment(),
    async (input, init) => {
      const request = new Request(input, init);
      calls += 1;
      assert.match(request.url, /\/rpc\/mark_grok_qa_dispatch_provider_attempt$/);
      assert.deepEqual(await request.clone().json(), {
        target_workspace_id: WORKSPACE_ID,
        target_content_version_id: VERSION_ID,
        target_worker_id: WORKER_ID,
        target_input_sha256: INPUT_HASH,
        target_banner_sha256: BANNER_HASH,
      });
      return Response.json({
        schema_version: "1.0",
        authorized_once: true,
        content_item_id: ITEM_ID,
        content_version_id: VERSION_ID,
        input_sha256: INPUT_HASH,
        banner_sha256: BANNER_HASH,
        provider_attempt_started_at: new Date().toISOString(),
      });
    },
  );
  assert.equal(result.authorized_once, true);
  assert.equal(calls, 1);

  await assert.rejects(
    () => executeGrokQaDispatchAction(
      catalogConfig,
      action,
      environment(),
      async () => Response.json({
        schema_version: "1.0",
        authorized_once: false,
        content_item_id: ITEM_ID,
        content_version_id: VERSION_ID,
        input_sha256: INPUT_HASH,
        banner_sha256: BANNER_HASH,
        provider_attempt_started_at: "2026-08-13T08:01:00.000Z",
      }),
    ),
    (error: unknown) => (error as { code?: unknown }).code
      === "grok_qa_dispatch_invalid_response",
  );

  await assert.rejects(
    () => executeGrokQaDispatchAction(
      catalogConfig,
      action,
      environment(),
      async () => { throw new Error("mark response lost"); },
    ),
    (error: unknown) => (error as { code?: unknown }).code
      === "grok_qa_dispatch_storage_unavailable",
  );
});

test("deliver exact-replays the staged verdict, uses only the dedicated relay, and completes sent", async () => {
  const rpcNames: string[] = [];
  let relayCalls = 0;
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const catalog = await catalogResponse(request);
    if (catalog) return catalog;
    const rpcName = request.url.split("/rpc/")[1];
    if (rpcName) rpcNames.push(rpcName);
    if (request.url.endsWith("/rpc/stage_grok_qa_dispatch_verdict")) {
      const body = await request.clone().json() as Record<string, unknown>;
      assert.deepEqual(body.target_verdict, verdict);
      return Response.json({
        schema_version: "1.0", content_item_id: ITEM_ID, content_version_id: VERSION_ID,
        status: "claimed", verdict_sha256: VERDICT_HASH, reused: true,
        model: "grok-4.5", prompt_version: "official-x-grok-qa@1",
        provider_response_id: "resp_abc123", input_sha256: INPUT_HASH,
        banner_sha256: BANNER_HASH,
        cost_in_usd_ticks: 100_000_000, x_search_citations: [SOURCE_URL], x_search_calls: 1,
      });
    }
    if (request.url.endsWith("/rpc/claim_grok_qa_verdict")) {
      return Response.json({
        claimed: true, status: "claimed", payload_sha256: VERDICT_HASH, decision: "PASS",
      });
    }
    if (request.url === "https://content-engine.example/internal/grok-qa-verdict") {
      relayCalls += 1;
      assert.equal(request.headers.get("x-grok-qa-relay-token"), RELAY_TOKEN);
      assert.equal(request.headers.get("x-api-key"), null);
      const body = await request.clone().json() as Record<string, unknown>;
      assert.equal(body.content_item_id, ITEM_ID);
      assert.equal(body.title, "Squid 한국어 뉴스");
      assert.equal(body.review_url, `https://coineasy-newscard.netlify.app/?view=library&content=${ITEM_ID}`);
      assert.equal(body.decision, "PASS");
      assert.equal(body.image_data_url, `data:image/png;base64,${Buffer.from(PNG).toString("base64")}`);
      assert.doesNotMatch(JSON.stringify(body), /chat_id|typefully|publish|approve|service-role/);
      return Response.json({ sent: true });
    }
    if (request.url.endsWith("/rpc/finalize_grok_qa_verdict")) {
      return Response.json({ status: "sent" });
    }
    if (request.url.endsWith("/rpc/complete_grok_qa_dispatch_job")) {
      const body = await request.clone().json() as Record<string, unknown>;
      assert.equal(body.target_outcome, "sent");
      assert.equal(body.target_error_code, null);
      return Response.json({
        schema_version: "1.0", content_item_id: ITEM_ID, content_version_id: VERSION_ID,
        status: "sent", reused: false,
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  const result = await executeGrokQaDispatchAction(
    catalogConfig,
    stageAction("deliver"),
    environment(),
    fetcher,
  );
  assert.equal(result.delivered, true);
  assert.equal(result.accepted, true);
  assert.equal(result.delivery_status, "sent");
  assert.equal(result.advisory_only, true);
  assert.equal(result.public_publish, false);
  assert.equal(relayCalls, 1);
  assert.deepEqual(rpcNames, [
    "stage_grok_qa_dispatch_verdict",
    "claim_grok_qa_verdict",
    "finalize_grok_qa_verdict",
    "complete_grok_qa_dispatch_job",
  ]);
});

test("deliver rejects a post-review banner swap before claiming a relay receipt", async () => {
  let receiptCalls = 0;
  let relayCalls = 0;
  const changed = new Uint8Array(PNG);
  changed[changed.length - 1] ^= 0xff;
  await assert.rejects(
    () => executeGrokQaDispatchAction(
      catalogConfig,
      stageAction("deliver"),
      environment(),
      async (input, init) => {
        const request = new Request(input, init);
        if (request.url.endsWith("/rpc/stage_grok_qa_dispatch_verdict")) {
          return Response.json({
            schema_version: "1.0", content_item_id: ITEM_ID,
            content_version_id: VERSION_ID, status: "claimed",
            verdict_sha256: VERDICT_HASH, reused: true, model: "grok-4.5",
            prompt_version: "official-x-grok-qa@1",
            provider_response_id: "resp_abc123", input_sha256: INPUT_HASH,
            banner_sha256: BANNER_HASH, cost_in_usd_ticks: 100_000_000,
            x_search_citations: [SOURCE_URL], x_search_calls: 1,
          });
        }
        if (request.url.endsWith("/rpc/claim_grok_qa_verdict")) {
          receiptCalls += 1;
          return Response.json({ claimed: true });
        }
        if (request.url.includes("/internal/grok-qa-verdict")) {
          relayCalls += 1;
          return Response.json({ sent: true });
        }
        if (
          request.method === "GET"
          && request.url.includes("/storage/v1/object/sign/content-studio/")
        ) {
          return new Response(changed, {
            headers: {
              "Content-Type": "image/png",
              "Content-Length": String(changed.byteLength),
            },
          });
        }
        const catalog = await catalogResponse(request);
        if (catalog) return catalog;
        throw new Error(`unexpected request ${request.url}`);
      },
    ),
    (error: unknown) => (error as { code?: unknown }).code
      === "grok_qa_dispatch_banner_conflict",
  );
  assert.equal(receiptCalls, 0);
  assert.equal(relayCalls, 0);
});

test("already-sent receipt completes idempotently without a second Telegram relay", async () => {
  let relayCalls = 0;
  let finalizeCalls = 0;
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const catalog = await catalogResponse(request);
    if (catalog) return catalog;
    if (request.url.endsWith("/rpc/stage_grok_qa_dispatch_verdict")) {
      return Response.json({
        schema_version: "1.0", content_item_id: ITEM_ID, content_version_id: VERSION_ID,
        status: "claimed", verdict_sha256: VERDICT_HASH, reused: true,
        model: "grok-4.5", prompt_version: "official-x-grok-qa@1",
        provider_response_id: "resp_abc123", input_sha256: INPUT_HASH,
        banner_sha256: BANNER_HASH,
        cost_in_usd_ticks: 100_000_000, x_search_citations: [SOURCE_URL], x_search_calls: 1,
      });
    }
    if (request.url.endsWith("/rpc/claim_grok_qa_verdict")) {
      return Response.json({
        claimed: false, status: "sent", payload_sha256: VERDICT_HASH, decision: "PASS",
      });
    }
    if (request.url.includes("/internal/grok-qa-verdict")) {
      relayCalls += 1;
      return Response.json({ sent: true });
    }
    if (request.url.endsWith("/rpc/finalize_grok_qa_verdict")) {
      finalizeCalls += 1;
      return Response.json({ status: "sent" });
    }
    if (request.url.endsWith("/rpc/complete_grok_qa_dispatch_job")) {
      return Response.json({
        schema_version: "1.0", content_item_id: ITEM_ID, content_version_id: VERSION_ID,
        status: "sent", reused: true,
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  const result = await executeGrokQaDispatchAction(
    catalogConfig,
    stageAction("deliver"),
    environment(),
    fetcher,
  );
  assert.equal(result.delivered, true);
  assert.equal(result.duplicate, true);
  assert.equal(result.accepted, true);
  assert.equal(result.delivery_status, "duplicate");
  assert.equal(relayCalls, 0);
  assert.equal(finalizeCalls, 0);
});

test("post-relay timeout becomes delivery_unknown with no finalize and no retry path", async () => {
  let finalizeCalls = 0;
  let failCalls = 0;
  const fetcher = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    const catalog = await catalogResponse(request);
    if (catalog) return catalog;
    if (request.url.endsWith("/rpc/stage_grok_qa_dispatch_verdict")) {
      return Response.json({
        schema_version: "1.0", content_item_id: ITEM_ID, content_version_id: VERSION_ID,
        status: "claimed", verdict_sha256: VERDICT_HASH, reused: true,
        model: "grok-4.5", prompt_version: "official-x-grok-qa@1",
        provider_response_id: "resp_abc123", input_sha256: INPUT_HASH,
        banner_sha256: BANNER_HASH,
        cost_in_usd_ticks: 100_000_000, x_search_citations: [SOURCE_URL], x_search_calls: 1,
      });
    }
    if (request.url.endsWith("/rpc/claim_grok_qa_verdict")) {
      return Response.json({
        claimed: true, status: "claimed", payload_sha256: VERDICT_HASH, decision: "PASS",
      });
    }
    if (request.url.includes("/internal/grok-qa-verdict")) {
      throw new Error("response lost after relay accepted request");
    }
    if (request.url.endsWith("/rpc/finalize_grok_qa_verdict")) {
      finalizeCalls += 1;
      return Response.json({ status: "failed" });
    }
    if (request.url.endsWith("/rpc/fail_grok_qa_dispatch_job")) {
      failCalls += 1;
      return Response.json({});
    }
    if (request.url.endsWith("/rpc/complete_grok_qa_dispatch_job")) {
      const body = await request.clone().json() as Record<string, unknown>;
      assert.equal(body.target_outcome, "delivery_unknown");
      assert.equal(body.target_error_code, "qa_delivery_state_unknown");
      return Response.json({
        schema_version: "1.0", content_item_id: ITEM_ID, content_version_id: VERSION_ID,
        status: "delivery_unknown", reused: false,
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  const result = await executeGrokQaDispatchAction(
    catalogConfig,
    stageAction("deliver"),
    environment(),
    fetcher,
  );
  assert.equal(result.status, "delivery_unknown");
  assert.equal(result.delivered, false);
  assert.equal(finalizeCalls, 0);
  assert.equal(failCalls, 0);
});

test("relay config rejects shared secrets and sends only the dedicated relay header", async () => {
  assert.deepEqual(grokQaRelayConfig(environment()), {
    railwayUrl: "https://content-engine.example",
    relayToken: RELAY_TOKEN,
  });
  assert.equal(grokQaRelayConfig(environment({ API_SECRET: RELAY_TOKEN })), null);
  assert.equal(grokQaRelayConfig(environment({ GROK_QA_DISPATCH_TOKEN: RELAY_TOKEN })), null);
  assert.equal(grokQaRelayConfig(environment({ XAI_API_KEY: RELAY_TOKEN })), null);

  let requestSeen: Request | null = null;
  const outcome = await sendGrokQaVerdictOutcome(
    grokQaRelayConfig(environment())!,
    detailForRelay(),
    verdict,
    `https://coineasy-newscard.netlify.app/?view=library&content=${ITEM_ID}`,
    async (input, init) => {
      requestSeen = new Request(input, init);
      if (requestSeen.url.includes("/storage/v1/object/sign/content-studio/")) {
        return new Response(PNG, {
          headers: {
            "Content-Type": "image/png",
            "Content-Length": String(PNG.byteLength),
          },
        });
      }
      return Response.json({ sent: true });
    },
  );
  assert.equal(outcome, "sent");
  assert.equal(requestSeen!.headers.get("x-grok-qa-relay-token"), RELAY_TOKEN);
  assert.equal(requestSeen!.headers.get("x-api-key"), null);
  const requestBody = await requestSeen!.clone().json() as Record<string, unknown>;
  assert.equal(
    requestBody.image_data_url,
    `data:image/png;base64,${Buffer.from(PNG).toString("base64")}`,
  );

  const unknown = await sendGrokQaVerdictOutcome(
    grokQaRelayConfig(environment())!,
    detailForRelay(),
    verdict,
    `https://coineasy-newscard.netlify.app/?view=library&content=${ITEM_ID}`,
    async (input) => String(input).includes("/storage/v1/object/sign/content-studio/")
      ? new Response(PNG, { headers: { "Content-Type": "image/png" } })
      : new Response(null, { status: 504 }),
  );
  const rejected = await sendGrokQaVerdictOutcome(
    grokQaRelayConfig(environment())!,
    detailForRelay(),
    verdict,
    `https://coineasy-newscard.netlify.app/?view=library&content=${ITEM_ID}`,
    async (input) => String(input).includes("/storage/v1/object/sign/content-studio/")
      ? new Response(PNG, { headers: { "Content-Type": "image/png" } })
      : new Response(null, { status: 502 }),
  );
  assert.equal(unknown, "delivery_unknown");
  assert.equal(rejected, "failed");
});

test("relay fails closed before Railway when the exact banner hash changes", async () => {
  let railwayCalls = 0;
  const result = await sendGrokQaVerdictOutcome(
    grokQaRelayConfig(environment())!,
    detailForRelay(),
    verdict,
    `https://coineasy-newscard.netlify.app/?view=library&content=${ITEM_ID}`,
    async (input) => {
      if (String(input).includes("/internal/grok-qa-verdict")) {
        railwayCalls += 1;
        return Response.json({ sent: true });
      }
      const changed = new Uint8Array(PNG);
      changed[changed.length - 1] ^= 0xff;
      return new Response(changed, { headers: { "Content-Type": "image/png" } });
    },
  );
  assert.equal(result, "failed");
  assert.equal(railwayCalls, 0);
});
