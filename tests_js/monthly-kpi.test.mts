import assert from "node:assert/strict";
import test from "node:test";

import monthlyKpiHandler from "../netlify/functions/monthly-kpi.mts";
import {
  fetchMonthlyKpi,
  hasValidMonthlyKpiAccess,
  normalizedKpiMonth,
} from "../netlify/functions/_shared/monthly-kpi.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const TOKEN = "content-kpi-sync-token-that-is-long-enough";

function config() {
  return {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
  };
}

function snapshot() {
  return {
    ok: true,
    schema_version: "1.0",
    workspace_id: WORKSPACE_ID,
    month: "2026-07",
    timezone: "Asia/Seoul",
    generated_at: "2026-07-30T12:00:00Z",
    days_in_month: 31,
    elapsed_days: 30,
    counting_rule: "distinct_published_content_item",
    clients: [{
      client_id: "squid",
      display_name: "Squid",
      targets: {
        daily_news: 31,
        article: 2,
        tutorial: 2,
        community_event: 2,
      },
      expected_to_date: {
        daily_news: 30,
        article: 2,
        tutorial: 2,
        community_event: 2,
      },
      published: { daily_news: 25, article: 1, tutorial: 1 },
      gaps_to_target: { daily_news: 6, article: 1, tutorial: 1 },
      pipeline: {
        daily_news: { needs_review: 1, ready: 0, failed: 0 },
        article: { needs_review: 1, ready: 0, failed: 0 },
        tutorial: { needs_review: 0, ready: 1, failed: 0 },
      },
      community_event_source: "notion",
    }],
  };
}

async function withNetlifyEnvironment(
  values: Record<string, string | undefined>,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: (name: string) => values[name] } },
  });
  try {
    await run();
  } finally {
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

test("monthly KPI access is a dedicated constant-time server credential", () => {
  const request = new Request("https://console.example/api/kpi/monthly", {
    headers: { "x-content-kpi-key": TOKEN },
  });
  assert.equal(
    hasValidMonthlyKpiAccess(request, (name) =>
      name === "CONTENT_KPI_SYNC_TOKEN" ? TOKEN : undefined
    ),
    true,
  );
  assert.equal(
    hasValidMonthlyKpiAccess(request, () => "too-short"),
    false,
  );
  assert.equal(normalizedKpiMonth("2026-07"), "2026-07");
  assert.equal(normalizedKpiMonth("2026-7"), null);
  assert.equal(normalizedKpiMonth("2026-13"), null);
});

test("fetches the workspace-scoped, read-only monthly RPC", async () => {
  let requestBody: unknown;
  const result = await fetchMonthlyKpi(
    config(),
    "2026-07",
    async (input, init) => {
      const request = new Request(input, init);
      assert.equal(
        request.url,
        "https://project.supabase.co/rest/v1/rpc/get_monthly_content_kpi",
      );
      assert.equal(request.headers.get("authorization"), "Bearer server-only-service-role");
      requestBody = JSON.parse(String(init?.body));
      return Response.json(snapshot());
    },
  );
  assert.deepEqual(requestBody, {
    target_workspace_id: WORKSPACE_ID,
    target_month: "2026-07-01",
  });
  assert.equal(result.month, "2026-07");
});

test("handler fails closed and never exposes the Supabase credential", async () => {
  await withNetlifyEnvironment({
    CONTENT_KPI_SYNC_TOKEN: TOKEN,
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  }, async () => {
    const denied = await monthlyKpiHandler(new Request(
      "https://console.example/api/kpi/monthly?month=2026-07",
    ));
    assert.equal(denied.status, 401);

    const invalid = await monthlyKpiHandler(new Request(
      "https://console.example/api/kpi/monthly?month=2026-7",
      { headers: { "x-content-kpi-key": TOKEN } },
    ));
    assert.equal(invalid.status, 400);
    assert.doesNotMatch(await invalid.text(), /service-role/i);
  });
});
