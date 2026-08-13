import assert from "node:assert/strict";
import test from "node:test";

import handler from "../netlify/functions/autonomous-ops-origintrail.mts";


const TOKEN = "autonomous-ops-token-that-is-dedicated";
const WORKSPACE = "11111111-1111-4111-8111-111111111111";
const SNAPSHOT = "a".repeat(64);
const INCIDENT = "b".repeat(64);

function env(overrides: Record<string, string | undefined> = {}) {
  const values: Record<string, string | undefined> = {
    AUTONOMOUS_OPS_WORKER_TOKEN: TOKEN,
    AUTONOMOUS_OPS_LEDGER_ENABLED: "true",
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "s".repeat(40),
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE,
    ...overrides,
  };
  return (name: string) => values[name];
}

async function context(
  getEnv: (name: string) => string | undefined,
  fetcher: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const originalFetch = globalThis.fetch;
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true, value: { env: { get: getEnv } },
  });
  globalThis.fetch = fetcher;
  try { await run(); } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

function request(body: unknown, token = TOKEN): Request {
  return new Request("https://preview.example/api/autonomous-ops/origintrail", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-coineasy-autonomous-ops-key": token,
    },
    body: JSON.stringify(body),
  });
}

function observation() {
  return {
    workspace_id: WORKSPACE,
    protocol_version: "origintrail-autonomous-ops@1",
    observed_at_epoch: 1_786_500_000,
    observation_date_kst: "2026-08-13",
    snapshot_sha256: SNAPSHOT,
    batch_failed_count: 0,
    batch_stale_count: 0,
    cost_overage_count: 0,
    buzz_delivery_failed_count: 0,
    buzz_delivery_unknown_count: 0,
    review_ack_unknown_count: 0,
    operations_response_unknown_count: 0,
    unexpected_publication_count: 0,
    nonterminal_batch_count: 0,
    actual_cost_microusd: 227,
  };
}

function plan() {
  return {
    action: "record_plan",
    protocol_version: "origintrail-autonomous-ops@1",
    snapshot_sha256: SNAPSHOT,
    incident_key: INCIDENT,
    category: "batch_failed",
    severity: "high",
    title_ko: "OriginTrail Batch 실패 감지",
    summary_ko: "종결 실패 Batch 작업이 관측되었습니다.",
    steps_ko: ["error_code를 읽기 전용 확인", "재제출 없이 수정 후보 작성"],
    execution_mode: "propose_only",
    automatic_publication: false,
    external_writes: false,
  };
}

test("auth and literal false gate block storage", async () => {
  let calls = 0;
  await context(env(), async () => { calls += 1; throw new Error("no"); }, async () => {
    assert.equal((await handler(request({
      action: "observe", protocol_version: "origintrail-autonomous-ops@1",
    }, "x".repeat(40)))).status, 401);
  });
  for (const flag of [undefined, "", "false", "TRUE", "1"]) {
    await context(env({ AUTONOMOUS_OPS_LEDGER_ENABLED: flag }), async () => {
      calls += 1; throw new Error("no");
    }, async () => {
      assert.equal((await handler(request({
        action: "observe", protocol_version: "origintrail-autonomous-ops@1",
      }))).status, 503);
    });
  }
  assert.equal(calls, 0);
});

test("observe calls only the bounded read RPC", async () => {
  let url = "";
  let body: Record<string, unknown> = {};
  await context(env(), async (input, init) => {
    url = String(input);
    body = JSON.parse(String(init?.body));
    return Response.json(observation());
  }, async () => {
    const response = await handler(request({
      action: "observe", protocol_version: "origintrail-autonomous-ops@1",
    }));
    assert.equal(response.status, 200);
  });
  assert.match(url, /observe_origintrail_autonomous_ops$/);
  assert.deepEqual(body, {
    target_workspace_id: WORKSPACE,
    target_protocol_version: "origintrail-autonomous-ops@1",
  });
});

test("record is propose-only and keeps scoped bearer separate", async () => {
  let url = "";
  let headers = new Headers();
  let body: Record<string, unknown> = {};
  await context(env({ SUPABASE_AUTONOMOUS_OPS_KEY: "scoped-jwt" }), async (input, init) => {
    url = String(input);
    headers = new Headers(init?.headers);
    body = JSON.parse(String(init?.body));
    const value = plan();
    return Response.json({
      workspace_id: WORKSPACE,
      task_id: "22222222-2222-4222-8222-222222222222",
      incident_key: INCIDENT,
      category: value.category,
      severity: value.severity,
      title_ko: value.title_ko,
      summary_ko: value.summary_ko,
      steps_ko: value.steps_ko,
      status: "proposed",
      reused: false,
      automatic_execution: false,
    });
  }, async () => {
    assert.equal((await handler(request(plan()))).status, 200);
  });
  assert.match(url, /record_origintrail_autonomous_ops_plan$/);
  assert.equal(headers.get("apikey"), "s".repeat(40));
  assert.equal(headers.get("authorization"), "Bearer scoped-jwt");
  assert.equal(body.target_execution_mode, "propose_only");
  assert.equal(body.target_automatic_publication, false);
  assert.equal(body.target_external_writes, false);
});

test("record rejects execution, publication, extra keys and arbitrary actions", async () => {
  const invalid = [
    { ...plan(), external_writes: true },
    { ...plan(), automatic_publication: true },
    { ...plan(), execution_mode: "execute" },
    { ...plan(), extra: "x" },
    { action: "deploy", protocol_version: "origintrail-autonomous-ops@1" },
  ];
  let calls = 0;
  for (const value of invalid) {
    await context(env(), async () => { calls += 1; throw new Error("no"); }, async () => {
      assert.equal((await handler(request(value))).status, 400);
    });
  }
  assert.equal(calls, 0);
});
