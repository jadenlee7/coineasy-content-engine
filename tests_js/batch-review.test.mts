import assert from "node:assert/strict";
import test from "node:test";

import batchReviewHandler from "../netlify/functions/batch-review.mts";
import batchReviewItemHandler from "../netlify/functions/batch-review-item.mts";
import {
  BatchReviewError,
  getBatchReviewItem,
  listBatchReviewInbox,
} from "../netlify/functions/_shared/batch-review.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const FINISHED_AT = "2026-07-31T12:00:00.000Z";

function reviewConfig() {
  return {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
  };
}

function listItem(overrides: Record<string, unknown> = {}) {
  return {
    job_id: JOB_ID,
    client_id: "origintrail",
    agent_id: "origintrail_client_agent",
    workflow_kind: "official_source_nonurgent_pack",
    stage: "generate",
    status: "completed",
    model: "gpt-5.6-luna",
    model_tier: "S",
    title: "OriginTrail 공식 원문 비긴급 팩",
    result_code: "needs_review",
    actual_cost_microusd: 2_200,
    finished_at: FINISHED_AT,
    source_url: "https://x.com/origin_trail/status/123",
    ...overrides,
  };
}

function detailItem(overrides: Record<string, unknown> = {}) {
  return {
    ...listItem(),
    result_payload: resultPayload(),
    source_content: "OriginTrail이 공식 업데이트를 발표했습니다.",
    input_sha256: "a".repeat(64),
    actual_input_tokens: 1_000,
    actual_output_tokens: 220,
    ...overrides,
  };
}

function resultPayload(overrides: Record<string, unknown> = {}) {
  return {
    headline_ko: "OriginTrail 업데이트",
    body_ko: "공식 원문을 기반으로 만든 검토용 초안입니다.",
    x_copy_ko: "OriginTrail 공식 업데이트",
    telegram_copy_ko: "OriginTrail 공식 업데이트를 확인해보세요.",
    ...overrides,
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

test("Batch review list uses the fixed workspace RPC and returns batch-prefixed refs", async () => {
  let requestBody: Record<string, unknown> = {};
  const page = await listBatchReviewInbox(reviewConfig(), {
    limit: 12,
    beforeFinishedAt: FINISHED_AT,
    beforeJobId: JOB_ID,
  }, async (input, init) => {
    const request = new Request(input, init);
    assert.equal(
      request.url,
      "https://project.supabase.co/rest/v1/rpc/list_agent_batch_review_inbox",
    );
    assert.equal(request.method, "POST");
    assert.equal(request.headers.get("authorization"), "Bearer server-only-service-role");
    requestBody = JSON.parse(String(init?.body));
    return Response.json({
      items: [listItem()],
      next_cursor: { finished_at: FINISHED_AT, job_id: JOB_ID },
    });
  });

  assert.equal(page.items[0].ref, `batch:${JOB_ID}`);
  assert.equal(page.items[0].client_id, "origintrail");
  assert.deepEqual(page.next_cursor, { finished_at: FINISHED_AT, job_id: JOB_ID });
  assert.deepEqual(requestBody, {
    target_workspace_id: WORKSPACE_ID,
    target_limit: 12,
    target_before_finished_at: FINISHED_AT,
    target_before_job_id: JOB_ID,
  });
});

test("Batch review list rejects another client or agent identity", async () => {
  for (const invalid of [
    listItem({ client_id: "squid" }),
    listItem({ agent_id: "squid_client_agent" }),
  ]) {
    await assert.rejects(
      () => listBatchReviewInbox(
        reviewConfig(),
        {},
        async () => Response.json({ items: [invalid], next_cursor: null }),
      ),
      (error: unknown) => (
        error instanceof BatchReviewError
        && error.code === "batch_review_invalid_response"
      ),
    );
  }
});

test("Batch review detail accepts only bounded OriginTrail review results", async () => {
  const item = await getBatchReviewItem(reviewConfig(), JOB_ID, async (input, init) => {
    const request = new Request(input, init);
    assert.equal(
      request.url,
      "https://project.supabase.co/rest/v1/rpc/get_agent_batch_review_item",
    );
    assert.deepEqual(JSON.parse(String(init?.body)), {
      target_workspace_id: WORKSPACE_ID,
      target_job_id: JOB_ID,
    });
    return Response.json(detailItem());
  });
  assert.equal(item?.ref, `batch:${JOB_ID}`);
  assert.equal(item?.result_payload.headline_ko, "OriginTrail 업데이트");
  assert.equal(item?.source_content, "OriginTrail이 공식 업데이트를 발표했습니다.");
  assert.equal(item?.actual_output_tokens, 220);

  for (const invalid of [
    detailItem({ client_id: "yellow" }),
    detailItem({ client_id: "squid" }),
    detailItem({ agent_id: "squid_client_agent" }),
    detailItem({ workflow_kind: "naver_seo_article" }),
    detailItem({ stage: "review" }),
    detailItem({ status: "failed" }),
    detailItem({ result_code: "approved" }),
    detailItem({ source_content: "" }),
    detailItem({ source_content: "x".repeat(60_001) }),
    detailItem({ result_payload: resultPayload({ headline_ko: "" }) }),
    detailItem({ result_payload: resultPayload({ body_ko: "" }) }),
    detailItem({ result_payload: resultPayload({ x_copy_ko: "" }) }),
    detailItem({ result_payload: resultPayload({ telegram_copy_ko: "" }) }),
    detailItem({ result_payload: resultPayload({ headline_ko: " \n\t" }) }),
    detailItem({ result_payload: resultPayload({ headline_ko: "x".repeat(121) }) }),
    detailItem({ result_payload: resultPayload({ body_ko: "x".repeat(1_801) }) }),
    detailItem({ result_payload: resultPayload({ x_copy_ko: "x".repeat(501) }) }),
    detailItem({ result_payload: resultPayload({ telegram_copy_ko: "x".repeat(1_801) }) }),
    detailItem({ result_payload: {
      headline_ko: "제목",
      body_ko: "본문",
      x_copy_ko: "X",
    } }),
    detailItem({ result_payload: {
      ...resultPayload(),
      api_secret: "must-not-leak",
    } }),
    detailItem({ result_payload: resultPayload({ body_ko: { nested: "본문" } }) }),
  ]) {
    await assert.rejects(
      () => getBatchReviewItem(
        reviewConfig(),
        JOB_ID,
        async () => Response.json(invalid),
      ),
      (error: unknown) => (
        error instanceof BatchReviewError
        && error.code === "batch_review_invalid_response"
      ),
    );
  }
});

test("Batch review endpoints authenticate before any database call and reject writes", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("database must not be called");
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const unauthorizedList = await batchReviewHandler(
        new Request("https://console.example/api/batch-review"),
      );
      assert.equal(unauthorizedList.status, 401);
      assert.equal(unauthorizedList.headers.get("cache-control"), "no-store");
      assert.equal(unauthorizedList.headers.get("vary"), "Cookie");

      const unauthorizedDetail = await batchReviewItemHandler(
        new Request(`https://console.example/api/batch-review/${JOB_ID}`),
        { params: { jobId: JOB_ID } } as never,
      );
      assert.equal(unauthorizedDetail.status, 401);

      const deniedListWrite = await batchReviewHandler(new Request(
        "https://console.example/api/batch-review",
        { method: "POST" },
      ));
      assert.equal(deniedListWrite.status, 405);
      assert.equal(deniedListWrite.headers.get("allow"), "GET");

      const deniedDetailWrite = await batchReviewItemHandler(new Request(
        `https://console.example/api/batch-review/${JOB_ID}`,
        { method: "DELETE" },
      ), { params: { jobId: JOB_ID } } as never);
      assert.equal(deniedDetailWrite.status, 405);
      assert.equal(deniedDetailWrite.headers.get("allow"), "GET");
      assert.equal(fetchCalls, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("authenticated Batch review endpoints are GET-only, no-store, and validate responses", async () => {
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const request = new Request(input);
    if (request.url.endsWith("/rest/v1/rpc/list_agent_batch_review_inbox")) {
      return Response.json({ items: [listItem()], next_cursor: null });
    }
    if (request.url.endsWith("/rest/v1/rpc/get_agent_batch_review_item")) {
      return Response.json(detailItem());
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const headers = { cookie: `${STUDIO_SESSION_COOKIE}=${cookie}` };
      const list = await batchReviewHandler(new Request(
        "https://console.example/api/batch-review?limit=12",
        { headers },
      ));
      assert.equal(list.status, 200);
      assert.equal(list.headers.get("cache-control"), "no-store");
      assert.equal(list.headers.get("vary"), "Cookie");
      const listPayload = await list.json() as Record<string, any>;
      assert.equal(listPayload.items[0].ref, `batch:${JOB_ID}`);

      const invalidFilters = await batchReviewHandler(new Request(
        "https://console.example/api/batch-review?limit=51",
        { headers },
      ));
      assert.equal(invalidFilters.status, 400);

      const detail = await batchReviewItemHandler(new Request(
        `https://console.example/api/batch-review/${JOB_ID}`,
        { headers },
      ), { params: { jobId: JOB_ID } } as never);
      assert.equal(detail.status, 200);
      assert.equal(detail.headers.get("cache-control"), "no-store");
      const detailPayload = await detail.json() as Record<string, any>;
      assert.equal(detailPayload.result_payload.headline_ko, "OriginTrail 업데이트");

      const invalidId = await batchReviewItemHandler(new Request(
        "https://console.example/api/batch-review/not-a-uuid",
        { headers },
      ), { params: { jobId: "not-a-uuid" } } as never);
      assert.equal(invalidId.status, 400);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
