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
    /function storedEditableRequest\(rawDetail\) \{[\s\S]*?\n      \}(?=\n\n      function storedTutorialRequest)/,
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

test("prefills a manual Yellow or Squid tutorial without changing review state", () => {
  const functionSource = consoleHtml.match(
    /function storedTutorialRequest\(rawDetail\) \{[\s\S]*?\n      \}(?=\n\n      function renderLibraryDetail)/,
  )?.[0];
  assert.ok(functionSource, "storedTutorialRequest must be present in the console");
  const storedTutorialRequest = Function(
    "plainObject",
    "TUTORIAL_CLIENTS",
    "safeWebUrl",
    `"use strict"; ${functionSource}; return storedTutorialRequest;`,
  )(
    (value: unknown) => value && typeof value === "object" && !Array.isArray(value) ? value : {},
    new Set(["yellow", "squid"]),
    (value: unknown) => typeof value === "string" && value.startsWith("https://")
      ? value
      : "",
  ) as (detail: unknown) => Record<string, any> | null;

  assert.deepEqual(storedTutorialRequest({
    client_id: "yellow",
    content_kind: "article",
    current_version: {
      content: {
        source: {
          submitted_content: "튜토리얼로 이어 만들 충분한 공식 원문입니다.",
          url: "https://x.com/Yellow/status/123",
        },
      },
    },
  }), {
    clientId: "yellow",
    sourceContent: "튜토리얼로 이어 만들 충분한 공식 원문입니다.",
    sourceUrl: "https://x.com/Yellow/status/123",
  });
  assert.equal(storedTutorialRequest({
    client_id: "babylon",
    content_kind: "article",
    current_version: { content: { source: { submitted_content: "충분히 긴 공식 원문입니다." } } },
  }), null);
  assert.equal(storedTutorialRequest({
    client_id: "squid",
    content_kind: "tutorial",
    current_version: { content: { source: { content: "이미 생성된 튜토리얼 원문입니다." } } },
  }), null);

  assert.match(consoleHtml, /data-library-tutorial>이 원문으로 튜토리얼 만들기</);
  assert.match(consoleHtml, /state\.mode = "tutorial"/);
  assert.match(consoleHtml, /sourceContent\.value = request\.sourceContent/);
  assert.match(consoleHtml, /sourceUrl\.value = request\.sourceUrl/);
  assert.match(consoleHtml, /function continueStoredTutorial\(\) \{[\s\S]*if \(generate\.disabled\)[\s\S]*if \(!confirmResultReset\(\)\) return;[\s\S]*state\.mode = "tutorial"/);
  assert.match(consoleHtml, /승인이나 게시 상태는 바뀌지 않습니다/);
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
