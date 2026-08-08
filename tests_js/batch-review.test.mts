import assert from "node:assert/strict";
import { createHash } from "node:crypto";
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
import { ORIGINTRAIL_ARCHIVED_JOB_ID } from "../netlify/functions/_shared/origintrail-archived-review.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const FINISHED_AT = "2026-07-31T12:00:00.000Z";
const SOURCE_URL = "https://x.com/origin_trail/status/123";
const SOURCE_CONTENT = "OriginTrail이 공식 업데이트를 발표했습니다.";
const RECORDED_MEDIA_URL =
  "https://pbs.twimg.com/amplify_video_thumb/2085781578374860800/img/vH2LVZnApTMbJhq2.jpg";
const PREVIEW_MEDIA_URL = `${RECORDED_MEDIA_URL}?name=orig`;

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
    source_url: SOURCE_URL,
    ...overrides,
  };
}

function detailItem(overrides: Record<string, unknown> = {}) {
  return {
    ...listItem(),
    result_payload: resultPayload(),
    source_content: SOURCE_CONTENT,
    input_sha256: "a".repeat(64),
    actual_input_tokens: 1_000,
    actual_output_tokens: 220,
    ...overrides,
  };
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record).sort().map(
      key => `${JSON.stringify(key)}:${canonicalJson(record[key])}`,
    ).join(",")}}`;
  }
  return JSON.stringify(value);
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function factCheckReference(overrides: Record<string, unknown> = {}) {
  return {
    kind: "origintrail_implementation",
    label_ko: "OriginTrail Prime Agent 어댑터 구현 범위",
    url: "https://github.com/OriginTrail/dkg/blob/075e87d881260a1aad2d86b53fa250d5d3f67d40/packages/adapter-prime-agent/README.md",
    observed_at: "2026-08-08T11:05:11.000Z",
    snapshot_sha256: "b".repeat(64),
    availability: "available",
    finding_ko: "현재 공개 구현은 전송·연결 계층이며 후속 단계가 남아 있습니다.",
    ...overrides,
  };
}

function allFactCheckReferences() {
  return [
    factCheckReference(),
    factCheckReference({
      kind: "prime_intellect_announcement",
      label_ko: "Prime Intellect 공식 발표",
      url: "https://www.primeintellect.ai/blog/prime-agent",
      snapshot_sha256: null,
    }),
    factCheckReference({
      kind: "prime_agent_release",
      label_ko: "Prime Agent 공개 릴리스",
      url: "https://github.com/PrimeIntellect-ai/prime-agent/releases/tag/v0.7.0",
      snapshot_sha256: null,
    }),
    factCheckReference({
      kind: "arc_community_leaderboard",
      label_ko: "ARC 커뮤니티 리더보드",
      url: "https://arcprize.org/api/leaderboards",
      snapshot_sha256: "c".repeat(64),
    }),
    factCheckReference({
      kind: "arc_methodology",
      label_ko: "ARC-AGI-3 평가 방법론",
      url: "https://arcprize.org/media/ARC_AGI_3_Technical_Report.pdf",
      snapshot_sha256: null,
    }),
    factCheckReference({
      kind: "scorecard_source",
      label_ko: "Prime Agent 점수표 소스",
      url: "https://github.com/PrimeIntellect-ai/arc-agi-3-prime-agent-scorecard/commit/aaee22436235de6f784df7b89302e1258aae9ab9",
      snapshot_sha256: null,
    }),
  ];
}

function factCheckReferencesWithFirst(overrides: Record<string, unknown>) {
  const references = allFactCheckReferences();
  references[0] = factCheckReference(overrides);
  return references;
}

function factCheckReferencesWithDuplicateKind() {
  const references = allFactCheckReferences();
  references[1] = factCheckReference({
    url: "https://github.com/OriginTrail/dkg/commit/075e87d881260a1aad2d86b53fa250d5d3f67d40",
  });
  return references;
}

function factCheckPayload(overrides: Record<string, unknown> = {}) {
  return {
    schema_version: "1.0",
    policy_version: "origintrail-media-fact-evidence@1",
    review_status: "qualified",
    human_review_required: true,
    verified_at: "2026-08-08T11:05:11.000Z",
    source_url: SOURCE_URL,
    source_content_sha256: sha256(SOURCE_CONTENT),
    media: {
      type: "video",
      media_key: "13_2085781578374860800",
      recorded_url: RECORDED_MEDIA_URL,
      preview_url: PREVIEW_MEDIA_URL,
      preview_url_sha256: sha256(PREVIEW_MEDIA_URL),
      width: 1_920,
      height: 1_920,
      factual_evidence: false,
    },
    review_notes_ko: [
      "미디어는 출처 고정용이며 사실 근거로 사용하지 않습니다.",
      "성능 수치는 공급자 발표와 커뮤니티 점수표로 구분합니다.",
    ],
    official_references: allFactCheckReferences(),
    ...overrides,
  };
}

function factCheckEvidence(
  payloadOverrides: Record<string, unknown> = {},
  outerOverrides: Record<string, unknown> = {},
) {
  const payload = factCheckPayload(payloadOverrides);
  return {
    payload,
    evidence_sha256: sha256(canonicalJson(payload)),
    ...outerOverrides,
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
    return Response.json(detailItem({ fact_check_evidence: factCheckEvidence() }));
  });
  assert.equal(item?.ref, `batch:${JOB_ID}`);
  assert.equal(item?.result_payload.headline_ko, "OriginTrail 업데이트");
  assert.equal(item?.source_content, "OriginTrail이 공식 업데이트를 발표했습니다.");
  assert.equal(item?.actual_output_tokens, 220);
  assert.equal(
    item?.fact_check_evidence?.payload.policy_version,
    "origintrail-media-fact-evidence@1",
  );
  assert.equal(item?.fact_check_evidence?.payload.review_status, "qualified");
  assert.equal(item?.fact_check_evidence?.payload.media.factual_evidence, false);
  assert.equal(item?.fact_check_evidence?.payload.official_references.length, 6);

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

test("Batch review detail keeps legacy evidence absence and explicit null compatible", async () => {
  for (const legacy of [detailItem(), detailItem({ fact_check_evidence: null })]) {
    const item = await getBatchReviewItem(
      reviewConfig(),
      JOB_ID,
      async () => Response.json(legacy),
    );
    assert.equal(item?.fact_check_evidence, undefined);
  }
});

test("Batch review detail fails closed on malformed or unbound fact evidence", async () => {
  const invalidEvidence = [
    factCheckEvidence({}, { unexpected: true }),
    factCheckEvidence({ unexpected: true }),
    factCheckEvidence({ schema_version: "2.0" }),
    factCheckEvidence({ policy_version: "origintrail-media-fact-check@1" }),
    factCheckEvidence({ review_status: "approved" }),
    factCheckEvidence({ human_review_required: false }),
    factCheckEvidence({ verified_at: "not-a-date" }),
    factCheckEvidence({ verified_at: "2026-02-31T11:05:11Z" }),
    factCheckEvidence({ verified_at: "2026-08-08T11:05:11+00:00" }),
    factCheckEvidence({ source_url: "https://x.com.evil.example/origin_trail/status/123" }),
    factCheckEvidence({ source_url: "http://x.com/origin_trail/status/123" }),
    factCheckEvidence({ source_url: `${SOURCE_URL}?tracking=1` }),
    factCheckEvidence({ source_content_sha256: "A".repeat(64) }),
    factCheckEvidence({ source_content_sha256: "c".repeat(64) }),
    factCheckEvidence({ media: {
      ...(factCheckPayload().media as Record<string, unknown>),
      unexpected: true,
    } }),
    factCheckEvidence({ media: {
      ...(factCheckPayload().media as Record<string, unknown>),
      factual_evidence: true,
    } }),
    factCheckEvidence({ media: {
      ...(factCheckPayload().media as Record<string, unknown>),
      preview_url: "https://evil.example/image.jpg?name=orig",
      preview_url_sha256: sha256("https://evil.example/image.jpg?name=orig"),
    } }),
    factCheckEvidence({ media: {
      ...(factCheckPayload().media as Record<string, unknown>),
      preview_url_sha256: "d".repeat(64),
    } }),
    factCheckEvidence({ media: {
      ...(factCheckPayload().media as Record<string, unknown>),
      preview_url: `${RECORDED_MEDIA_URL}?name=small`,
      preview_url_sha256: sha256(`${RECORDED_MEDIA_URL}?name=small`),
    } }),
    factCheckEvidence({ media: {
      ...(factCheckPayload().media as Record<string, unknown>),
      width: 0,
    } }),
    factCheckEvidence({ review_notes_ko: [] }),
    factCheckEvidence({ review_notes_ko: [" "] }),
    factCheckEvidence({ review_notes_ko: ["x".repeat(1_001)] }),
    factCheckEvidence({ review_notes_ko: Array.from({ length: 9 }, () => "검토") }),
    factCheckEvidence({ official_references: [] }),
    factCheckEvidence({ official_references: factCheckReferencesWithFirst({
      unexpected: true,
    }) }),
    factCheckEvidence({ official_references: factCheckReferencesWithFirst({
      kind: "unknown",
    }) }),
    factCheckEvidence({ official_references: factCheckReferencesWithFirst({
      url: "https://github.com.evil.example/OriginTrail/dkg/blob/main/README.md",
    }) }),
    factCheckEvidence({ official_references: factCheckReferencesWithFirst({
      url: "https://github.com/another-org/dkg/blob/main/README.md",
    }) }),
    factCheckEvidence({ official_references: factCheckReferencesWithFirst({
      snapshot_sha256: "B".repeat(64),
    }) }),
    factCheckEvidence({ official_references: factCheckReferencesWithFirst({
      availability: "unknown",
    }) }),
    factCheckEvidence({ official_references: factCheckReferencesWithFirst({
      observed_at: "2026-08-08T11:05:11+00:00",
    }) }),
    factCheckEvidence({ official_references: factCheckReferencesWithDuplicateKind() }),
    factCheckEvidence({}, { evidence_sha256: "e".repeat(64) }),
    factCheckEvidence({}, { evidence_sha256: "E".repeat(64) }),
  ];

  for (const fact_check_evidence of invalidEvidence) {
    await assert.rejects(
      () => getBatchReviewItem(
        reviewConfig(),
        JOB_ID,
        async () => Response.json(detailItem({ fact_check_evidence })),
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
      return Response.json(detailItem({ fact_check_evidence: factCheckEvidence() }));
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
      assert.equal(
        detailPayload.fact_check_evidence.payload.human_review_required,
        true,
      );

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

test("an authenticated archived Staging review survives deletion of its Preview database", async () => {
  const originalFetch = globalThis.fetch;
  let fetchCalls = 0;
  globalThis.fetch = async () => {
    fetchCalls += 1;
    throw new Error("the archived review must not read deleted storage");
  };
  try {
    await withNetlifyEnvironment({ STUDIO_ACCESS_TOKEN: ACCESS_TOKEN }, async () => {
      const cookie = createStudioSessionValue(ACCESS_TOKEN);
      const response = await batchReviewItemHandler(new Request(
        `https://console.example/api/batch-review/${ORIGINTRAIL_ARCHIVED_JOB_ID}`,
        { headers: { cookie: `${STUDIO_SESSION_COOKIE}=${cookie}` } },
      ), { params: { jobId: ORIGINTRAIL_ARCHIVED_JOB_ID } } as never);
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("cache-control"), "no-store");
      const payload = await response.json() as Record<string, any>;
      assert.equal(payload.job_id, ORIGINTRAIL_ARCHIVED_JOB_ID);
      assert.equal(payload.source_content, null);
      assert.equal(payload.source_evidence.storage, "hash_only_archive");
      assert.equal(payload.source_evidence.content_length, 6_661);
      assert.match(payload.source_evidence.content_sha256, /^[a-f0-9]{64}$/);
      assert.equal(payload.fact_check_evidence, undefined);
      assert.equal(fetchCalls, 0);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
