import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consoleHtml = readFileSync(
  new URL("../web/console/index.html", import.meta.url),
  "utf8",
);

test("distinguishes missing copy from a rejected Squid subtitle placement", () => {
  assert.match(consoleHtml, /visual_localization_status === "unsafe_placement"/);
  assert.match(consoleHtml, /원문 자막을 자연스럽게 교체하기 어려워 원본 비주얼 유지/);
  assert.match(consoleHtml, /원문 자막을 같은 위치의 한국어로 교체/);
  assert.match(consoleHtml, /번역할 문구가 없어 원본 비주얼 그대로 유지/);
});

test("passes the Railway-cleaned source file to the Figma editable endpoint", () => {
  assert.match(consoleHtml, /source_visual_file: payload\.source_visual_file \|\| ""/);
});

test("links a matching generated card back to its approved Figma template", () => {
  assert.match(consoleHtml, /id="figma-template-link"/);
  assert.match(consoleHtml, /function showApprovedFigmaTemplate\(reference\)/);
  assert.match(consoleHtml, /reference\.node_id\.replace\(":", "-"\)/);
  assert.match(consoleHtml, /showApprovedFigmaTemplate\(payload\.figma_template\)/);
  assert.match(consoleHtml, /승인 Figma 템플릿 · \$\{reference\.version\}/);
});

test("offers real news, article, and tutorial team modes", () => {
  assert.match(consoleHtml, /data-mode="news"/);
  assert.match(consoleHtml, /data-mode="article"/);
  assert.match(consoleHtml, /data-mode="tutorial"/);
  assert.match(consoleHtml, /제목·리드·3~5개 섹션·핵심 요약/);
  assert.match(consoleHtml, /\/api\/article\/\$\{encodeURIComponent\(requestContext\.client\)\}/);
  assert.match(consoleHtml, /renderArticleResult\(articlePayload, requestContext\)/);
  assert.match(consoleHtml, /\/api\/article-visual\/\$\{encodeURIComponent\(contentId\)\}\/\$\{encodeURIComponent\(visualId\)\}/);
  assert.match(consoleHtml, /prepareArticleBanner\(articlePayload, requestSessionEpoch, requestContext\)/);
  assert.match(consoleHtml, /canvas\.width = width/);
  assert.match(consoleHtml, /canvas\.height = height/);
  assert.match(consoleHtml, /배너 PNG 저장/);
  assert.match(consoleHtml, /Figma 배너 SVG/);
  assert.match(consoleHtml, /data-article-visual-png/);
  assert.match(consoleHtml, /비주얼 3장/);
  assert.match(consoleHtml, /id="article-markdown"/);
  assert.match(consoleHtml, /data-copy-target="article-markdown"/);
  assert.match(consoleHtml, /sourceContent\.maxLength = articleMode \? 60_000 : 20_000/);
  assert.match(consoleHtml, /원문 본문을 300자 이상/);
  assert.match(consoleHtml, /현재 튜토리얼 생성은 Yellow와 Squid만 지원/);
  assert.match(consoleHtml, /\/api\/tutorial\/\$\{encodeURIComponent\(requestContext\.client\)\}/);
  assert.equal((consoleHtml.match(/"Idempotency-Key": generationRequestId/g) || []).length, 3);
  assert.match(consoleHtml, /state\.generationRequest = null/);
  assert.match(consoleHtml, /아티클은 링크만으로 만들 수 없으며 원문 본문을 300자 이상/);
});

test("marks failed visual localization as requiring a manual review", () => {
  assert.match(consoleHtml, /id="review-warning"/);
  assert.match(consoleHtml, /검토 필요/);
  assert.match(consoleHtml, /자막 영역 지정 기능 · 후속 지원/);
  assert.match(consoleHtml, /드래그 선택이나 좌표 재생성을 지원하지 않으며 선택 좌표도 저장하지 않습니다/);
  assert.match(consoleHtml, /visualLocalizationNeedsReview\(payload\)/);
});

test("renders deterministic brand QA without treating it as publication approval", () => {
  assert.match(consoleHtml, /id="brand-qa-panel"[^>]+aria-live="polite"/);
  assert.match(consoleHtml, /function normalizeBrandQa\(value\)/);
  assert.match(consoleHtml, /report\.policy_version !== "brand-qa@1"/);
  assert.match(consoleHtml, /function showBrandQa\(rawReport\)/);
  assert.match(consoleHtml, /showBrandQa\(payload\.brand_qa\)/);
  assert.match(consoleHtml, /renderBrandQaMarkup\(generationMeta\.brand_qa\)/);
  assert.match(consoleHtml, /최종 사실·브랜드·이미지 검토 후 승인/);
  assert.match(consoleHtml, /자동으로 게시되지는 않습니다/);
  assert.match(consoleHtml, /escapeHtml\(check\.label\)/);
  assert.match(consoleHtml, /escapeHtml\(check\.detail\)/);
  assert.match(consoleHtml, /clearBrandQa\(\)/);
});

test("preserves the existing news-card, editable SVG, and channel-copy flow", () => {
  assert.match(consoleHtml, /const state = \{ mode: "news"/);
  assert.match(consoleHtml, /fetch\(`\/api\/news-card\/\$\{encodeURIComponent\(requestContext\.client\)\}`/);
  assert.match(consoleHtml, /template_style: requestContext\.template/);
  assert.match(consoleHtml, /prepareEditableDownload\(payload, requestSessionEpoch, requestContext\)/);
  assert.match(consoleHtml, /payload\.channel_copy\?\.telegram/);
  assert.match(consoleHtml, /payload\.channel_copy\?\.x/);
});

test("sends stored results to the private Telegram review flow and opens DM deep links", () => {
  assert.match(consoleHtml, /function notifyTelegramReview\(payload, bannerBlob, requestSessionEpoch, requestContext\)/);
  assert.match(consoleHtml, /\/review-notification`/);
  assert.match(consoleHtml, /formData\.set\("content_version_id", versionId\)/);
  assert.match(consoleHtml, /payload\.mock_mode === true/);
  assert.match(consoleHtml, /reviewNotificationVersions\.has\(versionId\)/);
  assert.match(consoleHtml, /await notifyTelegramReview\(articlePayload, articleBannerBlob \|\| null/);
  assert.match(consoleHtml, /await notifyTelegramReview\(tutorialPayload, null/);
  assert.match(consoleHtml, /await notifyTelegramReview\(payload, null/);
  assert.match(consoleHtml, /initialQuery\.get\("content"\)/);
  assert.match(consoleHtml, /function openInitialReviewLink\(\)/);
  assert.match(consoleHtml, /selectStudioView\("library", false\)/);
  assert.match(consoleHtml, /initialReviewRef = initialBatchReviewRef \|\| initialReviewContentId/);
  assert.match(consoleHtml, /loadLibraryDetail\(initialReviewRef\)/);
});

test("binds generation responses and editable SVG follow-ups to the submitted client and mode", () => {
  assert.match(consoleHtml, /const requestContext = Object\.freeze\(\{[\s\S]*mode: state\.mode,[\s\S]*client: state\.client,[\s\S]*template: state\.template/);
  assert.match(consoleHtml, /function generationContextIsCurrent\(requestContext\)/);
  assert.match(consoleHtml, /if \(requestSessionEpoch !== state\.sessionEpoch \|\| !generationContextIsCurrent\(requestContext\)\) return;/);
  assert.match(consoleHtml, /sourceContent\.value\.trim\(\) === requestContext\.sourceContent/);
  assert.match(consoleHtml, /normalizeUserUrl\(sourceUrl\.value\) === requestContext\.sourceUrl/);
  assert.match(consoleHtml, /sourceType\.value === requestContext\.sourceType/);
  assert.match(consoleHtml, /function prepareEditableDownload\(payload, sessionEpoch, requestContext\)/);
  assert.match(consoleHtml, /encodeURIComponent\(requestContext\.client\)/);
  assert.match(consoleHtml, /downloadSvg\.download = `\$\{requestContext\.client\}-\$\{templateStyle\}-figma-editable\.svg`/);
});

test("uses a server-side team session without exposing or persisting the access code", () => {
  assert.match(consoleHtml, /id="access-gate"/);
  assert.match(consoleHtml, /id="access-code"[^>]+type="password"/);
  assert.match(consoleHtml, /fetch\("\/api\/studio-session"/);
  assert.match(consoleHtml, /JSON\.stringify\(\{ access_code: accessCode\.value \}\)/);
  assert.match(consoleHtml, /credentials: "same-origin"/);
  assert.match(consoleHtml, /STUDIO_ACCESS_TOKEN/);
  assert.match(consoleHtml, /handleStudioAccessResponse\(response, payload\)/);
  assert.match(consoleHtml, /if \(!response\.ok\) throw new Error\("logout_failed"\)/);
  assert.doesNotMatch(consoleHtml, /localStorage|sessionStorage/);
  assert.doesNotMatch(consoleHtml, /API_SECRET\s*=/);
});

test("scrubs generated work and invalidates in-flight responses when Studio locks", () => {
  assert.match(consoleHtml, /function scrubStudioWork\(\) \{[\s\S]*sourceContent\.value = "";[\s\S]*sourceUrl\.value = "";[\s\S]*state\.generationRequest = null;[\s\S]*download\.removeAttribute\("href"\);[\s\S]*renderBrand\(\);/);
  assert.match(consoleHtml, /function renderBrand\(\) \{[\s\S]*resultImage\.removeAttribute\("src"\);[\s\S]*clearEditableDownload\(\);[\s\S]*clearTutorialResult\(\);[\s\S]*clearArticleResult\(\);[\s\S]*telegramCopy\.value = "";[\s\S]*xCopy\.value = "";/);
  assert.match(consoleHtml, /function lockAndScrubStudio[\s\S]*state\.sessionEpoch \+= 1;[\s\S]*scrubStudioWork\(\);[\s\S]*showStudioAccess/);
  assert.match(consoleHtml, /lockAndScrubStudio\("세션이 만료되었습니다/);
  assert.match(consoleHtml, /lockAndScrubStudio\("로그아웃했습니다/);
  assert.match(consoleHtml, /if \(requestSessionEpoch !== state\.sessionEpoch\) return;/);
  assert.match(consoleHtml, /prepareEditableDownload\(payload, requestSessionEpoch, requestContext\)/);
});

test("keeps mock tutorials visibly marked as samples that must not be published", () => {
  assert.match(consoleHtml, /id="tutorial-mock-warning"[^>]+role="alert"/);
  assert.match(consoleHtml, /샘플 · 게시 금지/);
  assert.match(consoleHtml, /const isMockTutorial = payload\.mock_mode === true/);
  assert.match(consoleHtml, /const isMockNews = payload\.mock_mode === true/);
  assert.match(consoleHtml, /channelCopy\.hidden = isMockNews \|\|/);
  assert.match(consoleHtml, /tutorialMockWarning\.hidden = !isMockTutorial/);
  assert.match(consoleHtml, /샘플 렌더 · 게시 금지/);
  assert.match(consoleHtml, /승인·게시할 수 있는 완성본이 아닙니다/);
});

test("counts X copy with the same weighted Unicode ranges as the server", () => {
  const functionSource = consoleHtml.match(
    /function xWeightedLength\(value\) \{[\s\S]*?\n      \}(?=\n\n      function updateCopyCounts)/,
  )?.[0];
  assert.ok(functionSource, "xWeightedLength must be present in the console");
  const xWeightedLength = Function(
    `"use strict"; ${functionSource}; return xWeightedLength;`,
  )() as (value: string) => number;

  assert.equal(xWeightedLength("ABC"), 3);
  assert.equal(xWeightedLength("한글"), 4);
  assert.equal(xWeightedLength("ABC 한글"), 8);
  assert.equal(xWeightedLength("한".repeat(141)), 282);
  assert.match(consoleHtml, /const weightedLength = xWeightedLength\(xCopy\.value\)/);
  assert.match(consoleHtml, /xCopy\.setAttribute\("aria-invalid", String\(overLimit\)\)/);
});

test("explains durable tutorial storage setup and failures to team members", () => {
  assert.match(consoleHtml, /durable_storage_not_configured/);
  assert.match(consoleHtml, /생성된 \$\{contentLabel\}는 임시 결과로 제공하지 않습니다/);
  assert.match(consoleHtml, /durable_storage_bucket_must_be_private/);
  assert.match(consoleHtml, /durable_storage_scope_not_ready/);
  assert.match(consoleHtml, /durable_storage_upload_failed/);
  assert.match(consoleHtml, /durable_catalog_result_unknown/);
  assert.match(consoleHtml, /tutorial_deadline_exceeded/);
  assert.match(consoleHtml, /news_card_deadline_exceeded/);
  assert.match(consoleHtml, /article_deadline_exceeded/);
  assert.match(consoleHtml, /payload\.error\.endsWith\("_idempotency_conflict"\)\) state\.generationRequest = null/);
  assert.match(consoleHtml, /confirmResultReset\(\)/);
});

test("recovers an article from durable storage after an empty gateway timeout", () => {
  assert.match(consoleHtml, /function articleResultUrl\(clientId, requestId\)/);
  assert.match(consoleHtml, /async function recoverStoredArticle\(/);
  assert.match(consoleHtml, /\/api\/article-result\//);
  assert.match(consoleHtml, /\[502, 504\]\.includes\(articleResponse\.status\)/);
  assert.match(consoleHtml, /!articlePayload\?\.error/);
  assert.match(consoleHtml, /팀 보관함에 저장된 결과를 확인하고 있습니다/);
  assert.match(consoleHtml, /payload: \{ error: "durable_catalog_result_unknown" \}/);
  assert.match(consoleHtml, /const deadline = Date\.now\(\) \+ 40_000/);
});
