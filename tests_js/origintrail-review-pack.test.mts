import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  deterministicReviewPackAssetId,
  materializeOriginTrailReviewPack,
  originTrailReviewPackSha256,
  OriginTrailReviewPackError,
} from "../netlify/functions/_shared/origintrail-review-pack.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const JOB_ID = "22222222-2222-4222-8222-222222222222";
const CONTENT_ID = "33333333-3333-4333-8333-333333333333";
const SOURCE_ID = "44444444-4444-4444-8444-444444444444";
const VERSION_ID = "55555555-5555-4555-8555-555555555555";
const INPUT_SHA = "a".repeat(64);
const RESULT_SHA = "b".repeat(64);
const LOGO = readFileSync(new URL(
  "../web/console/assets/brands/origintrail-dark.png",
  import.meta.url,
));

function detail(overrides: Record<string, unknown> = {}) {
  const source = "OriginTrail의 검증 가능한 지식 업데이트 공식 게시물입니다.";
  return {
    job_id: JOB_ID,
    request_id: CONTENT_ID,
    source_item_ids: [SOURCE_ID],
    result_sha256: RESULT_SHA,
    client_id: "origintrail",
    agent_id: "origintrail_client_agent",
    workflow_kind: "official_source_nonurgent_pack",
    stage: "generate",
    status: "completed",
    model: "gpt-5.6-luna",
    model_tier: "S",
    title: "OriginTrail 검증 가능한 지식 업데이트",
    result_code: "needs_review",
    actual_cost_microusd: 220,
    finished_at: "2026-08-08T01:00:00.000Z",
    source_url: "https://x.com/origin_trail/status/2082883998829752783",
    source_content: source,
    source_evidence_kind: "x_post_text",
    result_payload: {
      headline_ko: "OriginTrail 검증 가능한 지식 업데이트",
      body_ko: "공식 게시물의 핵심 내용을 한국어로 정리했습니다.",
      x_copy_ko: "OriginTrail 공식 업데이트를 확인하세요.",
      telegram_copy_ko: "OriginTrail 공식 게시물의 핵심 내용을 확인하세요.",
    },
    input_sha256: INPUT_SHA,
    actual_input_tokens: 800,
    actual_output_tokens: 200,
    ...overrides,
  };
}

function config() {
  return {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "service-role-key",
    workspaceId: WORKSPACE_ID,
  };
}

test("review pack hash and asset UUID are deterministic and domain-bound", () => {
  const input = {
    workspaceId: WORKSPACE_ID,
    jobId: JOB_ID,
    contentItemId: CONTENT_ID,
    sourceItemId: SOURCE_ID,
    inputSha256: INPUT_SHA,
    resultSha256: RESULT_SHA,
    sourceContentSha256: "c".repeat(64),
    bannerSha256: "d".repeat(64),
  };
  const expected = createHash("sha256").update([
    "coineasy-origintrail-review-pack",
    "1.0",
    WORKSPACE_ID,
    JOB_ID,
    CONTENT_ID,
    SOURCE_ID,
    INPUT_SHA,
    RESULT_SHA,
    "c".repeat(64),
    "d".repeat(64),
  ].join("\0"), "utf8").digest("hex");
  assert.equal(originTrailReviewPackSha256(input), expected);
  assert.equal(originTrailReviewPackSha256(input), originTrailReviewPackSha256(input));
  assert.match(deterministicReviewPackAssetId(JOB_ID, "d".repeat(64)), /^[a-f0-9-]{36}$/);
  assert.equal(
    deterministicReviewPackAssetId(JOB_ID, "d".repeat(64)),
    deterministicReviewPackAssetId(JOB_ID, "d".repeat(64)),
  );
});

test("one call materializes exact Batch copy, deterministic banner, catalog, and source binding", async () => {
  const source = String(detail().source_content);
  const sourceSha = createHash("sha256").update(source, "utf8").digest("hex");
  let uploadedAssetId = "";
  let recordedBody: Record<string, unknown> = {};
  let boundBody: Record<string, unknown> = {};
  const calls: string[] = [];
  const result = await materializeOriginTrailReviewPack(
    config(),
    JOB_ID,
    "https://console.example",
    async (input, init) => {
      const request = new Request(input, init);
      calls.push(`${request.method} ${request.url}`);
      if (request.url.endsWith("/rest/v1/rpc/get_agent_batch_review_item")) {
        return Response.json(detail());
      }
      if (request.url === "https://console.example/assets/brands/origintrail-dark.png") {
        return new Response(LOGO, {
          headers: { "content-type": "image/png", "content-length": String(LOGO.length) },
        });
      }
      if (request.url.includes("/storage/v1/object/content-studio/")) {
        uploadedAssetId = request.url.split("/").at(-2) || "";
        assert.equal(request.method, "POST");
        assert.equal(request.headers.get("x-upsert"), "false");
        return Response.json({ Key: "stored" });
      }
      if (request.url.endsWith("/rest/v1/rpc/record_generated_content")) {
        recordedBody = JSON.parse(String(init?.body));
        return Response.json({
          content_item_id: CONTENT_ID,
          content_version_id: VERSION_ID,
          asset_ids: [uploadedAssetId],
        });
      }
      if (request.url.endsWith("/rest/v1/rpc/bind_origintrail_batch_review_pack")) {
        boundBody = JSON.parse(String(init?.body));
        return Response.json({
          job_id: JOB_ID,
          content_item_id: CONTENT_ID,
          content_version_id: VERSION_ID,
          asset_id: uploadedAssetId,
          source_item_id: SOURCE_ID,
          banner_sha256: boundBody.target_banner_sha256,
          review_pack_sha256: boundBody.target_review_pack_sha256,
          protocol_version: "origintrail-review-pack@1",
          reused: false,
        });
      }
      throw new Error(`unexpected request ${request.method} ${request.url}`);
    },
  );

  assert.equal(result.contentItemId, CONTENT_ID);
  assert.equal(result.contentVersionId, VERSION_ID);
  assert.equal(result.assetId, uploadedAssetId);
  assert.equal(result.sourceItemId, SOURCE_ID);
  assert.equal(result.protocolVersion, "origintrail-review-pack@1");
  assert.equal(recordedBody.target_content_item_id, CONTENT_ID);
  assert.equal(recordedBody.target_prompt_version, "origintrail-batch-review-pack@1");
  assert.deepEqual(recordedBody.target_channel_copy, {
    telegram: detail().result_payload.telegram_copy_ko,
    x: detail().result_payload.x_copy_ko,
  });
  const generationMeta = recordedBody.target_generation_meta as Record<string, unknown>;
  const storedContent = recordedBody.target_content as Record<string, unknown>;
  const storedSource = storedContent.source as Record<string, unknown>;
  assert.equal(generationMeta.mock_mode, false);
  assert.equal(generationMeta.source_content_sha256, sourceSha);
  assert.equal(generationMeta.source_evidence_kind, "x_post_text");
  assert.equal(storedSource.type, "x_post_text");
  assert.equal(generationMeta.banner_sha256, result.bannerSha256);
  assert.equal((generationMeta.fact_check as Record<string, unknown>).human_review_required, true);
  assert.equal(boundBody.target_content_item_id, CONTENT_ID);
  assert.equal(boundBody.target_source_item_id, SOURCE_ID);
  assert.equal(boundBody.target_result_sha256, RESULT_SHA);
  assert.match(String(boundBody.target_review_pack_sha256), /^[a-f0-9]{64}$/);
  assert.equal(calls.length, 5);
});

test("legacy detail without catalog identity fails before rendering or storage", async () => {
  let calls = 0;
  const {
    request_id: _requestId,
    source_item_ids: _sourceItemIds,
    result_sha256: _resultSha256,
    ...legacy
  } = detail();
  await assert.rejects(
    () => materializeOriginTrailReviewPack(
      config(),
      JOB_ID,
      "https://console.example",
      async () => {
        calls += 1;
        return Response.json(legacy);
      },
    ),
    (error: unknown) => error instanceof OriginTrailReviewPackError
      && error.code === "origintrail_review_pack_evidence_required",
  );
  assert.equal(calls, 1);
});
