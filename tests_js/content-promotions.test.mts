import assert from "node:assert/strict";
import test from "node:test";
import performanceHandler from "../netlify/functions/library-performance.mts";
import {
  canonicalPublicationUrl,
  ContentPromotionError,
  listContentPromotionRecommendations,
  recordManualPublicationObservation,
} from "../netlify/functions/_shared/content-promotions.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const ITEM_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const PUBLICATION_ID = "44444444-4444-4444-8444-444444444444";
const RECOMMENDATION_ID = "55555555-5555-4555-8555-555555555555";
const ACCESS_TOKEN = "test-studio-access-token-32-bytes";

function config() {
  return {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
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

test("canonicalizes only exact public X and Telegram post URLs", () => {
  assert.equal(
    canonicalPublicationUrl("x", "https://X.com/SquidRouter/status/123/"),
    "https://x.com/squidrouter/status/123",
  );
  assert.equal(
    canonicalPublicationUrl("telegram", "https://t.me/SquidKorea/456/"),
    "https://t.me/squidkorea/456",
  );
  assert.equal(
    canonicalPublicationUrl("telegram", "https://telegram.me/s/SquidKorea/456?single"),
    "https://t.me/squidkorea/456",
  );
  assert.equal(
    canonicalPublicationUrl("x", "https://www.twitter.com/Yellow/status/123?s=20#share"),
    "https://x.com/yellow/status/123",
  );
  assert.equal(
    canonicalPublicationUrl("telegram", "https://t.me/c/123/456"),
    null,
  );
  assert.equal(
    canonicalPublicationUrl("telegram", "https://t.me/+invite"),
    null,
  );
  assert.equal(
    canonicalPublicationUrl("x", "https://x.com/Yellow/status/123?utm_source=x"),
    "https://x.com/yellow/status/123",
  );
  assert.equal(
    canonicalPublicationUrl("x", "https://evil.example/Yellow/status/123"),
    null,
  );
});

test("records an observation through the workspace-scoped RPC without publishing", async () => {
  let posted: Record<string, unknown> = {};
  const receipt = await recordManualPublicationObservation(
    config(),
    ITEM_ID,
    VERSION_ID,
    "x",
    "https://x.com/Yellow/status/123",
    async (input, init) => {
      const request = new Request(input, init);
      assert.equal(
        request.url,
        "https://project.supabase.co/rest/v1/rpc/record_manual_publication_observation",
      );
      assert.equal(request.headers.get("authorization"), "Bearer server-only-service-role");
      posted = JSON.parse(String(init?.body));
      return Response.json({
        publication_id: PUBLICATION_ID,
        content_version_id: VERSION_ID,
        channel: "x",
        external_url: "https://x.com/yellow/status/123",
        reused: false,
      }, { status: 201 });
    },
  );
  assert.deepEqual(posted, {
    target_workspace_id: WORKSPACE_ID,
    target_content_item_id: ITEM_ID,
    target_content_version_id: VERSION_ID,
    target_channel: "x",
    target_external_url: "https://x.com/yellow/status/123",
  });
  assert.equal(receipt.publication_id, PUBLICATION_ID);
  assert.equal(receipt.external_url, "https://x.com/yellow/status/123");
  await assert.rejects(
    () => recordManualPublicationObservation(
      config(),
      ITEM_ID,
      VERSION_ID,
      "x",
      "https://x.com/Yellow/status/123",
      async () => Response.json({
        publication_id: PUBLICATION_ID,
        content_version_id: "66666666-6666-4666-8666-666666666666",
        channel: "x",
        external_url: "https://x.com/yellow/status/123",
        reused: false,
      }),
    ),
    (error: unknown) =>
      error instanceof ContentPromotionError
      && error.code === "publication_observation_invalid_response",
  );
  await assert.rejects(
    () => recordManualPublicationObservation(
      config(),
      ITEM_ID,
      VERSION_ID,
      "telegram",
      "https://t.me/c/123/456",
    ),
    (error: unknown) =>
      error instanceof ContentPromotionError
      && error.code === "invalid_publication_observation",
  );
});

test("accepts only bounded deterministic recommendation DTOs", async () => {
  const observedAt = new Date(Date.now() - 60_000).toISOString();
  const summary = await listContentPromotionRecommendations(
    config(),
    ITEM_ID,
    VERSION_ID,
    async (input, init) => {
      const request = new Request(input, init);
      assert.equal(
        request.url,
        "https://project.supabase.co/rest/v1/rpc/list_content_promotion_recommendations",
      );
      assert.deepEqual(JSON.parse(String(init?.body)), {
        target_workspace_id: WORKSPACE_ID,
        target_content_item_id: ITEM_ID,
        target_content_version_id: VERSION_ID,
      });
      return Response.json({
        items: [{
          recommendation_id: RECOMMENDATION_ID,
          content_version_id: VERSION_ID,
          target_kind: "article",
          score: 0.83,
          reason_codes: ["community_alignment", "high_reach"],
          policy_version: "content-performance-v1",
          source_url: "https://x.com/Yellow/status/123",
          channel: "x",
          observed_at: observedAt,
          source_ready: true,
        }],
        publications: [{
          publication_id: PUBLICATION_ID,
          content_version_id: VERSION_ID,
          channel: "x",
          external_url: "https://x.com/Yellow/status/123",
        }],
      });
    },
  );
  assert.deepEqual(summary.items, [{
    recommendation_id: RECOMMENDATION_ID,
    content_version_id: VERSION_ID,
    target_kind: "article",
    score: 0.83,
    reason_codes: ["community_alignment", "high_reach"],
    policy_version: "content-performance-v1",
    source_url: "https://x.com/yellow/status/123",
    channel: "x",
    observed_at: observedAt,
    source_ready: true,
  }]);
  assert.deepEqual(summary.publications, [{
    publication_id: PUBLICATION_ID,
    content_version_id: VERSION_ID,
    channel: "x",
    external_url: "https://x.com/yellow/status/123",
  }]);

  await assert.rejects(
    () => listContentPromotionRecommendations(
      config(),
      ITEM_ID,
      VERSION_ID,
      async () => Response.json({
        items: [{
          recommendation_id: RECOMMENDATION_ID,
          content_version_id: VERSION_ID,
          target_kind: "tutorial",
          score: 1.2,
          reason_codes: ["raw_message"],
          policy_version: "content-performance-v1",
          source_url: "https://t.me/c/123/456",
          channel: "telegram",
          observed_at: observedAt,
          source_ready: true,
        }],
        publications: [],
      }),
    ),
    (error: unknown) =>
      error instanceof ContentPromotionError
      && error.code === "content_promotions_invalid_response",
  );
});

test("rejects stale, future, and wrong-version promotion evidence", async () => {
  const recommendation = (observedAt: string, contentVersionId = VERSION_ID) => ({
    recommendation_id: RECOMMENDATION_ID,
    content_version_id: contentVersionId,
    target_kind: "article",
    score: 0.83,
    reason_codes: ["high_reach"],
    policy_version: "content-performance-v1",
    source_url: "https://x.com/Yellow/status/123",
    channel: "x",
    observed_at: observedAt,
    source_ready: true,
  });
  for (const item of [
    recommendation(new Date(Date.now() - 24 * 60 * 60 * 1_000 - 5_000).toISOString()),
    recommendation(new Date(Date.now() + 5 * 60 * 1_000 + 5_000).toISOString()),
    recommendation(new Date().toISOString(), "66666666-6666-4666-8666-666666666666"),
  ]) {
    await assert.rejects(
      () => listContentPromotionRecommendations(
        config(),
        ITEM_ID,
        VERSION_ID,
        async () => Response.json({ items: [item], publications: [] }),
      ),
      (error: unknown) =>
        error instanceof ContentPromotionError
        && error.code === "content_promotions_invalid_response",
    );
  }
});

test("manual publication HTTP route requires a team session and only records evidence", async () => {
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const originalFetch = globalThis.fetch;
  let rpcCalls = 0;
  let rpcBody: Record<string, unknown> = {};
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    assert.ok(request.url.endsWith("/rest/v1/rpc/record_manual_publication_observation"));
    rpcCalls += 1;
    rpcBody = JSON.parse(String(init?.body));
    return Response.json({
      publication_id: PUBLICATION_ID,
      content_version_id: VERSION_ID,
      channel: "telegram",
      external_url: "https://t.me/squidkorea/456",
      reused: false,
    }, { status: 201 });
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const unauthorized = await performanceHandler(
        new Request(`https://console.example/api/library/${ITEM_ID}/performance`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            content_version_id: VERSION_ID,
            channel: "telegram",
            external_url: "https://t.me/SquidKorea/456",
          }),
        }),
        { params: { contentId: ITEM_ID } } as never,
      );
      assert.equal(unauthorized.status, 401);
      assert.equal(rpcCalls, 0);

      const missingVersion = await performanceHandler(
        new Request(`https://console.example/api/library/${ITEM_ID}/performance`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
          },
          body: JSON.stringify({
            channel: "telegram",
            external_url: "https://t.me/SquidKorea/456",
          }),
        }),
        { params: { contentId: ITEM_ID } } as never,
      );
      assert.equal(missingVersion.status, 400);
      assert.equal(rpcCalls, 0);

      const response = await performanceHandler(
        new Request(`https://console.example/api/library/${ITEM_ID}/performance`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            cookie: `${STUDIO_SESSION_COOKIE}=${cookie}`,
          },
          body: JSON.stringify({
            content_version_id: VERSION_ID,
            channel: "telegram",
            external_url: "https://t.me/SquidKorea/456",
          }),
        }),
        { params: { contentId: ITEM_ID } } as never,
      );
      assert.equal(response.status, 201);
      assert.equal(response.headers.get("cache-control"), "no-store");
      assert.equal(rpcCalls, 1);
      assert.deepEqual(rpcBody, {
        target_workspace_id: WORKSPACE_ID,
        target_content_item_id: ITEM_ID,
        target_content_version_id: VERSION_ID,
        target_channel: "telegram",
        target_external_url: "https://t.me/squidkorea/456",
      });
      const payload = await response.json() as Record<string, unknown>;
      assert.equal(payload.external_url, "https://t.me/squidkorea/456");
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
