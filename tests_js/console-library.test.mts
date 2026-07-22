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
