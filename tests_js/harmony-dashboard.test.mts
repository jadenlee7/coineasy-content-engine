import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import type { Context } from "@netlify/functions";

import { handleHarmonyDashboard } from "../netlify/functions/harmony-dashboard.mts";
import {
  getHarmonyDashboard,
  harmonyDashboardConfig,
  harmonyDashboardPreviewCommitMatches,
  harmonyDashboardPreviewEnabled,
  harmonyDashboardPreviewOrigin,
  HarmonyDashboardError,
  normalizeHarmonyDashboard,
} from "../netlify/functions/_shared/harmony-dashboard.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "a0000000-0000-4000-8000-000000000001";
const ROUND_ID = "b0000000-0000-4000-8000-000000000001";
const PLAN_ID = "c0000000-0000-4000-8000-000000000001";
const INBOX_ID = "d0000000-0000-4000-8000-000000000001";
const QA_RECEIPT_ID = "e0000000-0000-4000-8000-000000000001";
const PROJECT_REF = "previewprojectref";
const PREVIEW_ORIGIN = "https://deploy-preview-200--coineasy-newscard.netlify.app";
const PRODUCTION_ORIGIN = "https://coineasy-newscard.netlify.app";
const STUDIO_TOKEN = "studio-preview-access-token";
const PUBLISHABLE_KEY = "sb_publishable_" + "p".repeat(32);
const COMMIT_SHA = "1".repeat(40);

function jwt(payload: Record<string, unknown>): string {
  const header = Buffer.from(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .toString("base64url");
  const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
  return header + "." + body + "." + "s".repeat(43);
}

function scopedKey(overrides: Record<string, unknown> = {}): string {
  return jwt({
    iss: "supabase",
    aud: "authenticated",
    ref: PROJECT_REF,
    role: "coineasy_harmony_dashboard",
    workspace_id: WORKSPACE_ID,
    client_id: "squid",
    environment: "preview",
    iat: Math.floor(Date.now() / 1_000),
    exp: Math.floor(Date.now() / 1_000) + 3_600,
    automatic_publication: false,
    max_cost_microusd: 0,
    max_external_actions: 0,
    ...overrides,
  });
}

function environment(
  overrides: Record<string, string | undefined> = {},
): Record<string, string> {
  const values: Record<string, string | undefined> = {
    HARMONY_DASHBOARD_EXPECTED_COMMIT_SHA: COMMIT_SHA,
    HARMONY_DASHBOARD_PREVIEW_ENABLED: "true",
    STUDIO_ACCESS_TOKEN: STUDIO_TOKEN,
    SUPABASE_URL: "https://" + PROJECT_REF + ".supabase.co",
    SUPABASE_PUBLISHABLE_KEY: PUBLISHABLE_KEY,
    SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey(),
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    ...overrides,
  };
  return Object.fromEntries(
    Object.entries(values).filter((entry): entry is [string, string] =>
      typeof entry[1] === "string"
    ),
  );
}

function runtimeContext(
  overrides: {
    deployContext?: string;
    published?: boolean;
    siteName?: string;
    siteUrl?: string;
  } = {},
): Context {
  return {
    deploy: {
      context: overrides.deployContext ?? "deploy-preview",
      published: overrides.published ?? false,
    },
    site: {
      name: overrides.siteName ?? "coineasy-newscard",
      url: overrides.siteUrl ?? PRODUCTION_ORIGIN,
    },
  } as unknown as Context;
}

function harmonyDashboardHandler(
  targetRequest: Request,
  context: Context = runtimeContext(),
  buildReleaseSha: string | null = COMMIT_SHA,
): Promise<Response> {
  return handleHarmonyDashboard(targetRequest, context, buildReleaseSha);
}

async function withGlobals<T>(
  values: Record<string, string>,
  fetcher: typeof fetch,
  callback: () => Promise<T>,
): Promise<T> {
  const netlifyDescriptor = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const originalFetch = globalThis.fetch;
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: (name: string) => values[name] } },
  });
  globalThis.fetch = fetcher;
  try {
    return await callback();
  } finally {
    globalThis.fetch = originalFetch;
    if (netlifyDescriptor) {
      Object.defineProperty(globalThis, "Netlify", netlifyDescriptor);
    } else {
      delete (globalThis as Record<string, unknown>).Netlify;
    }
  }
}

function request(
  method = "GET",
  origin = PREVIEW_ORIGIN,
  authenticated = true,
): Request {
  const headers = new Headers();
  if (authenticated) {
    headers.set(
      "Cookie",
      STUDIO_SESSION_COOKIE + "=" + createStudioSessionValue(STUDIO_TOKEN),
    );
  }
  return new Request(origin + "/api/harmony/dashboard", { method, headers });
}

function dashboard(): Record<string, any> {
  const stages = [
    "plan",
    "private_content",
    "independent_qa",
    "operator_inbox",
    "recap",
  ].map((stage, index) => ({
    stage,
    ordinal: index + 1,
    receipt_sha256: String(index + 1).repeat(64),
    input_sha256: "a".repeat(64),
    output_sha256: index === 2 ? "c".repeat(64) : "b".repeat(64),
    recorded_at: "2026-08-25T10:0" + index + ":00Z",
    verdict: index === 2 ? "passed" : null,
  }));
  return {
    schema_version: "harmony-preview-dashboard@1",
    workspace_id: WORKSPACE_ID,
    client_id: "squid",
    observed_at: "2026-08-25T10:10:00Z",
    counts: {
      signals: 4,
      connector_receipts: 4,
      rounds: 1,
      plans: 1,
      stage_receipts: 5,
      pending_operator_inbox: 1,
    },
    latest_round: {
      schema_version: "harmony-dashboard-round@1",
      round_id: ROUND_ID,
      plan_id: PLAN_ID,
      input_set_sha256: "d".repeat(64),
      round_sha256: "e".repeat(64),
      status: "operator_review_pending",
      headline_ko: "Squid 한국 커뮤니티 첫 협업 라운드",
      summary_ko: "집계 신호와 공식 근거를 분리하고 독립 QA를 통과한 Preview 제안입니다.",
      stages,
      automatic_publication: false,
    },
    operator_inbox: [{
      schema_version: "harmony-dashboard-inbox@1",
      inbox_id: INBOX_ID,
      round_id: ROUND_ID,
      plan_id: PLAN_ID,
      status: "pending",
      scope_sha256: "f".repeat(64),
      qa_receipt_id: QA_RECEIPT_ID,
      qa_receipt_sha256: "3".repeat(64),
      qa_output_sha256: "c".repeat(64),
      created_at: "2026-08-25T10:03:00Z",
      automatic_publication: false,
    }],
    trust: {
      environment: "preview",
      client_scope_verified: true,
      portable_trust: false,
    },
    flags: {
      read_only: true,
      external_calls: false,
      provider_calls: false,
      publication_calls: false,
      automatic_publication: false,
    },
  };
}

const forbiddenFetch: typeof fetch = async () => {
  throw new Error("fetch must not be called");
};

test("Preview flag is default-off and accepts only literal true", () => {
  assert.equal(harmonyDashboardPreviewEnabled(() => undefined), false);
  assert.equal(harmonyDashboardPreviewEnabled(() => "false"), false);
  assert.equal(harmonyDashboardPreviewEnabled(() => "TRUE"), false);
  assert.equal(harmonyDashboardPreviewEnabled(() => "true"), true);
});

test("deployment guard requires deploy-preview and distinct canonical origins", () => {
  assert.equal(
    harmonyDashboardPreviewOrigin(
      PREVIEW_ORIGIN + "/api/harmony/dashboard",
      runtimeContext(),
    ),
    PREVIEW_ORIGIN,
  );
  assert.equal(
    harmonyDashboardPreviewOrigin(
      PREVIEW_ORIGIN + "/api/harmony/dashboard",
      runtimeContext({ deployContext: "production" }),
    ),
    null,
  );
  assert.equal(
    harmonyDashboardPreviewOrigin(
      PRODUCTION_ORIGIN + "/api/harmony/dashboard",
      runtimeContext(),
    ),
    null,
  );
  assert.equal(
    harmonyDashboardPreviewOrigin(
      "https://attacker.example/api/harmony/dashboard",
      runtimeContext(),
    ),
    null,
  );
  assert.equal(
    harmonyDashboardPreviewOrigin(
      PREVIEW_ORIGIN + "/api/harmony/dashboard",
      runtimeContext({ published: true }),
    ),
    null,
  );
  assert.equal(
    harmonyDashboardPreviewOrigin(
      PREVIEW_ORIGIN + "/api/harmony/dashboard",
      runtimeContext({ siteUrl: "https://example.com" }),
    ),
    PREVIEW_ORIGIN,
  );
  assert.equal(
    harmonyDashboardPreviewOrigin(
      PREVIEW_ORIGIN + "/api/harmony/dashboard",
      runtimeContext({ siteName: "attacker" }),
    ),
    null,
  );
});

test("deployment guard requires the exact approved commit SHA", () => {
  assert.equal(
    harmonyDashboardPreviewCommitMatches(
      (name) => environment()[name],
      COMMIT_SHA,
    ),
    true,
  );
  for (const [values, buildReleaseSha] of [
    [environment(), null],
    [environment({ HARMONY_DASHBOARD_EXPECTED_COMMIT_SHA: undefined }), COMMIT_SHA],
    [environment(), "2".repeat(40)],
    [environment({ HARMONY_DASHBOARD_EXPECTED_COMMIT_SHA: "main" }), COMMIT_SHA],
  ] as const) {
    assert.equal(
      harmonyDashboardPreviewCommitMatches(
        (name) => values[name],
        buildReleaseSha,
      ),
      false,
    );
  }
});

test("HTTP adapter blocks method, Production, disabled flag, and wrong host before I/O", async () => {
  await withGlobals(environment(), forbiddenFetch, async () => {
    const response = await harmonyDashboardHandler(request("POST"));
    assert.equal(response.status, 405);
    assert.equal(response.headers.get("allow"), "GET");
    assert.equal(response.headers.get("cache-control"), "no-store");
  });
  await withGlobals(environment(), forbiddenFetch, async () => {
    const response = await harmonyDashboardHandler(
      request(),
      runtimeContext({ deployContext: "production" }),
    );
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), { error: "harmony_dashboard_preview_only" });
  });
  await withGlobals(environment(), forbiddenFetch, async () => {
    const response = await harmonyDashboardHandler(
      request(),
      runtimeContext(),
      "2".repeat(40),
    );
    assert.equal(response.status, 409);
    assert.deepEqual(await response.json(), {
      error: "harmony_dashboard_preview_commit_mismatch",
    });
  });
  await withGlobals(environment(), forbiddenFetch, async () => {
    assert.equal(
      (await harmonyDashboardHandler(request("GET", PRODUCTION_ORIGIN))).status,
      403,
    );
  });
  await withGlobals(environment({ HARMONY_DASHBOARD_PREVIEW_ENABLED: undefined }), forbiddenFetch, async () => {
    const response = await harmonyDashboardHandler(request());
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "harmony_dashboard_preview_disabled" });
  });
  await withGlobals(environment(), forbiddenFetch, async () => {
    const response = await harmonyDashboardHandler(
      request("GET", "https://attacker.example"),
    );
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), {
      error: "harmony_dashboard_preview_only",
    });
  });
});

test("HTTP adapter requires the signed Studio session before storage", async () => {
  await withGlobals(environment(), forbiddenFetch, async () => {
    const response = await harmonyDashboardHandler(
      request("GET", PREVIEW_ORIGIN, false),
    );
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: "studio_auth_required" });
  });
});

test("config requires publishable apikey and exact scoped JWT without service fallback", () => {
  const good = environment();
  const config = harmonyDashboardConfig((name) => good[name]);
  assert.ok(config);
  assert.equal(config.projectKey, PUBLISHABLE_KEY);
  assert.equal(config.authorizationKey, good.SUPABASE_HARMONY_DASHBOARD_KEY);
  assert.equal(config.clientId, "squid");

  for (const bad of [
    environment({ SUPABASE_HARMONY_DASHBOARD_KEY: undefined }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: undefined,
      SUPABASE_SERVICE_ROLE_KEY: scopedKey(),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ role: "service_role" }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ client_id: "yellow" }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ environment: "production" }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ exp: 1 }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ iss: "attacker" }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ aud: "anon" }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({
        iat: Math.floor(Date.now() / 1_000) + 61,
      }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({
        exp: Math.floor(Date.now() / 1_000) + 2_678_401,
      }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ automatic_publication: true }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ max_cost_microusd: 1 }),
    }),
    environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: scopedKey({ max_external_actions: 1 }),
    }),
    environment({
      SUPABASE_PUBLISHABLE_KEY: jwt({ role: "service_role" }),
    }),
  ]) {
    assert.equal(harmonyDashboardConfig((name) => bad[name]), null);
  }
});

test("invalid scoped claims fail before Supabase fetch", async () => {
  const invalidKeys = [
    scopedKey({ iss: "attacker" }),
    scopedKey({ aud: "anon" }),
    scopedKey({ client_id: "yellow" }),
    scopedKey({ environment: "production" }),
    scopedKey({ automatic_publication: true }),
    scopedKey({ max_cost_microusd: 1 }),
    scopedKey({ max_external_actions: 1 }),
  ];
  let fetchCalls = 0;
  const fetcher: typeof fetch = async () => {
    fetchCalls += 1;
    throw new Error("invalid claims must not reach storage");
  };
  for (const authorizationKey of invalidKeys) {
    await withGlobals(environment({
      SUPABASE_HARMONY_DASHBOARD_KEY: authorizationKey,
    }), fetcher, async () => {
      const response = await harmonyDashboardHandler(request());
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), {
        error: "harmony_dashboard_not_configured",
      });
    });
  }
  assert.equal(fetchCalls, 0);
});

test("Supabase read uses GET with publishable apikey and scoped JWT bearer", async () => {
  const values = environment();
  const config = harmonyDashboardConfig((name) => values[name]);
  assert.ok(config);
  let calls = 0;
  const result = await getHarmonyDashboard(config, async (input, init) => {
    calls += 1;
    const url = new URL(input);
    assert.equal(url.pathname, "/rest/v1/rpc/get_preview_harmony_dashboard");
    assert.equal(url.searchParams.get("target_workspace_id"), WORKSPACE_ID);
    assert.equal(url.searchParams.get("target_client_id"), "squid");
    assert.equal(init?.method, "GET");
    assert.equal(init?.body, undefined);
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("apikey"), PUBLISHABLE_KEY);
    assert.equal(
      headers.get("authorization"),
      "Bearer " + values.SUPABASE_HARMONY_DASHBOARD_KEY,
    );
    return Response.json(dashboard());
  });
  assert.equal(calls, 1);
  assert.equal(result.schema_version, "harmony-preview-dashboard@1");
  assert.equal(result.latest_round?.stages.length, 5);
  assert.equal(result.operator_inbox.length, 1);
  assert.equal(
    JSON.stringify(result).includes(values.SUPABASE_HARMONY_DASHBOARD_KEY),
    false,
  );
});

test("handler returns exact safe dashboard and never caches it", async () => {
  const values = environment();
  await withGlobals(values, async () => Response.json(dashboard()), async () => {
    const response = await harmonyDashboardHandler(request());
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.equal(response.headers.get("vary"), "Cookie");
    assert.deepEqual(await response.json(), dashboard());
  });
});

test("projection accepts newest-first pending inbox items from different rounds", () => {
  const value = dashboard();
  const olderInbox = {
    ...value.operator_inbox[0],
    inbox_id: "d0000000-0000-4000-8000-000000000002",
    round_id: "b0000000-0000-4000-8000-000000000002",
    plan_id: "c0000000-0000-4000-8000-000000000002",
    qa_receipt_id: "e0000000-0000-4000-8000-000000000002",
    qa_receipt_sha256: "4".repeat(64),
    qa_output_sha256: "5".repeat(64),
    created_at: "2026-08-25T09:03:00Z",
  };
  value.operator_inbox = [value.operator_inbox[0], olderInbox];
  value.counts = {
    ...value.counts,
    signals: 8,
    connector_receipts: 8,
    rounds: 2,
    plans: 2,
    stage_receipts: 10,
    pending_operator_inbox: 2,
  };

  const normalized = normalizeHarmonyDashboard(value, {
    workspaceId: WORKSPACE_ID,
    clientId: "squid",
  });
  assert.equal(normalized.operator_inbox.length, 2);
  assert.equal(normalized.operator_inbox[1].round_id, olderInbox.round_id);
});

test("projection rejects drift, unsafe content, and broken QA bindings", () => {
  const config = { workspaceId: WORKSPACE_ID, clientId: "squid" as const };
  const cases = [
    { ...dashboard(), extra: true },
    {
      ...dashboard(),
      flags: { ...dashboard().flags, automatic_publication: true },
    },
    {
      ...dashboard(),
      latest_round: {
        ...dashboard().latest_round,
        summary_ko: "Bearer " + "secret".repeat(10),
      },
    },
    {
      ...dashboard(),
      latest_round: {
        ...dashboard().latest_round,
        stages: [...dashboard().latest_round.stages].reverse(),
      },
    },
    {
      ...dashboard(),
      operator_inbox: [{
        ...dashboard().operator_inbox[0],
        qa_receipt_sha256: "0".repeat(64),
      }],
    },
    {
      ...dashboard(),
      operator_inbox: [],
      counts: {
        ...dashboard().counts,
        pending_operator_inbox: 0,
      },
    },
    {
      ...dashboard(),
      latest_round: null,
      operator_inbox: [],
      counts: {
        ...dashboard().counts,
        rounds: 1,
        pending_operator_inbox: 0,
      },
    },
  ];
  for (const value of cases) {
    assert.throws(
      () => normalizeHarmonyDashboard(value, config),
      (error: unknown) =>
        error instanceof HarmonyDashboardError
        && error.code === "harmony_dashboard_invalid_response",
    );
  }
});

test("adapter has no write, service fallback, provider, subprocess, or secret output", () => {
  const source = [
    "../netlify/functions/_shared/harmony-dashboard.mts",
    "../netlify/functions/harmony-dashboard.mts",
  ].map((path) => readFileSync(new URL(path, import.meta.url), "utf8")).join("\n");
  assert.match(source, /get_preview_harmony_dashboard/);
  assert.match(source, /method: "GET"/);
  assert.match(source, /context\.deploy/);
  assert.match(source, /currentStudioReleaseSha/);
  assert.doesNotMatch(
    source,
    /getEnv\("(?:CONTEXT|DEPLOY_PRIME_URL|URL|COMMIT_REF)"\)/,
  );
  assert.doesNotMatch(source, /method:\s*"POST"|node:child_process|spawn\(|execFile|console\.(?:log|error)|OPENAI_API_KEY|XAI_API_KEY|TELEGRAM_BOT_TOKEN|PUBLICATION_WORKER_TOKEN/);
  assert.doesNotMatch(source, /authorizationKey\s*\?\?|projectKey\s*\?\?|serviceRoleKey/);
});
