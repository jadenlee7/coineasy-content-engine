import assert from "node:assert/strict";
import test from "node:test";
import {
  ContentReviewReadinessError,
  getContentReviewReadiness,
} from "../netlify/functions/_shared/content-review-readiness.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const ITEM_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const JOB_ID = "44444444-4444-4444-8444-444444444444";
const SOURCE_ID = "55555555-5555-4555-8555-555555555555";
const CREATED_AT = "2026-08-28T12:00:00.000Z";
const HASH = "a".repeat(64);

function config() {
  return {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
  };
}

function readinessResult() {
  return {
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    generate_job_id: JOB_ID,
    source_item_id: SOURCE_ID,
    source_published_at: CREATED_AT,
    source_is_latest: true,
    source_within_24h: true,
    feed_active: true,
    feed_poll_interval_minutes: 15,
    feed_last_polled_at: CREATED_AT,
    feed_poll_recent: true,
    banner_sha256: HASH,
    grok_outbox_count: 1,
    grok_status: "sent",
    grok_decision: "PASS",
    grok_next_action: "ready_for_human_approval",
    grok_verdict_sha256: "b".repeat(64),
    grok_banner_sha256: HASH,
    approval_count: 0,
    publication_count: 0,
  };
}

test("review readiness binds the exact workspace, item, and immutable version", async () => {
  let requestBody: Record<string, unknown> = {};
  const readiness = await getContentReviewReadiness(
    config(),
    ITEM_ID,
    VERSION_ID,
    async (input, init) => {
      const request = new Request(input, init);
      assert.equal(
        request.url,
        "https://project.supabase.co/rest/v1/rpc/get_content_review_readiness",
      );
      assert.equal(request.method, "POST");
      assert.equal(request.headers.get("authorization"), "Bearer server-only-service-role");
      requestBody = JSON.parse(String(init?.body));
      return Response.json(readinessResult());
    },
  );

  assert.deepEqual(requestBody, {
    target_workspace_id: WORKSPACE_ID,
    target_content_item_id: ITEM_ID,
    target_content_version_id: VERSION_ID,
  });
  assert.equal(readiness?.generate_job_id, JOB_ID);
  assert.equal(readiness?.grok_decision, "PASS");
  assert.equal(readiness?.publication_count, 0);
  assert.doesNotMatch(
    JSON.stringify(readiness),
    /source_url|source_copy|verdict\"|summary|provider_response|storage_path|request_payload/,
  );
});

test("review readiness accepts a bounded zero-outbox snapshot", async () => {
  const result = {
    ...readinessResult(),
    grok_outbox_count: 0,
    grok_status: null,
    grok_decision: null,
    grok_next_action: null,
    grok_verdict_sha256: null,
    grok_banner_sha256: null,
  };
  const readiness = await getContentReviewReadiness(
    config(),
    ITEM_ID,
    VERSION_ID,
    async () => Response.json(result),
  );
  assert.equal(readiness?.grok_outbox_count, 0);
  assert.equal(readiness?.grok_status, null);
});

test("review readiness rejects extra private fields and inconsistent Grok evidence", async () => {
  for (const result of [
    { ...readinessResult(), provider_response_id: "must-not-leak" },
    { ...readinessResult(), grok_outbox_count: 0 },
    { ...readinessResult(), grok_decision: "PASS", grok_next_action: null },
    { ...readinessResult(), grok_banner_sha256: "c".repeat(64) },
    { ...readinessResult(), source_published_at: null },
    { ...readinessResult(), feed_last_polled_at: null },
    { ...readinessResult(), grok_status: null },
    { ...readinessResult(), content_version_id: "66666666-6666-4666-8666-666666666666" },
  ]) {
    await assert.rejects(
      () => getContentReviewReadiness(
        config(),
        ITEM_ID,
        VERSION_ID,
        async () => Response.json(result),
      ),
      (error: unknown) => error instanceof ContentReviewReadinessError
        && error.code === "invalid_content_review_readiness_response",
    );
  }
});

test("review readiness reports unavailable storage without synthesizing zero", async () => {
  await assert.rejects(
    () => getContentReviewReadiness(
      config(),
      ITEM_ID,
      VERSION_ID,
      async () => new Response(null, { status: 404 }),
    ),
    (error: unknown) => error instanceof ContentReviewReadinessError
      && error.code === "content_review_readiness_unavailable",
  );
});
