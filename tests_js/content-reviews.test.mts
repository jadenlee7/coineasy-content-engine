import assert from "node:assert/strict";
import test from "node:test";
import reviewHandler from "../netlify/functions/library-review.mts";
import {
  brandReviewGuidanceAudit,
  getBrandReviewGuidance,
  getContentReviewSummary,
  recordStudioContentReview,
} from "../netlify/functions/_shared/content-reviews.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const ITEM_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const APPROVAL_ID = "44444444-4444-4444-8444-444444444444";
const REQUEST_ID = "55555555-5555-4555-8555-555555555555";
const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const CREATED_AT = "2026-07-31T09:00:00.000Z";

function config() {
  return {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
  };
}

function guidanceResult() {
  return {
    policy_version: "brand-review-learning@1",
    approved_examples: [{
      content_item_id: ITEM_ID,
      content_version_id: VERSION_ID,
      content_kind: "article",
      text: "공식 원문에 근거한 자연스러운 한국어 승인 예시",
      approved_at: CREATED_AT,
    }],
    avoid_reason_codes: [{
      code: "unsupported_claim",
      count: 2,
    }],
  };
}

function detailResult() {
  return {
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    client_id: "squid",
    content_kind: "article",
    title: "검토할 아티클",
    status: "needs_review",
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    version_number: 1,
    prompt_version: "article@4",
    locale: "ko-KR",
    content: { lead: "원문 근거 리드" },
    channel_copy: { x: "X 문구", telegram: "텔레그램 문구" },
    deliverables: {},
    qa: {},
    generation_meta: { mock_mode: false },
    assets: [],
    figma_links: [],
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

test("brand review guidance is bounded, typed, and auditable without comments", async () => {
  const guidance = await getBrandReviewGuidance(
    config(),
    "squid",
    "article",
    async (input, init) => {
      const request = new Request(input, init);
      assert.match(request.url, /rpc\/get_brand_review_guidance$/);
      assert.equal(request.headers.get("authorization"), "Bearer server-only-service-role");
      assert.deepEqual(JSON.parse(String(init?.body)), {
        target_workspace_id: WORKSPACE_ID,
        target_client_id: "squid",
        target_content_kind: "article",
        target_example_limit: 3,
      });
      return Response.json(guidanceResult());
    },
  );

  assert.equal(guidance.approved_examples.length, 1);
  assert.equal(guidance.avoid_reason_codes[0].code, "unsupported_claim");
  assert.doesNotMatch(JSON.stringify(guidance), /comment|reviewer/);
  const audit = brandReviewGuidanceAudit(guidance);
  assert.deepEqual({
    ...audit,
    brand_review_guidance_hash: "<sha256>",
  }, {
    brand_review_policy_version: "brand-review-learning@1",
    brand_review_example_ids: [ITEM_ID],
    brand_review_avoid_reason_codes: ["unsupported_claim"],
    brand_review_guidance_hash: "<sha256>",
  });
  assert.match(audit.brand_review_guidance_hash, /^[a-f0-9]{64}$/);
});

test("studio review RPC binds workspace, current version, reasons, and idempotency", async () => {
  let requestBody: Record<string, unknown> = {};
  const result = await recordStudioContentReview(
    config(),
    ITEM_ID,
    {
      contentVersionId: VERSION_ID,
      decision: "rejected",
      reasonCodes: ["off_brand_tone", "awkward_korean"],
      comment: "브랜드 문장 리듬을 다시 맞춰주세요.",
      factCheck: null,
      idempotencyKey: REQUEST_ID,
    },
    async (_input, init) => {
      requestBody = JSON.parse(String(init?.body));
      return Response.json({
        approval_id: APPROVAL_ID,
        content_item_id: ITEM_ID,
        content_version_id: VERSION_ID,
        decision: "rejected",
        status: "rejected",
        reason_codes: ["awkward_korean", "off_brand_tone"],
        fact_check_policy_version: "double-fact-check@1",
        source_facts_verified: false,
        output_claims_verified: false,
        created_at: CREATED_AT,
        reused: false,
      });
    },
  );

  assert.equal(result.status, "rejected");
  assert.deepEqual(requestBody, {
    target_workspace_id: WORKSPACE_ID,
    target_content_item_id: ITEM_ID,
    target_content_version_id: VERSION_ID,
    review_decision: "rejected",
    review_reason_codes: ["off_brand_tone", "awkward_korean"],
    review_comment: "브랜드 문장 리듬을 다시 맞춰주세요.",
    review_fact_check_policy_version: "double-fact-check@1",
    review_source_facts_verified: false,
    review_output_claims_verified: false,
    review_idempotency_key: REQUEST_ID,
  });
});

test("legacy review summaries default missing fact-check fields to unverified", async () => {
  const summary = await getContentReviewSummary(
    config(),
    ITEM_ID,
    async () => Response.json({
      approval_id: APPROVAL_ID,
      content_version_id: VERSION_ID,
      decision: "approved",
      reason_codes: [],
      comment: null,
      reviewer_source: "studio_session",
      created_at: CREATED_AT,
    }),
  );
  assert.deepEqual({
    fact_check_policy_version: summary?.fact_check_policy_version,
    source_facts_verified: summary?.source_facts_verified,
    output_claims_verified: summary?.output_claims_verified,
  }, {
    fact_check_policy_version: null,
    source_facts_verified: false,
    output_claims_verified: false,
  });
});

test("approval HTTP input requires the completed double fact-check attestation", async () => {
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  await withNetlifyEnvironment({
    STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
    SUPABASE_URL: "https://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  }, async () => {
    const response = await reviewHandler(new Request(
      `https://console.example/api/library/${ITEM_ID}/review`,
      {
        method: "POST",
        headers: {
          cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
          "Content-Type": "application/json",
          "Idempotency-Key": REQUEST_ID,
        },
        body: JSON.stringify({
          content_version_id: VERSION_ID,
          decision: "approved",
          reason_codes: [],
          comment: "",
          fact_check: {
            policy_version: "double-fact-check@1",
            source_facts_verified: true,
            output_claims_verified: false,
          },
        }),
      },
    ), { params: { contentId: ITEM_ID } } as never);
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_fact_check_attestation" });
  });
});

test("review HTTP route requires the Studio session and records an approval without publishing", async () => {
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    calls.push(request.url);
    if (request.url.endsWith("/rpc/get_content_library_item")) {
      return Response.json(detailResult());
    }
    if (request.url.endsWith("/rpc/record_studio_content_review_v2")) {
      return Response.json({
        approval_id: APPROVAL_ID,
        content_item_id: ITEM_ID,
        content_version_id: VERSION_ID,
        decision: "approved",
        status: "approved",
        reason_codes: [],
        fact_check_policy_version: "double-fact-check@1",
        source_facts_verified: true,
        output_claims_verified: true,
        created_at: CREATED_AT,
        reused: false,
      });
    }
    if (request.url.endsWith("/rpc/get_content_review_summary")) {
      return Response.json({
        approval_id: APPROVAL_ID,
        content_version_id: VERSION_ID,
        decision: "approved",
        reason_codes: [],
        comment: null,
        reviewer_source: "studio_session",
        created_at: CREATED_AT,
        fact_check_policy_version: "double-fact-check@1",
        source_facts_verified: true,
        output_claims_verified: true,
      });
    }
    if (request.url.endsWith("/rpc/get_brand_review_guidance")) {
      return Response.json(guidanceResult());
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
      const unauthorized = await reviewHandler(new Request(
        `https://console.example/api/library/${ITEM_ID}/review`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": REQUEST_ID,
          },
          body: JSON.stringify({
            content_version_id: VERSION_ID,
            decision: "approved",
            reason_codes: [],
            comment: "",
            fact_check: {
              policy_version: "double-fact-check@1",
              source_facts_verified: true,
              output_claims_verified: true,
            },
          }),
        },
      ), { params: { contentId: ITEM_ID } } as never);
      assert.equal(unauthorized.status, 401);
      assert.equal(calls.length, 0);

      const response = await reviewHandler(new Request(
        `https://console.example/api/library/${ITEM_ID}/review`,
        {
          method: "POST",
          headers: {
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "Content-Type": "application/json",
            "Idempotency-Key": REQUEST_ID,
          },
          body: JSON.stringify({
            content_version_id: VERSION_ID,
            decision: "approved",
            reason_codes: [],
            comment: "",
            fact_check: {
              policy_version: "double-fact-check@1",
              source_facts_verified: true,
              output_claims_verified: true,
            },
          }),
        },
      ), { params: { contentId: ITEM_ID } } as never);

      assert.equal(response.status, 201);
      const payload = await response.json() as Record<string, any>;
      assert.equal(payload.result.status, "approved");
      assert.equal(payload.latest_review.reviewer_source, "studio_session");
      assert.equal(payload.latest_review.source_facts_verified, true);
      assert.equal(payload.brand_learning.brand_review_example_ids[0], ITEM_ID);
      assert.ok(calls.every((url) => !url.includes("publication")));
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a post-commit projection failure still returns the committed review", async () => {
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.endsWith("/rpc/get_content_library_item")) {
      return Response.json(detailResult());
    }
    if (url.endsWith("/rpc/record_studio_content_review_v2")) {
      return Response.json({
        approval_id: APPROVAL_ID,
        content_item_id: ITEM_ID,
        content_version_id: VERSION_ID,
        decision: "approved",
        status: "approved",
        reason_codes: [],
        fact_check_policy_version: "double-fact-check@1",
        source_facts_verified: true,
        output_claims_verified: true,
        created_at: CREATED_AT,
        reused: false,
      });
    }
    if (
      url.endsWith("/rpc/get_content_review_summary")
      || url.endsWith("/rpc/get_brand_review_guidance")
    ) {
      return Response.json({ message: "temporary projection failure" }, { status: 503 });
    }
    throw new Error(`unexpected request ${url}`);
  };

  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const response = await reviewHandler(new Request(
        `https://console.example/api/library/${ITEM_ID}/review`,
        {
          method: "POST",
          headers: {
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
            "Content-Type": "application/json",
            "Idempotency-Key": REQUEST_ID,
          },
          body: JSON.stringify({
            content_version_id: VERSION_ID,
            decision: "approved",
            reason_codes: [],
            comment: "",
            fact_check: {
              policy_version: "double-fact-check@1",
              source_facts_verified: true,
              output_claims_verified: true,
            },
          }),
        },
      ), { params: { contentId: ITEM_ID } } as never);

      assert.equal(response.status, 201);
      const payload = await response.json() as Record<string, any>;
      assert.equal(payload.result.status, "approved");
      assert.equal(payload.latest_review.approval_id, APPROVAL_ID);
      assert.equal(payload.latest_review.reviewer_source, "studio_session");
      assert.equal(payload.latest_review.output_claims_verified, true);
      assert.deepEqual(payload.brand_learning, { available: false });
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
