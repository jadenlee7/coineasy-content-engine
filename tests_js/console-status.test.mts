import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consoleHtml = readFileSync(
  new URL("../web/console/index.html", import.meta.url),
  "utf8",
);

test("distinguishes missing copy from a rejected Squid subtitle placement", () => {
  assert.match(consoleHtml, /visual_localization_status === "unsafe_placement"/);
  assert.match(consoleHtml, /원문 비주얼은 그대로 유지하고 한국어 게시 문구로 보완/);
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
  assert.match(consoleHtml, /미승인 배너 PNG 참고본/);
  assert.match(consoleHtml, /미승인 배너 SVG 참고본/);
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
  assert.match(consoleHtml, /payload\?\.error === "fact_check_regeneration_required"[\s\S]*state\.generationRequest = null/);
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

test("keeps Squid news creation on the reviewed remix or classic visual family", () => {
  assert.match(consoleHtml, /const SQUID_NEWS_TEMPLATES = new Set\(\["remix", "classic"\]\)/);
  assert.match(consoleHtml, /state\.client === "squid" && !SQUID_NEWS_TEMPLATES\.has\(state\.template\)[\s\S]*state\.template = "remix"/);
  assert.match(consoleHtml, /item\.disabled = disabled/);
  assert.match(consoleHtml, /if \(!button \|\| button\.disabled\) return/);
  assert.match(consoleHtml, /data-client="squid"\]\[data-template="classic"\][\s\S]*squid-squib-token-juggle\.png/);
  assert.match(consoleHtml, /--brand-primary:#efff5a; --brand-secondary:#e6ccfc; --brand-bg:#e6ccfc/);
  assert.match(consoleHtml, /state\.client === "squid" && state\.template === "classic"[\s\S]*squid-light\.png/);
  assert.match(consoleHtml, /detail\.client_id === "squid" && \["editorial", "signal"\]\.includes\(storedTemplateStyle\)[\s\S]*\? "classic"/);
  assert.match(consoleHtml, /Squid 원문 우선 · 추천/);
  assert.match(consoleHtml, /@squidrouter 배너 유지 \+ 한국어 문구/);
  assert.match(consoleHtml, /Squid 클래식 · fallback/);
  assert.match(consoleHtml, /const changed = state\.client !== clientId;[\s\S]*if \(changed && clientId === "squid"\) state\.template = "remix"/);
  assert.match(consoleHtml, /function isOfficialSquidXStatusUrl\(value\)/);
  assert.match(consoleHtml, /function hasOfficialSquidSource\(contentValue, urlValue\)/);
  assert.match(consoleHtml, /hasOfficialSquidSource\(requestContext\.sourceContent, payload\.source_url \|\| requestContext\.sourceUrl\)/);
  assert.match(consoleHtml, /shell\.dataset\.output = "source-native"/);
  assert.match(consoleHtml, /--source-native-aspect/);
  assert.match(consoleHtml, /payload\.output_width/);
  assert.match(consoleHtml, /payload\.output_height/);
  assert.match(consoleHtml, /aspect-ratio: var\(--asset-aspect, 1\)/);
  assert.match(consoleHtml, /--asset-aspect:\$\{width\} \/ \$\{height\}/);
  assert.match(consoleHtml, /\.library-thumb img \{[^}]*object-fit: contain/);
  assert.match(consoleHtml, /function clearArticleBanner\(\)[\s\S]*shell\.removeAttribute\("data-output"\)[\s\S]*shell\.style\.removeProperty\("--source-native-aspect"\)/);
  assert.match(consoleHtml, /공식 @squidrouter 원문 배너를 그대로 가져와 한국어 게시 문구와 함께 준비/);
  assert.match(consoleHtml, /클래식 fallback은 원문 배너를 사용하지 않습니다/);
  assert.match(consoleHtml, /source_not_official_squid/);
  assert.match(consoleHtml, /공식 @squidrouter 계정의 원문인지 확인되지 않았습니다/);
  assert.doesNotMatch(consoleHtml, /sourceUrl\.addEventListener\("blur"[\s\S]{0,300}setStatus/);
});

test("defaults a newly selected Squid client to remix without overwriting an explicit Squid classic choice", () => {
  const selectClientSource = consoleHtml.match(
    /function selectClient\(clientId\) \{[\s\S]*?\n      \}(?=\n\n      function syncTemplateOptions)/,
  )?.[0];
  assert.ok(selectClientSource, "selectClient must be present in the console");
  const state = { client: "yellow", template: "classic" };
  const clients = { querySelectorAll: () => [] };
  const selectClient = Function(
    "state",
    "clients",
    `"use strict"; ${selectClientSource}; return selectClient;`,
  )(state, clients) as (clientId: string) => void;

  selectClient("squid");
  assert.deepEqual(state, { client: "squid", template: "remix" });
  state.template = "classic";
  selectClient("squid");
  assert.deepEqual(state, { client: "squid", template: "classic" });
});

test("uses the same X-link priority as the server when identifying an official Squid source", () => {
  const helperNames = ["normalizeUserUrl", "isXStatusUrl", "isOfficialSquidXStatusUrl", "hasOfficialSquidSource"];
  const helperSources = helperNames.map((name, index) => {
    const nextName = helperNames[index + 1];
    const lookahead = nextName ? `(?=\\n\\n      function ${nextName})` : "(?=\\n\\n      function errorMessage)";
    const source = consoleHtml.match(new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n      \\}${lookahead}`))?.[0];
    assert.ok(source, `${name} must be present in the console`);
    return source;
  }).join("\n");
  const hasOfficialSquidSource = Function(
    `"use strict"; ${helperSources}; return hasOfficialSquidSource;`,
  )() as (contentValue: string, urlValue: string) => boolean;

  const official = "https://x.com/squidrouter/status/2083266484789514640";
  assert.equal(hasOfficialSquidSource(official, "https://docs.example.com/story"), true);
  assert.equal(hasOfficialSquidSource(official, "https://x.com/other/status/2083266484789514640"), false);
  assert.equal(hasOfficialSquidSource("본문", official), true);
  assert.equal(hasOfficialSquidSource("본문", `${official}/photo/1`), true);
  assert.equal(hasOfficialSquidSource("본문", `${official}/arbitrary`), false);
  assert.equal(hasOfficialSquidSource("본문", "https://x.com/squidrouter/status/208326648478951464012"), false);
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

test("labels every pre-approval tutorial and article output as review-only", () => {
  assert.match(consoleHtml, /미승인 검토용 튜토리얼/);
  assert.match(consoleHtml, /미승인 PNG 참고본/);
  assert.match(consoleHtml, /이중 사실 확인 승인 전에는 PNG를 게시하지 마세요/);
  assert.match(consoleHtml, /미승인 검토용 Railway 원고/);
  assert.match(consoleHtml, /현재 원고와 게시 문구도 이중 사실 확인 승인 전에는 사용할 수 없습니다/);
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
  assert.match(consoleHtml, /payload\?\.error === "fact_check_regeneration_required"[\s\S]*state\.generationRequest = null/);
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
