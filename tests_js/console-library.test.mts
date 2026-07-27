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

test("rebuilds Figma SVGs from only the stored daily-news render envelope", () => {
  const functionSource = consoleHtml.match(
    /function storedEditableRequest\(rawDetail\) \{[\s\S]*?\n      \}(?=\n\n      function storedPromotionRequest)/,
  )?.[0];
  assert.ok(functionSource, "storedEditableRequest must be present in the console");
  const storedEditableRequest = Function(
    "plainObject",
    "BRAND",
    "safeWebUrl",
    `"use strict"; ${functionSource}; return storedEditableRequest;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    { yellow: {}, origintrail: {}, squid: {}, babylon: {} },
    (value: unknown) => typeof value === "string" && value.startsWith("https://")
      ? value
      : "",
  ) as (detail: unknown) => Record<string, any> | null;

  const request = storedEditableRequest({
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

  assert.deepEqual(request, {
    clientId: "squid",
    templateStyle: "remix",
    payload: {
      spec: { headline: "한국어 뉴스" },
      template_style: "remix",
      source_image_url: "https://pbs.twimg.com/media/source.jpg",
      source_visual_file: "squid/news_123/source_visual_cleaned.jpg",
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

  assert.match(consoleHtml, /data-library-editable>Figma 편집용 SVG 받기</);
  assert.match(consoleHtml, /fetch\(`\/api\/editable-card\/\$\{encodeURIComponent\(request\.clientId\)\}`/);
  assert.match(consoleHtml, /credentials: "same-origin"/);
  assert.match(consoleHtml, /handleStudioAccessResponse\(response, errorPayload\)/);
  assert.match(consoleHtml, /URL\.revokeObjectURL\(blobUrl\)/);
  assert.match(consoleHtml, /activeContentId !== libraryState\.activeId/);
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
  assert.match(consoleHtml, /function resetLibraryDetail[\s\S]*libraryState\.detailRequest \+= 1;[\s\S]*libraryDetail\.innerHTML = "";/);
  assert.match(consoleHtml, /function clearLibrary\(\) \{[\s\S]*libraryState\.listRequest \+= 1;[\s\S]*libraryState\.items = \[\];[\s\S]*resetLibraryDetail\(\);/);
  assert.match(consoleHtml, /if \(reset\) \{[\s\S]*resetLibraryDetail\(\);[\s\S]*보관함을 불러오는 중입니다/);
  assert.match(consoleHtml, /function scrubStudioWork\(\) \{[\s\S]*clearLibrary\(\);[\s\S]*selectStudioView\("create", false\);/);
  assert.match(consoleHtml, /requestSessionEpoch !== state\.sessionEpoch \|\| requestId !== libraryState\.listRequest/);
  assert.match(consoleHtml, /requestSessionEpoch !== state\.sessionEpoch \|\| requestId !== libraryState\.detailRequest/);
  assert.match(consoleHtml, /handleStudioAccessResponse\(response, payload\)/);
});
