import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import buzzShadowHandler from "../netlify/functions/buzz-shadow-origintrail.mts";
import {
  BuzzShadowError,
  hasValidBuzzShadowAccess,
  projectBuzzShadowPage,
} from "../netlify/functions/_shared/buzz-shadow.mts";
import type { BatchReviewPage } from "../netlify/functions/_shared/batch-review.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const FINISHED_AT = "2026-07-31T12:00:00.000Z";
const TOKEN = "buzz-shadow-read-token-that-is-long-enough";

const adapterSource = [
  "../netlify/functions/_shared/buzz-shadow.mts",
  "../netlify/functions/buzz-shadow-origintrail.mts",
].map((path) => readFileSync(new URL(path, import.meta.url), "utf8")).join("\n");

function reviewPage(overrides: Record<string, unknown> = {}): BatchReviewPage {
  return {
    items: [{
      ref: `batch:${JOB_ID}`,
      job_id: JOB_ID,
      client_id: "origintrail",
      agent_id: "origintrail_client_agent",
      workflow_kind: "official_source_nonurgent_pack",
      stage: "generate",
      status: "completed",
      model: "gpt-5.6-luna",
      model_tier: "S",
      title: "OriginTrail 검토 초안",
      result_code: "needs_review",
      actual_cost_microusd: 2_200,
      finished_at: FINISHED_AT,
      source_url: "https://x.com/origin_trail/status/2082883998829752783",
      ...overrides,
    }],
    next_cursor: { finished_at: FINISHED_AT, job_id: JOB_ID },
  } as BatchReviewPage;
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

test("Buzz shadow access uses only its dedicated constant-time credential", () => {
  const request = new Request("https://console.example/api/buzz-shadow/origintrail/batch", {
    headers: { "x-coineasy-buzz-key": TOKEN },
  });
  assert.equal(
    hasValidBuzzShadowAccess(request, (name) =>
      name === "BUZZ_SHADOW_ACCESS_TOKEN" ? TOKEN : undefined
    ),
    true,
  );
  assert.equal(hasValidBuzzShadowAccess(request, () => "too-short"), false);
  assert.equal(hasValidBuzzShadowAccess(
    new Request(request.url, { headers: { authorization: `Bearer ${TOKEN}` } }),
    () => TOKEN,
  ), false);
});

test("adapter imports no relay, provider, publisher, deploy, or process execution path", () => {
  assert.doesNotMatch(
    adapterSource,
    /node:child_process|execFile|spawn\(|OPENAI_API_KEY|TELEGRAM_BOT_TOKEN|TYPEFULLY|\/publish\/|git push|messages send|getBatchReviewItem|result_payload/i,
  );
  assert.match(adapterSource, /listBatchReviewInbox/);
});

test("projects a deterministic metadata-only OriginTrail review event", () => {
  const page = projectBuzzShadowPage(reviewPage(), WORKSPACE_ID);
  assert.equal(page.schema_version, "1.0");
  assert.equal(page.mode, "shadow_read_only");
  assert.equal(page.events.length, 1);
  assert.deepEqual(Object.keys(page.events[0]).sort(), [
    "actual_cost_microusd",
    "agent_id",
    "client_id",
    "event_id",
    "event_type",
    "finished_at",
    "job_id",
    "model_tier",
    "result_code",
    "review_ref",
    "source_url",
    "studio_review_path",
    "workflow_kind",
  ]);
  assert.match(page.events[0].event_id, /^[a-f0-9]{64}$/);
  assert.equal(page.events[0].studio_review_path, `/?batch=${JOB_ID}`);
  assert.equal(page.events[0].source_url, "https://x.com/origin_trail/status/2082883998829752783");
  assert.deepEqual(page.next_cursor, reviewPage().next_cursor);

  const replay = projectBuzzShadowPage(reviewPage({
    title: "sk-test-secret-must-not-cross-the-boundary",
  }), WORKSPACE_ID);
  assert.equal(replay.events[0].event_id, page.events[0].event_id);
  assert.notEqual(
    projectBuzzShadowPage(
      reviewPage(),
      "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    ).events[0].event_id,
    page.events[0].event_id,
  );
  const serialized = JSON.stringify(replay);
  assert.doesNotMatch(serialized, /secret|title|result_payload|input_sha256|token|provider/i);
});

test("preserves PostgreSQL microseconds in event and keyset cursor timestamps", () => {
  const preciseFinishedAt = "2026-07-31T12:00:00.123456+00:00";
  const precisePage = reviewPage({ finished_at: preciseFinishedAt });
  precisePage.next_cursor = {
    finished_at: preciseFinishedAt,
    job_id: JOB_ID,
  };

  const projected = projectBuzzShadowPage(precisePage, WORKSPACE_ID);
  assert.equal(projected.events[0].finished_at, preciseFinishedAt);
  assert.equal(projected.next_cursor?.finished_at, preciseFinishedAt);
});

test("fails the whole Buzz shadow page closed on source or identity ambiguity", () => {
  assert.throws(
    () => projectBuzzShadowPage(reviewPage(), "not-a-workspace"),
    (error: unknown) => error instanceof BuzzShadowError
      && error.code === "buzz_shadow_invalid_review_page",
  );

  for (const override of [
    { ref: `batch:33333333-3333-4333-8333-333333333333` },
    { client_id: "squid" },
    { agent_id: "squid_client_agent" },
    { workflow_kind: "naver_seo_article" },
    { stage: "review" },
    { status: "failed" },
    { result_code: "approved" },
    { model_tier: "M", model: "gpt-5.6-luna" },
  ]) {
    assert.throws(
      () => projectBuzzShadowPage(reviewPage(override), WORKSPACE_ID),
      (error: unknown) => error instanceof BuzzShadowError
        && error.code === "buzz_shadow_invalid_review_page",
    );
  }

  for (const sourceUrl of [
    null,
    "http://x.com/origin_trail/status/2082883998829752783",
    "https://x.com/Origin_Trail/status/2082883998829752783",
    "https://x.com/origin_trail/status/2082883998829752783?x=1",
    "https://x.com/origin_trail/status/not-digits",
    "https://example.com/origin_trail/status/2082883998829752783",
  ]) {
    assert.throws(
      () => projectBuzzShadowPage(reviewPage({ source_url: sourceUrl }), WORKSPACE_ID),
      (error: unknown) => error instanceof BuzzShadowError
        && error.code === "buzz_shadow_invalid_review_page",
    );
  }

  const duplicate = reviewPage();
  duplicate.items.push({ ...duplicate.items[0] });
  assert.throws(
    () => projectBuzzShadowPage(duplicate, WORKSPACE_ID),
    (error: unknown) => error instanceof BuzzShadowError
      && error.code === "buzz_shadow_invalid_review_page",
  );
});

test("endpoint authenticates before storage and rejects every write method", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("storage must not be called");
  };
  try {
    await withNetlifyEnvironment({
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const unconfigured = await buzzShadowHandler(new Request(
        "https://console.example/api/buzz-shadow/origintrail/batch",
        { headers: { "x-coineasy-buzz-key": TOKEN } },
      ));
      assert.equal(unconfigured.status, 503);
      assert.deepEqual(await unconfigured.json(), { error: "buzz_shadow_not_configured" });
    });

    await withNetlifyEnvironment({
      BUZZ_SHADOW_ACCESS_TOKEN: TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const denied = await buzzShadowHandler(new Request(
        "https://console.example/api/buzz-shadow/origintrail/batch",
      ));
      assert.equal(denied.status, 401);
      assert.equal(denied.headers.get("cache-control"), "no-store");
      assert.equal(denied.headers.get("vary"), "x-coineasy-buzz-key");

      const write = await buzzShadowHandler(new Request(
        "https://console.example/api/buzz-shadow/origintrail/batch",
        { method: "POST", headers: { "x-coineasy-buzz-key": TOKEN } },
      ));
      assert.equal(write.status, 405);
      assert.equal(write.headers.get("allow"), "GET");
      assert.equal(fetchCalls, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("endpoint returns only the bounded shadow projection and preserves the cursor", async () => {
  const originalFetch = globalThis.fetch;
  let rpcBody: Record<string, unknown> = {};
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    assert.equal(
      request.url,
      "https://project.supabase.co/rest/v1/rpc/list_agent_batch_review_inbox",
    );
    rpcBody = JSON.parse(String(init?.body));
    const item = reviewPage().items[0];
    return Response.json({
      items: [{ ...item, ref: undefined }],
      next_cursor: reviewPage().next_cursor,
    });
  };
  try {
    await withNetlifyEnvironment({
      BUZZ_SHADOW_ACCESS_TOKEN: TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await buzzShadowHandler(new Request(
        `https://console.example/api/buzz-shadow/origintrail/batch?limit=12&before_finished_at=${encodeURIComponent(FINISHED_AT)}&before_job_id=${JOB_ID}`,
        { headers: { "x-coineasy-buzz-key": TOKEN } },
      ));
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("cache-control"), "no-store");
      assert.equal(response.headers.get("x-content-type-options"), "nosniff");
      const payload = await response.json() as Record<string, any>;
      assert.equal(payload.events[0].job_id, JOB_ID);
      assert.doesNotMatch(JSON.stringify(payload), /server-only-service-role/);
      assert.deepEqual(rpcBody, {
        target_workspace_id: WORKSPACE_ID,
        target_limit: 12,
        target_before_finished_at: FINISHED_AT,
        target_before_job_id: JOB_ID,
      });
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("endpoint rejects invalid filters and malformed upstream pages", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    return Response.json({
      items: [{
        ...reviewPage().items[0],
        ref: undefined,
        client_id: "squid",
      }],
      next_cursor: null,
    });
  };
  try {
    await withNetlifyEnvironment({
      BUZZ_SHADOW_ACCESS_TOKEN: TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const headers = { "x-coineasy-buzz-key": TOKEN };
      const invalid = await buzzShadowHandler(new Request(
        "https://console.example/api/buzz-shadow/origintrail/batch?limit=51",
        { headers },
      ));
      assert.equal(invalid.status, 400);
      assert.equal(fetchCalls, 0);

      const malformed = await buzzShadowHandler(new Request(
        "https://console.example/api/buzz-shadow/origintrail/batch",
        { headers },
      ));
      assert.equal(malformed.status, 502);
      assert.deepEqual(await malformed.json(), { error: "batch_review_invalid_response" });
      assert.equal(fetchCalls, 1);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("shadow token equal to another secret fails closed as not configured", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("storage must not be called");
  };
  try {
    await withNetlifyEnvironment({
      BUZZ_SHADOW_ACCESS_TOKEN: TOKEN,
      SUPABASE_SERVICE_ROLE_KEY: TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await buzzShadowHandler(new Request(
        "https://console.example/api/buzz-shadow/origintrail/batch",
        { headers: { "x-coineasy-buzz-key": TOKEN } },
      ));
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: "buzz_shadow_not_configured" });
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(fetchCalls, 0);
});

test("scoped buzz shadow key is the bearer while the project API key stays unchanged", async () => {
  const SCOPED = "scoped-batch-reviewer-role-jwt-value";
  const originalFetch = globalThis.fetch;
  let bearer: string | null = null;
  globalThis.fetch = async (input, init) => {
    bearer = new Headers(init?.headers).get("authorization");
    assert.equal(new Headers(init?.headers).get("apikey"), "server-only-service-role");
    return Response.json({
      items: [reviewPage().items[0]],
      next_cursor: null,
    });
  };
  try {
    await withNetlifyEnvironment({
      BUZZ_SHADOW_ACCESS_TOKEN: TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      SUPABASE_BUZZ_SHADOW_KEY: SCOPED,
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await buzzShadowHandler(new Request(
        "https://console.example/api/buzz-shadow/origintrail/batch?limit=1",
        { headers: { "x-coineasy-buzz-key": TOKEN } },
      ));
      assert.equal(response.status, 200);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(bearer, `Bearer ${SCOPED}`);
});
