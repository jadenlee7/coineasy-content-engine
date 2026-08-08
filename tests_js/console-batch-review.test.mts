import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consoleHtml = readFileSync(
  new URL("../web/console/index.html", import.meta.url),
  "utf8",
);

const JOB_ID = "22222222-2222-4222-8222-222222222222";

test("exposes Content Studio and Batch as explicit review sources", () => {
  assert.match(consoleHtml, /id="library-source-switcher"/);
  assert.match(consoleHtml, /data-library-source="content_studio"[^>]*>Content Studio</);
  assert.match(consoleHtml, /data-library-source="batch"[^>]*>Batch 검토</);
  assert.match(consoleHtml, /libraryState = \{ source: "content_studio"/);
  assert.match(consoleHtml, /libraryFilters\.hidden = showingBatch/);
  assert.match(consoleHtml, /selectLibrarySource\(button\.dataset\.librarySource\)/);
});

test("loads paginated Batch reviews only through authenticated GET routes", () => {
  assert.match(consoleHtml, /params\.set\("before_finished_at", libraryState\.nextCursor\.finished_at\)/);
  assert.match(consoleHtml, /params\.set\("before_job_id", libraryState\.nextCursor\.job_id\)/);
  assert.match(consoleHtml, /fetch\(`\/api\/batch-review\?\$\{params\.toString\(\)\}`/);
  assert.match(consoleHtml, /fetch\(`\/api\/batch-review\/\$\{encodeURIComponent\(batchJobId\)\}`/);
  assert.match(consoleHtml, /method: "GET"/);
  assert.match(consoleHtml, /credentials: "same-origin"/);
  assert.match(consoleHtml, /normalizeBatchReviewItem/);
  assert.match(consoleHtml, /ref !== `batch:\$\{jobId\}`/);
  assert.match(consoleHtml, /item\.client_id !== "origintrail"/);
  assert.match(consoleHtml, /item\.agent_id !== "origintrail_client_agent"/);
  assert.match(consoleHtml, /workflow_kind !== "official_source_nonurgent_pack"/);
  assert.match(consoleHtml, /result_code !== "needs_review"/);
});

test("opens a Buzz shadow review link directly in the read-only Batch source", () => {
  assert.match(consoleHtml, /initialQuery\.get\("batch"\)/);
  assert.match(consoleHtml, /initialBatchReviewRef = initialBatchReviewJobId/);
  assert.match(consoleHtml, /libraryState\.source = "batch"/);
  assert.match(consoleHtml, /initialReviewRef = initialBatchReviewRef \|\| initialReviewContentId/);
  assert.match(consoleHtml, /loadLibraryDetail\(initialReviewRef\)/);
});

test("Batch list normalization accepts only the OriginTrail canary identity", () => {
  const functionSource = consoleHtml.match(
    /function normalizeBatchReviewItem\(value\) \{[\s\S]*?\n      \}(?=\n\n      function libraryCardMarkup)/,
  )?.[0];
  assert.ok(functionSource, "normalizeBatchReviewItem must be present");

  const normalizeBatchReviewItem = Function(
    "plainObject",
    "batchJobIdFromRef",
    "safeWebUrl",
    `"use strict"; ${functionSource}; return normalizeBatchReviewItem;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    (value: unknown) => typeof value === "string" && value === `batch:${JOB_ID}` ? JOB_ID : "",
    (value: unknown) => typeof value === "string" && value.startsWith("https://") ? value : "",
  ) as (payload: unknown) => Record<string, unknown> | null;

  const validItem = {
    ref: `batch:${JOB_ID}`,
    job_id: JOB_ID,
    client_id: "origintrail",
    agent_id: "origintrail_client_agent",
    workflow_kind: "official_source_nonurgent_pack",
    stage: "generate",
    status: "completed",
    result_code: "needs_review",
    title: "OriginTrail 검토 초안",
    finished_at: "2026-07-31T12:00:00.000Z",
    source_url: "https://x.com/origin_trail/status/123",
  };

  assert.equal(normalizeBatchReviewItem(validItem)?.client_id, "origintrail");
  assert.equal(normalizeBatchReviewItem({
    ...validItem,
    client_id: "squid",
  }), null);
  assert.equal(normalizeBatchReviewItem({
    ...validItem,
    agent_id: "squid_client_agent",
  }), null);
});

test("Batch detail renders escaped plain text and offers no mutation controls", () => {
  const functionSource = consoleHtml.match(
    /function renderBatchReviewDetail\(payload\) \{[\s\S]*?\n      \}(?=\n\n      async function loadLibraryDetail)/,
  )?.[0];
  assert.ok(functionSource, "renderBatchReviewDetail must be present");
  assert.doesNotMatch(
    functionSource,
    /data-library-review|data-library-editable|data-library-publication|data-library-promotion|<button|<textarea|download/i,
  );
  assert.match(functionSource, /batch-review-output/);
  assert.match(functionSource, /batch-review-banner/);
  assert.match(functionSource, /\/api\/batch-review\/\$\{encodeURIComponent\(jobId\)\}\/banner\.png/);
  assert.match(functionSource, /Batch에 고정된 공식 원문/);
  assert.match(functionSource, /원문 본문이 수집되지 않아 사용할 수 없는 결과입니다/);
  assert.match(functionSource, /headline_ko: 120/);
  assert.match(functionSource, /body_ko: 1800/);
  assert.match(functionSource, /x_copy_ko: 500/);
  assert.match(functionSource, /telegram_copy_ko: 1800/);
  assert.match(functionSource, /resultKeys\.length === Object\.keys\(resultFieldLimits\)\.length/);
  assert.match(functionSource, /rawResultPayload\[key\]\.trim\(\)\.length >= 1/);
  assert.match(functionSource, /고정된 보조 팩트체크/);
  assert.match(functionSource, /사람의 이중 사실 확인이 필요합니다/);
  assert.match(functionSource, /safeWebUrl\(reference\.url\)/);
  assert.match(functionSource, /escapeHtml\(note\)/);
  assert.match(functionSource, /escapeHtml\(reference\.label_ko\)/);
  assert.match(functionSource, /escapeHtml\(reference\.finding_ko\)/);
  assert.match(functionSource, /escapeHtml\(safeUrl\)/);

  const libraryState: Record<string, unknown> = {};
  const libraryDetail = { innerHTML: "", hidden: true };
  const libraryDetailState = { hidden: false };
  const renderBatchReviewDetail = Function(
    "plainObject",
    "batchJobIdFromRef",
    "safeWebUrl",
    "escapeHtml",
    "formatLibraryDate",
    "libraryState",
    "libraryDetail",
    "libraryDetailState",
    `"use strict"; ${functionSource}; return renderBatchReviewDetail;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    (value: unknown) => typeof value === "string" && value === `batch:${JOB_ID}` ? JOB_ID : "",
    (value: unknown) => typeof value === "string" && value.startsWith("https://") ? value : "",
    (value: unknown) => String(value ?? "").replace(
      /[&<>\"']/g,
      char => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#039;" }[char]),
    ),
    () => "2026. 07. 31.",
    libraryState,
    libraryDetail,
    libraryDetailState,
  ) as (payload: unknown) => void;

  const validDetail = {
    ref: `batch:${JOB_ID}`,
    job_id: JOB_ID,
    client_id: "origintrail",
    agent_id: "origintrail_client_agent",
    workflow_kind: "official_source_nonurgent_pack",
    stage: "generate",
    status: "completed",
    result_code: "needs_review",
    title: "<script>alert('title')</script>",
    model: "gpt-5.6-luna",
    model_tier: "S",
    actual_cost_microusd: 2_200,
    actual_input_tokens: 1_000,
    actual_output_tokens: 220,
    input_sha256: "a".repeat(64),
    finished_at: "2026-07-31T12:00:00.000Z",
    source_url: "https://x.com/origin_trail/status/123",
    source_content: "OriginTrail이 공식 업데이트를 발표했습니다.",
    result_payload: {
      headline_ko: "<img src=x onerror=alert(1)>",
      body_ko: "검토용 plain text",
      x_copy_ko: "X 검토 문구",
      telegram_copy_ko: "Telegram 검토 문구",
    },
  };
  renderBatchReviewDetail(validDetail);

  assert.equal(libraryState.activeDetail &&
    (libraryState.activeDetail as Record<string, unknown>).source, "batch");
  assert.equal(libraryDetail.hidden, false);
  assert.equal(libraryDetailState.hidden, true);
  assert.match(libraryDetail.innerHTML, new RegExp(`batch:${JOB_ID}`));
  assert.match(libraryDetail.innerHTML, /OriginTrail/);
  assert.doesNotMatch(libraryDetail.innerHTML, /Squid/);
  assert.match(libraryDetail.innerHTML, /&lt;script&gt;alert\(&#039;title&#039;\)&lt;\/script&gt;/);
  assert.match(libraryDetail.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(libraryDetail.innerHTML, /<script|<img src=x|<button|<textarea/i);
  assert.match(libraryDetail.innerHTML, /<img src="\/api\/batch-review\//);
  assert.match(libraryDetail.innerHTML, /width="1200" height="630"/);
  assert.match(libraryDetail.innerHTML, /Buzz 전달 시 같은 PNG가 첨부/);
  assert.match(libraryDetail.innerHTML, /Batch에 고정된 공식 원문/);
  assert.match(libraryDetail.innerHTML, /OriginTrail이 공식 업데이트를 발표했습니다/);
  assert.doesNotMatch(libraryDetail.innerHTML, /원문 본문이 수집되지 않아/);
  assert.doesNotMatch(libraryDetail.innerHTML, /고정된 보조 팩트체크/);
  assert.doesNotMatch(
    libraryDetail.innerHTML,
    /data-library-review|data-library-editable|data-library-publication|data-library-promotion/i,
  );

  const factCheckEvidence = {
    evidence_sha256: "e".repeat(64),
    payload: {
      schema_version: "1.0",
      policy_version: "origintrail-media-fact-evidence@1",
      review_status: "qualified",
      human_review_required: true,
      verified_at: "2026-08-08T10:00:00.000Z",
      source_url: "https://x.com/origin_trail/status/2085782218815775024",
      source_content_sha256: "a".repeat(64),
      media: {
        type: "video",
        media_key: "13_2085781578374860800",
        recorded_url: "https://pbs.twimg.com/example.jpg",
        preview_url: "https://pbs.twimg.com/example.jpg?name=orig",
        preview_url_sha256: "b".repeat(64),
        width: 1920,
        height: 1920,
        factual_evidence: false,
      },
      review_notes_ko: [
        "공개 점수는 커뮤니티 제출 결과로 한정해 해석합니다.",
        "<img src=x onerror=alert('note')>",
      ],
      official_references: [
        {
          kind: "origintrail_implementation",
          label_ko: "OriginTrail 구현 문서 <script>alert('label')</script>",
          url: "https://github.com/OriginTrail/dkg/blob/example/README.md",
          observed_at: "2026-08-08T10:00:00.000Z",
          snapshot_sha256: "c".repeat(64),
          availability: "available",
          finding_ko: "현재 확인 범위는 전송·연결 계층입니다. <svg onload=alert('finding')>",
        },
        {
          kind: "arc_methodology",
          label_ko: "ARC-AGI-3 방법론",
          url: "https://arcprize.org/media/ARC_AGI_3_Technical_Report.pdf",
          observed_at: "2026-08-08T10:00:00.000Z",
          snapshot_sha256: null,
          availability: "unavailable",
          finding_ko: "현재 원문 스냅샷을 확인하지 못해 검증 완료로 간주하지 않습니다.",
        },
      ],
    },
  };
  renderBatchReviewDetail({
    ...validDetail,
    fact_check_evidence: factCheckEvidence,
  });
  assert.match(libraryDetail.innerHTML, /고정된 보조 팩트체크/);
  assert.match(libraryDetail.innerHTML, /사람의 이중 사실 확인이 필요합니다/);
  assert.match(libraryDetail.innerHTML, /수집 시점 확인됨/);
  assert.match(libraryDetail.innerHTML, /수집 시점 확인 불가/);
  assert.match(libraryDetail.innerHTML, /&lt;img src=x onerror=alert\(&#039;note&#039;\)&gt;/);
  assert.match(libraryDetail.innerHTML, /&lt;script&gt;alert\(&#039;label&#039;\)&lt;\/script&gt;/);
  assert.match(libraryDetail.innerHTML, /&lt;svg onload=alert\(&#039;finding&#039;\)&gt;/);
  assert.doesNotMatch(libraryDetail.innerHTML, /<script|<svg onload|<img src=x|<button|<textarea/i);
  assert.doesNotMatch(
    libraryDetail.innerHTML,
    /data-library-review|data-library-editable|data-library-publication|data-library-promotion/i,
  );

  renderBatchReviewDetail({
    ...validDetail,
    fact_check_evidence: {
      ...factCheckEvidence,
      payload: {
        ...factCheckEvidence.payload,
        official_references: [{
          ...factCheckEvidence.payload.official_references[0],
          url: "javascript:alert('unsafe')",
        }],
      },
    },
  });
  assert.doesNotMatch(libraryDetail.innerHTML, /고정된 보조 팩트체크|unsafe/);

  renderBatchReviewDetail({
    ...validDetail,
    fact_check_evidence: null,
  });
  assert.doesNotMatch(libraryDetail.innerHTML, /고정된 보조 팩트체크/);

  assert.throws(() => renderBatchReviewDetail({
    ...validDetail,
    client_id: "squid",
  }), /invalid_batch_review_detail/);

  renderBatchReviewDetail({
    ...validDetail,
    source_content: "https://t.co/example",
  });
  assert.match(libraryDetail.innerHTML, /원문 본문이 수집되지 않아 사용할 수 없는 결과입니다/);
  assert.match(libraryDetail.innerHTML, /근거 부족/);
  assert.doesNotMatch(libraryDetail.innerHTML, /batch-review-banner/);
  assert.throws(() => renderBatchReviewDetail({
    ...validDetail,
    agent_id: "squid_client_agent",
  }), /invalid_batch_review_detail/);

  assert.throws(() => renderBatchReviewDetail({
    ...validDetail,
    result_payload: {
      ...validDetail.result_payload,
      api_secret: "must-not-render",
    },
  }), /invalid_batch_review_detail/);
  assert.throws(() => renderBatchReviewDetail({
    ...validDetail,
    result_payload: {
      ...validDetail.result_payload,
      body_ko: { nested: "must-not-render" },
    },
  }), /invalid_batch_review_detail/);
  for (const field of ["headline_ko", "body_ko", "x_copy_ko", "telegram_copy_ko"]) {
    for (const invalidText of ["", " \n\t"]) {
      assert.throws(() => renderBatchReviewDetail({
        ...validDetail,
        result_payload: {
          ...validDetail.result_payload,
          [field]: invalidText,
        },
      }), /invalid_batch_review_detail/);
    }
  }
});
