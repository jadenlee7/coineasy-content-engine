import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consoleHtml = readFileSync(
  new URL("../web/console/index.html", import.meta.url),
  "utf8",
);

test("offers a simple create and team-library switch without replacing generator modes", () => {
  assert.match(consoleHtml, /data-studio-view="create"[^>]*>만들기</);
  assert.match(consoleHtml, /data-studio-view="library"[^>]*>팀 보관함</);
  assert.match(consoleHtml, /id="create-view"/);
  assert.match(consoleHtml, /id="library-view"[^>]+hidden/);
  assert.match(consoleHtml, /data-mode="news"/);
  assert.match(consoleHtml, /data-mode="article"/);
  assert.match(consoleHtml, /data-mode="tutorial"/);
});

test("loads newest library pages through the authenticated server route", () => {
  assert.match(consoleHtml, /new URLSearchParams\(\{ limit: "12" \}\)/);
  assert.match(consoleHtml, /params\.set\("client", libraryClientFilter\.value\)/);
  assert.match(consoleHtml, /params\.set\("kind", libraryKindFilter\.value\)/);
  assert.match(consoleHtml, /params\.set\("status", libraryStatusFilter\.value\)/);
  assert.match(consoleHtml, /params\.set\("before_created_at", libraryState\.nextCursor\.created_at\)/);
  assert.match(consoleHtml, /params\.set\("before_id", libraryState\.nextCursor\.id\)/);
  assert.match(consoleHtml, /fetch\(`\/api\/library\?\$\{params\.toString\(\)\}`/);
  assert.match(consoleHtml, /credentials: "same-origin"/);
  assert.match(consoleHtml, /payload\.next_cursor/);
  assert.match(consoleHtml, />더 불러오기</);
});

test("uses the durable catalog status vocabulary in filters and labels", () => {
  assert.match(consoleHtml, /<option value="rejected">수정 요청<\/option>/);
  assert.match(consoleHtml, /<option value="scheduled">예약됨<\/option>/);
  assert.match(consoleHtml, /rejected: "수정 요청"/);
  assert.match(consoleHtml, /scheduled: "예약됨"/);
  assert.doesNotMatch(consoleHtml, /changes_requested/);
});

test("loads safe detail DTOs for news, articles, and tutorials", () => {
  assert.match(consoleHtml, /fetch\(`\/api\/library\/\$\{encodeURIComponent\(contentId\)\}`/);
  assert.match(consoleHtml, /contentKind === "article"/);
  assert.match(consoleHtml, /contentKind === "tutorial"/);
  assert.match(consoleHtml, /const spec = plainObject\(content\.spec\)/);
  assert.match(consoleHtml, /item\.signed_url \|\| item\.url/);
  assert.match(consoleHtml, /const currentVersion = plainObject\(detail\.current_version\)/);
  assert.match(consoleHtml, /generationMeta\.mock_mode === true/);
  assert.match(consoleHtml, /샘플 · 게시 금지/);
  assert.match(consoleHtml, /id="library-telegram-copy"/);
  assert.match(consoleHtml, /id="library-x-copy"/);
  assert.doesNotMatch(consoleHtml, /storage_path/);
  assert.doesNotMatch(consoleHtml, /card\.dataset\.contentId === libraryState\.activeId/);
});

test("records explicit approve or change-request decisions without auto publishing", () => {
  assert.match(consoleHtml, /팀 검토 · 브랜드 학습/);
  assert.match(consoleHtml, /data-library-review="approved"/);
  assert.match(consoleHtml, /data-library-review="rejected"/);
  assert.match(consoleHtml, /data-library-review-reason/);
  assert.match(consoleHtml, /메모 원문은 모델에 전달되지 않습니다/);
  assert.match(consoleHtml, /샘플은 승인 불가/);
  assert.match(consoleHtml, /fetch\(`\/api\/library\/\$\{encodeURIComponent\(activeContentId\)\}\/review`/);
  assert.match(consoleHtml, /"Idempotency-Key": reviewRequest\.id/);
  assert.match(consoleHtml, /reason_codes: reasonCodes/);
  assert.match(consoleHtml, /function factCheckReviewState\(rawGenerationMeta, expectedContentKind\)/);
  assert.match(consoleHtml, /report\.policy_version === "double-fact-check@1"/);
  assert.match(consoleHtml, /data-library-fact-check-attestation="source_facts_verified"/);
  assert.match(consoleHtml, /data-library-fact-check-attestation="output_claims_verified"/);
  assert.match(consoleHtml, /fact_check: factCheckAttestation/);
  assert.match(consoleHtml, /fact_check_policy_version/);
  assert.match(consoleHtml, /source_facts_verified/);
  assert.match(consoleHtml, /output_claims_verified/);
  assert.match(consoleHtml, /기존 승인 · 이중 사실 확인 재검증 필요/);
  assert.match(consoleHtml, /같은 공식 원문으로 새 버전을 생성한 뒤 이중 사실 확인을 완료해주세요/);
  assert.match(consoleHtml, /window\.confirm\(`\$\{decisionLabel\}을 기록할까요\?/);
  assert.match(consoleHtml, /이 작업은 게시를 실행하지 않지만/);
  assert.match(consoleHtml, /선택한 사유 코드만 다음 생성의 주의점에 반영됩니다/);
  const reviewFunction = consoleHtml.match(
    /async function submitStoredReview\(button\) \{[\s\S]*?\n      \}(?=\n\n      function publicationContextIsCurrent)/,
  )?.[0] || "";
  assert.doesNotMatch(reviewFunction, /publication|publish/);
});

test("keeps generated and stored post copy in review mode until attested approval", () => {
  assert.doesNotMatch(consoleHtml, /복사해서 바로 게시하기/);
  assert.match(consoleHtml, /<h3>검토용 게시 문구<\/h3>/);
  assert.match(consoleHtml, /data-copy-target="telegram-copy" disabled>승인 후 복사/);
  assert.match(consoleHtml, /data-copy-target="x-copy" disabled>승인 후 복사/);
  assert.match(consoleHtml, /id="article-markdown"[^>]*readonly/);
  assert.match(consoleHtml, /const copyAllowed = !isMock[\s\S]*detail\.status === "approved"[\s\S]*hasAttestedDoubleFactCheckApproval/);
  assert.match(consoleHtml, /copyFactCheck\.approvalEligible/);
  assert.match(consoleHtml, /검토용 미리보기입니다\. 공식 원문과 최종 주장을 모두 확인한 현재 버전만 복사할 수 있습니다/);
});

test("publishes only the exact server-allowed approved Telegram version", () => {
  assert.match(consoleHtml, /function renderTelegramPublishMarkup\(rawDetail, isMock, assets, channelCopyValue\)/);
  assert.match(consoleHtml, /capabilities\.telegram !== true\s*\|\| capabilities\.telegram_client_allowed !== true/);
  assert.match(consoleHtml, /detail\.content_kind !== "daily_news"/);
  assert.match(consoleHtml, /function hasAttestedDoubleFactCheckApproval\(rawReview, contentVersionId\)/);
  assert.match(consoleHtml, /review\.fact_check_policy_version === "double-fact-check@1"/);
  assert.match(consoleHtml, /review\.source_facts_verified === true/);
  assert.match(consoleHtml, /review\.output_claims_verified === true/);
  assert.match(consoleHtml, /factCheck\.approvalEligible/);
  assert.match(consoleHtml, /generationMeta\.mock_mode === true/);
  assert.match(consoleHtml, /asset\.asset_kind === "png" && asset\.mime_type === "image\/png"/);
  assert.match(consoleHtml, /data-library-publish-channel="telegram"/);
  assert.match(consoleHtml, /window\.confirm\(`\$\{brandName\} · v\$\{versionNumber\} 승인본을 Telegram 공개 채널에 실제 게시할까요\?/);
  assert.match(consoleHtml, /fetch\(`\/api\/library\/\$\{encodeURIComponent\(context\.contentItemId\)\}\/publish`, \{/);
  assert.match(consoleHtml, /"Idempotency-Key": intent\.id/);
  assert.match(consoleHtml, /body: JSON\.stringify\(\{\s*content_version_id: context\.contentVersionId,\s*channel: "telegram"\s*\}\)/);
  assert.match(consoleHtml, /TELEGRAM_PUBLICATION_POLL_TIMEOUT_MS = 30_000/);
  assert.match(consoleHtml, /delivery_unknown: "전송 결과 확인 필요"/);
  assert.match(consoleHtml, /function formatTelegramDeliveryTime\(value\)/);
  assert.match(consoleHtml, /if \(typeof value !== "string" \|\| !value\) return "";/);
  assert.match(consoleHtml, /timeZone: "Asia\/Seoul"/);
  assert.match(consoleHtml, /Telegram 전송 시도 시각/);
  assert.match(consoleHtml, /delivery_started_at: deliveryStartedAt/);
  assert.match(consoleHtml, /중복 게시를 막기 위해 자동 재시도하지 않습니다/);
  assert.match(consoleHtml, /data-library-publish-resolution="cancel-unobserved"/);
  assert.match(consoleHtml, /function resolveUnknownTelegramPublication\(button\)/);
  assert.match(consoleHtml, /phrase !== "미발행 확인"/);
  assert.match(consoleHtml, /\/publish-resolution`, \{/);
  assert.match(consoleHtml, /resolution: "confirmed_not_observed_cancelled"/);
  assert.match(consoleHtml, /channel_checked: true/);
  assert.match(consoleHtml, /caption_checked: true/);
  assert.match(consoleHtml, /png_checked: true/);
  assert.match(consoleHtml, /telegram_publication_client_not_allowed/);
  assert.doesNotMatch(consoleHtml, /PUBLICATION_WORKER_TOKEN|X-Publication-Worker-Key/);
});

test("accepts only a complete current-version fact-check report in the browser", () => {
  const functionSource = consoleHtml.match(
    /function factCheckReviewState\(rawGenerationMeta, expectedContentKind\) \{[\s\S]*?\n      \}(?=\n\n      function renderContentReviewMarkup)/,
  )?.[0];
  assert.ok(functionSource, "factCheckReviewState must be present");
  const factCheckReviewState = Function(
    "plainObject",
    `"use strict"; ${functionSource}; return factCheckReviewState;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
  ) as (value: unknown, kind: string) => { approvalEligible: boolean; status: string };
  const report = {
    schema_version: "1.0",
    policy_version: "double-fact-check@1",
    content_kind: "daily_news",
    status: "review",
    human_review_required: true,
    input_sha256: "a".repeat(64),
    output_sha256: "b".repeat(64),
    checks: [
      { id: "source_evidence", status: "review", label: "Source evidence", detail: "Human review required.", metrics: {} },
      { id: "output_claims", status: "pass", label: "Output claims", detail: "Mechanical anchors recorded.", metrics: {} },
    ],
  };
  assert.equal(factCheckReviewState({ fact_check: report }, "daily_news").approvalEligible, true);
  assert.equal(factCheckReviewState({ fact_check: report }, "article").approvalEligible, false);
  assert.equal(factCheckReviewState({ fact_check: { ...report, input_sha256: "short" } }, "daily_news").approvalEligible, false);
  assert.equal(factCheckReviewState({ fact_check: { ...report, checks: report.checks.slice(0, 1) } }, "daily_news").approvalEligible, false);
  assert.equal(factCheckReviewState({ fact_check: { ...report, status: "pass" } }, "daily_news").approvalEligible, false);
  assert.equal(factCheckReviewState({ fact_check: { ...report, status: "blocked", checks: report.checks.map(check => ({ ...check, status: "blocked" })) } }, "daily_news").approvalEligible, false);
});

test("accepts only canonical delivery timestamps in the browser publication DTO", () => {
  const functionSource = consoleHtml.match(
    /function normalizedTelegramPublication\(rawPublication, expectedContentId, expectedVersionId\) \{[\s\S]*?\n      \}(?=\n\n      function renderTelegramPublishMarkup)/,
  )?.[0];
  assert.ok(functionSource, "normalizedTelegramPublication must be present");
  const normalizedTelegramPublication = Function(
    "plainObject",
    "safeWebUrl",
    `"use strict"; ${functionSource}; return normalizedTelegramPublication;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    (value: unknown) => typeof value === "string" && value.startsWith("https://") ? value : "",
  ) as (value: unknown, contentId: string, versionId: string) => Record<string, any> | null;
  const contentId = "22222222-2222-4222-8222-222222222222";
  const versionId = "33333333-3333-4333-8333-333333333333";
  const base = {
    publication_id: "44444444-4444-4444-8444-444444444444",
    content_item_id: contentId,
    content_version_id: versionId,
    channel: "telegram",
    status: "publishing",
    delivery_started_at: "2026-08-01T12:34:56.123Z",
    external_url: null,
    error_code: null,
  };
  assert.equal(
    normalizedTelegramPublication(base, contentId, versionId)?.delivery_started_at,
    "2026-08-01T12:34:56.123Z",
  );
  for (const deliveryStartedAt of [
    "not-an-iso-timestamp",
    "2026-02-30T12:00:00.000Z",
    "2026-08-01T99:00:00.000Z",
    "2026-08-01T12:34:56+00:00",
  ]) {
    assert.equal(normalizedTelegramPublication({
      ...base,
      delivery_started_at: deliveryStartedAt,
    }, contentId, versionId), null);
  }
});

test("renders deduplicated and escaped source evidence from every stored content shape", () => {
  const functionSource = consoleHtml.match(
    /function renderSourceEvidence\(rawContent\) \{[\s\S]*?\n      \}(?=\n\n      function renderStoredContent)/,
  )?.[0];
  assert.ok(functionSource, "renderSourceEvidence must be present in the console");
  const renderSourceEvidence = Function(
    "plainObject",
    "safeWebUrl",
    "escapeHtml",
    `"use strict"; ${functionSource}; return renderSourceEvidence;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    (value: unknown) => {
      if (typeof value !== "string" || !value.trim()) return "";
      try {
        const url = new URL(value, "https://console.example");
        return ["https:", "http:"].includes(url.protocol) ? url.href : "";
      } catch {
        return "";
      }
    },
    (value: unknown) => String(value ?? "").replace(
      /[&<>\"']/g,
      char => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "\"":"&quot;", "'":"&#039;" }[char]),
    ),
  ) as (content: unknown) => string;

  const firstUrl = "https://x.com/SquidRouter/status/123";
  const secondUrl = "https://example.com/source";
  const unsafeText = `<script>alert("source")</script>${"원문".repeat(260)}`;
  const markup = renderSourceEvidence({
    source: {
      url: firstUrl,
      resolved_content: unsafeText,
      submitted_content: "fallback text",
    },
    source_url: firstUrl,
    source_map: [
      { source_url: firstUrl },
      { source_url: "javascript:alert(1)" },
      { source_url: "data:text/html,unsafe" },
      { source_url: secondUrl },
    ],
  });

  assert.equal(markup.split(firstUrl).length - 1, 1);
  assert.equal(markup.split(secondUrl).length - 1, 1);
  assert.doesNotMatch(markup, /javascript:|<script>/);
  assert.match(markup, /&lt;script&gt;alert\(&quot;source&quot;\)&lt;\/script&gt;/);
  assert.match(markup, /<details><summary>원문 본문 펼쳐보기<\/summary>/);
  assert.match(markup, /target="_blank" rel="noopener noreferrer"/);

  const rawSourceMarkup = renderSourceEvidence({ source: "직접 저장된 <원문>" });
  assert.doesNotMatch(rawSourceMarkup, /href=/);
  assert.match(rawSourceMarkup, /직접 저장된 &lt;원문&gt;/);

  const tutorialMarkup = renderSourceEvidence({
    source: { content: "튜토리얼 <본문>" },
  });
  assert.match(tutorialMarkup, /튜토리얼 &lt;본문&gt;/);

  const legacyMarkup = renderSourceEvidence({
    source_url: secondUrl,
    source_content: "이전 형식의 <본문>",
  });
  assert.match(legacyMarkup, /이전 형식의 &lt;본문&gt;/);
  assert.equal(renderSourceEvidence({ source: {} }), "");
});

test("rebuilds durable Figma SVGs from only the stored daily-news render envelope", () => {
  const functionSource = consoleHtml.match(
    /function storedEditableRequest\(rawDetail\) \{[\s\S]*?\n      \}(?=\n\n      function storedPromotionRequest)/,
  )?.[0];
  assert.ok(functionSource, "storedEditableRequest must be present in the console");
  const storedEditableRequest = Function(
    "plainObject",
    "BRAND",
    `"use strict"; ${functionSource}; return storedEditableRequest;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    { yellow: {}, origintrail: {}, squid: {}, babylon: {} },
  ) as (detail: unknown) => Record<string, any> | null;

  const expiredRemixRequest = storedEditableRequest({
    client_id: "squid",
    content_kind: "daily_news",
    current_version: {
      content: {
        spec: { headline: "한국어 뉴스" },
        source: {
          image_url: "https://pbs.twimg.com/media/source.jpg",
          resolved_content: "편집 API에 보내면 안 되는 원문",
        },
        render: {
          requested_template_style: "classic",
          template_style: "remix",
          source_visual_file: "squid/news_123/source_visual_cleaned.jpg",
        },
        api_secret: "never-send",
      },
    },
  });

  assert.equal(expiredRemixRequest, null);

  const request = storedEditableRequest({
    client_id: "squid",
    content_kind: "daily_news",
    current_version: {
      content: {
        spec: { headline: "한국어 뉴스" },
        source: {
          resolved_content: "편집 API에 보내면 안 되는 원문",
        },
        render: {
          requested_template_style: "remix",
          template_style: "classic",
          source_visual_file: "squid/news_123/source_visual_cleaned.jpg",
        },
        api_secret: "never-send",
      },
    },
  });

  assert.deepEqual(request, {
    clientId: "squid",
    templateStyle: "classic",
    payload: {
      spec: { headline: "한국어 뉴스" },
      template_style: "classic",
      source_image_url: "",
      source_visual_file: "",
    },
  });
  assert.doesNotMatch(JSON.stringify(request), /resolved_content|api_secret|never-send/);
  assert.equal(storedEditableRequest({
    client_id: "squid",
    content_kind: "article",
    current_version: { content: { spec: { headline: "아티클" }, render: { template_style: "classic" } } },
  }), null);
  assert.equal(storedEditableRequest({
    client_id: "squid",
    content_kind: "daily_news",
    current_version: { content: { spec: {}, render: { template_style: "classic" } } },
  }), null);

  assert.match(consoleHtml, /data-library-editable>미승인 편집 참고용 SVG 받기</);
  assert.match(consoleHtml, /fetch\(`\/api\/editable-card\/\$\{encodeURIComponent\(request\.clientId\)\}`/);
  assert.match(consoleHtml, /credentials: "same-origin"/);
  assert.match(consoleHtml, /handleStudioAccessResponse\(response, errorPayload\)/);
  assert.match(consoleHtml, /URL\.revokeObjectURL\(blobUrl\)/);
  assert.match(consoleHtml, /activeContentId !== libraryState\.activeId/);
  assert.match(consoleHtml, /생성 직후 받은 SVG도 미승인이며 수정 후 새 버전 등록과 재검토가 필요합니다/);
  assert.match(consoleHtml, /SVG를 수정하면 승인된 현재 버전과 다른 미승인 파생본/);
});

test("prefills deeper work only from an exact performance recommendation and source-ready official evidence", () => {
  const functionSource = consoleHtml.match(
    /function storedPromotionRequest\(rawDetail, recommendationId\) \{[\s\S]*?\n      \}(?=\n\n      function promotionReasonLabel)/,
  )?.[0];
  assert.ok(functionSource, "storedPromotionRequest must be present in the console");
  const storedPromotionRequest = Function(
    "plainObject",
    "TUTORIAL_CLIENTS",
    "safeWebUrl",
    `"use strict"; ${functionSource}; return storedPromotionRequest;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    new Set(["yellow", "squid"]),
    (value: unknown) => typeof value === "string" && value.startsWith("https://")
      ? value
      : "",
  ) as (detail: unknown, targetKind: string) => Record<string, any> | null;

  const officialSource = "공식 원문 근거입니다. ".repeat(30);
  const versionId = "33333333-3333-4333-8333-333333333333";
  const tutorialId = "44444444-4444-4444-8444-444444444444";
  assert.deepEqual(storedPromotionRequest({
    client_id: "yellow",
    content_kind: "daily_news",
    current_version_id: versionId,
    promotion_recommendations: [{
      recommendation_id: tutorialId,
      content_version_id: versionId,
      target_kind: "tutorial",
      source_ready: true,
    }],
    current_version: {
      content_version_id: versionId,
      content: {
        source: {
          submitted_content: officialSource,
          url: "https://x.com/Yellow/status/123",
        },
      },
    },
  }, tutorialId), {
    clientId: "yellow",
    targetKind: "tutorial",
    sourceContent: officialSource.trim(),
    sourceUrl: "https://x.com/Yellow/status/123",
  });
  assert.equal(storedPromotionRequest({
    client_id: "squid",
    content_kind: "daily_news",
    current_version_id: versionId,
    promotion_recommendations: [{
      recommendation_id: tutorialId,
      content_version_id: versionId,
      target_kind: "tutorial",
      source_ready: false,
    }],
    current_version: { content_version_id: versionId, content: { source: { content: officialSource } } },
  }, tutorialId), null);
  assert.equal(storedPromotionRequest({
    client_id: "squid",
    content_kind: "daily_news",
    current_version_id: versionId,
    promotion_recommendations: [{
      recommendation_id: tutorialId,
      content_version_id: versionId,
      target_kind: "tutorial",
      source_ready: true,
    }],
    current_version: { content_version_id: versionId, content: { source: { content: "짧은 공식 원문" } } },
  }, tutorialId), null);
  assert.equal(storedPromotionRequest({
    client_id: "babylon",
    content_kind: "daily_news",
    current_version_id: versionId,
    promotion_recommendations: [{
      recommendation_id: tutorialId,
      content_version_id: versionId,
      target_kind: "tutorial",
      source_ready: true,
    }],
    current_version: { content_version_id: versionId, content: { source: { content: officialSource } } },
  }, tutorialId), null);

  const firstArticleId = "55555555-5555-4555-8555-555555555555";
  const secondArticleId = "66666666-6666-4666-8666-666666666666";
  const sameKindDetail = {
    client_id: "yellow",
    content_kind: "daily_news",
    current_version_id: versionId,
    promotion_recommendations: [
      {
        recommendation_id: firstArticleId,
        content_version_id: versionId,
        target_kind: "article",
        source_ready: false,
      },
      {
        recommendation_id: secondArticleId,
        content_version_id: versionId,
        target_kind: "article",
        source_ready: true,
      },
    ],
    current_version: {
      content_version_id: versionId,
      content: { source: { content: officialSource } },
    },
  };
  assert.equal(storedPromotionRequest(sameKindDetail, firstArticleId), null);
  assert.equal(storedPromotionRequest(sameKindDetail, secondArticleId)?.targetKind, "article");

  assert.match(consoleHtml, /data-library-promotion="\$\{escapeHtml\(item\.recommendation_id\)\}"/);
  assert.match(consoleHtml, /state\.mode = request\.targetKind/);
  assert.match(consoleHtml, /sourceContent\.value = request\.sourceContent/);
  assert.match(consoleHtml, /sourceUrl\.value = request\.sourceUrl/);
  assert.match(consoleHtml, /function continueStoredPromotion\(recommendationId\) \{[\s\S]*if \(generate\.disabled\)[\s\S]*storedPromotionRequest\(libraryState\.activeDetail, recommendationId\)[\s\S]*if \(!confirmResultReset\(\)\) return;[\s\S]*state\.mode = request\.targetKind/);
  assert.doesNotMatch(consoleHtml, /data-library-tutorial/);
  assert.match(consoleHtml, /추천은 자동 게시되지 않으며 공식 원문 확인 후 시작됩니다/);
});

test("links only already-public posts and explains the observation window", () => {
  assert.match(consoleHtml, /id="library-publication-\$\{channel\}"/);
  assert.match(consoleHtml, /row\("x", "X 링크"/);
  assert.match(consoleHtml, /row\("telegram", "Telegram 링크"/);
  assert.match(consoleHtml, /data-library-publication="\$\{channel\}"/);
  assert.match(consoleHtml, /직접 게시한 공개 링크를 연결하면/);
  assert.match(consoleHtml, /이 버튼은 게시를 실행하거나 게시물을 수정하지 않습니다/);
  assert.match(consoleHtml, /fetch\(`\/api\/library\/\$\{encodeURIComponent\(activeContentId\)\}\/performance`/);
  assert.match(consoleHtml, /body: JSON\.stringify\(\{ content_version_id: contentVersionId, channel, external_url: externalUrl \}\)/);
  assert.match(consoleHtml, /12시간 이상 반응이 쌓인 뒤/);
  assert.match(consoleHtml, /publication_observation_conflict/);
  assert.match(consoleHtml, /performanceRequest: \{ x: 0, telegram: 0 \}/);
  assert.match(consoleHtml, /requestId = \+\+libraryState\.performanceRequest\[channel\]/);
  const recordFunction = consoleHtml.match(
    /async function recordStoredPublication\(button\) \{[\s\S]*?\n      \}(?=\n\n      function continueStoredPromotion)/,
  )?.[0] || "";
  assert.match(recordFunction, /requestId === libraryState\.performanceRequest\[channel\]/);
  assert.match(recordFunction, /button\.disabled = connected/);
  assert.doesNotMatch(recordFunction, /exportRequest/);
  assert.match(consoleHtml, /manual_publications/);
  assert.match(consoleHtml, /성과 추천 정보를 지금 불러오지 못했습니다/);
  assert.doesNotMatch(consoleHtml, /공식 원문 300자 이상 보강 후 생성 가능/);
});

test("hides manual Telegram observation only while exact automation owns delivery", () => {
  const functionSource = consoleHtml.match(
    /function renderPublicationObservationMarkup\(rawDetail, isMock\) \{[\s\S]*?\n      \}(?=\n\n      function renderContentReviewMarkup)/,
  )?.[0];
  assert.ok(functionSource, "renderPublicationObservationMarkup must be present");
  const renderPublicationObservationMarkup = Function(
    "plainObject",
    "safeWebUrl",
    "escapeHtml",
    "normalizedTelegramPublication",
    `"use strict"; ${functionSource}; return renderPublicationObservationMarkup;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    (value: unknown) => typeof value === "string" && value.startsWith("https://") ? value : "",
    (value: unknown) => String(value ?? "").replace(/[&<>\"']/g, char => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#039;",
    }[char] || char)),
    (raw: any, contentItemId: string, contentVersionId: string) => {
      const statuses = new Set(["queued", "publishing", "published", "failed", "delivery_unknown", "cancelled"]);
      return raw
        && raw.content_item_id === contentItemId
        && raw.content_version_id === contentVersionId
        && statuses.has(raw.status)
        ? raw
        : null;
    },
  ) as (detail: unknown, isMock: boolean) => string;

  const contentItemId = "22222222-2222-4222-8222-222222222222";
  const contentVersionId = "33333333-3333-4333-8333-333333333333";
  for (const status of ["queued", "publishing", "published"]) {
    const markup = renderPublicationObservationMarkup({
      content_item_id: contentItemId,
      content_kind: "daily_news",
      current_version_id: contentVersionId,
      telegram_publication: {
        content_item_id: contentItemId,
        content_version_id: contentVersionId,
        status,
      },
      manual_publications: [],
    }, false);
    assert.match(markup, /id="library-publication-x"/);
    assert.doesNotMatch(markup, /id="library-publication-telegram"/);
    assert.match(markup, /Telegram은 위 자동 발행 상태에서 관리/);
  }

  const unknownMarkup = renderPublicationObservationMarkup({
    content_item_id: contentItemId,
    content_kind: "daily_news",
    current_version_id: contentVersionId,
    telegram_publication: {
      content_item_id: contentItemId,
      content_version_id: contentVersionId,
      status: "delivery_unknown",
    },
    manual_publications: [],
  }, false);
  assert.match(unknownMarkup, /id="library-publication-telegram"/);
  assert.match(unknownMarkup, /실제로 확인된 기존 메시지 링크만 연결/);
  assert.match(unknownMarkup, /새 발행을 실행하지 않습니다/);

  for (const status of ["failed", "cancelled"]) {
    const terminalMarkup = renderPublicationObservationMarkup({
      content_item_id: contentItemId,
      content_kind: "daily_news",
      current_version_id: contentVersionId,
      telegram_publication: {
        content_item_id: contentItemId,
        content_version_id: contentVersionId,
        status,
      },
      manual_publications: [],
    }, false);
    assert.match(terminalMarkup, /id="library-publication-telegram"/);
  }

  const manualMarkup = renderPublicationObservationMarkup({
    content_item_id: contentItemId,
    content_kind: "daily_news",
    current_version_id: contentVersionId,
    telegram_publication: null,
    manual_publications: [],
  }, false);
  assert.match(manualMarkup, /id="library-publication-x"/);
  assert.match(manualMarkup, /id="library-publication-telegram"/);

  const staleAutomationMarkup = renderPublicationObservationMarkup({
    content_item_id: contentItemId,
    content_kind: "daily_news",
    current_version_id: contentVersionId,
    telegram_publication: {
      content_item_id: contentItemId,
      content_version_id: "44444444-4444-4444-8444-444444444444",
      status: "published",
    },
    manual_publications: [],
  }, false);
  assert.match(staleAutomationMarkup, /id="library-publication-telegram"/);
});

test("permits only web URLs before rendering signed assets and links", () => {
  const functionSource = consoleHtml.match(
    /function safeWebUrl\(value\) \{[\s\S]*?\n      \}(?=\n\n      function formatLibraryDate)/,
  )?.[0];
  assert.ok(functionSource, "safeWebUrl must be present in the console");
  const safeWebUrl = Function(
    "window",
    `"use strict"; ${functionSource}; return safeWebUrl;`,
  )({ location: { origin: "https://console.example" } }) as (value: unknown) => string;

  assert.equal(safeWebUrl("javascript:alert(1)"), "");
  assert.equal(safeWebUrl("data:text/html,unsafe"), "");
  assert.equal(safeWebUrl("https://project.supabase.co/storage/v1/object/sign/card.png?token=signed"), "https://project.supabase.co/storage/v1/object/sign/card.png?token=signed");
  assert.equal(safeWebUrl("/api/safe-thumbnail"), "https://console.example/api/safe-thumbnail");
});

test("shows loading, empty, failure, and retry states in plain Korean", () => {
  assert.match(consoleHtml, /보관함을 불러오는 중입니다/);
  assert.match(consoleHtml, /아직 저장된 작업이 없습니다/);
  assert.match(consoleHtml, /보관함을 불러오지 못했습니다/);
  assert.match(consoleHtml, /작업 상세를 불러오는 중입니다/);
  assert.match(consoleHtml, /작업을 열지 못했습니다/);
  assert.match(consoleHtml, /data-library-retry="list"/);
  assert.match(consoleHtml, /data-library-retry="detail"/);
});

test("scrubs library data and ignores stale list or detail responses after logout", () => {
  assert.match(consoleHtml, /function resetLibraryDetail[\s\S]*libraryState\.detailRequest \+= 1;[\s\S]*libraryState\.reviewRequest = null;[\s\S]*libraryDetail\.innerHTML = "";/);
  assert.match(consoleHtml, /function clearLibrary\(\) \{[\s\S]*libraryState\.listRequest \+= 1;[\s\S]*libraryState\.items = \[\];[\s\S]*resetLibraryDetail\(\);/);
  assert.match(consoleHtml, /if \(reset\) \{[\s\S]*resetLibraryDetail\(\);[\s\S]*보관함을 불러오는 중입니다/);
  assert.match(consoleHtml, /function scrubStudioWork\(\) \{[\s\S]*clearLibrary\(\);[\s\S]*selectStudioView\("create", false\);/);
  assert.match(consoleHtml, /requestSessionEpoch !== state\.sessionEpoch \|\| requestId !== libraryState\.listRequest/);
  assert.match(consoleHtml, /requestSessionEpoch !== state\.sessionEpoch \|\| requestId !== libraryState\.detailRequest/);
  assert.match(consoleHtml, /handleStudioAccessResponse\(response, payload\)/);
});
