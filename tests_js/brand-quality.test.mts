import assert from "node:assert/strict";
import test from "node:test";
import {
  evaluateBrandQuality,
  type BrandQaClient,
} from "../netlify/functions/_shared/brand-quality.mts";

const clients: BrandQaClient[] = ["yellow", "origintrail", "squid", "babylon"];

test("passes source-grounded daily news for all four client brands", () => {
  for (const clientId of clients) {
    const squid = clientId === "squid";
    const report = evaluateBrandQuality({
      clientId,
      contentKind: "daily_news",
      sourceText: "공식 업데이트에서 새로운 검증 흐름과 사용자 안내를 공개했습니다.",
      headline: squid ? "어디서 시작할까요?" : "공식 업데이트의 핵심 변화",
      bodyLines: squid
        ? ["Squid로는 쉬워요", "공식 안내에서 확인한 흐름이에요"]
        : ["검증 흐름을 더 명확하게 정리", "사용자가 확인할 기준을 안내"],
      channelCopy: {
        telegram: squid ? "어디서 시작할까요?\nSquid로는 쉬워요." : "공식 업데이트의 핵심 변화를 정리했습니다.",
        x: squid ? "어디서 시작할까요?\nSquid로는 쉬워요." : "공식 업데이트의 핵심 변화를 확인하세요.",
      },
      templateStyle: "classic",
    });
    assert.equal(report.status, "pass", clientId);
    assert.equal(report.score, 100, clientId);
    assert.equal(report.human_review_required, true);
  }
});

test("passes a properly structured source-grounded article", () => {
  const report = evaluateBrandQuality({
    clientId: "origintrail",
    contentKind: "article",
    sourceText: "공식 문서는 데이터 출처와 검증 경로를 연결하는 새 흐름을 설명합니다.",
    title: "데이터 출처를 확인하는 새로운 흐름",
    lead: "공식 문서에 공개된 검증 구조를 한국 사용자 관점에서 정리합니다.",
    sections: [
      { heading: "무엇이 달라졌나", body: "데이터와 출처의 연결 구조가 더 명확해졌습니다." },
      { heading: "왜 중요한가", body: "사용자는 정보가 어디에서 왔는지 확인할 수 있습니다." },
      { heading: "어떻게 확인하나", body: "공식 문서의 검증 경로를 순서대로 확인합니다." },
    ],
    keyTakeaways: ["출처 연결", "검증 경로", "공식 문서 확인"],
    channelCopy: {
      telegram: "데이터 출처와 검증 경로를 정리했습니다.",
      x: "데이터 출처와 검증 경로를 확인하세요.",
    },
  });
  assert.equal(report.status, "pass");
  assert.equal(report.score, 100);
});

test("requires Squid article visual copy to fit the sparse inline geometry", () => {
  const article = {
    clientId: "squid" as const,
    contentKind: "article" as const,
    sourceText: "Squid 공식 문서는 지원 네트워크와 확인 경로를 설명합니다.",
    title: "Squid 지원 경로 확인하기",
    lead: "공식 자료에서 확인할 흐름을 짧게 정리합니다.",
    sections: [
      { heading: "무엇이 달라졌나", body: "공식 지원 범위가 업데이트됐습니다." },
      { heading: "어떻게 작동하나", body: "Squid의 공식 경로를 사용합니다." },
      { heading: "무엇을 확인하나", body: "지원 상태를 먼저 확인합니다." },
    ],
    keyTakeaways: ["지원 범위", "공식 경로", "상태 확인"],
    channelCopy: { telegram: "Squid 지원 경로", x: "Squid 지원 경로" },
  };
  const safeVisual = {
    eyebrow: "CANTON × SQUID",
    headline: "Canton으로 가는 길",
    caption: "지원 흐름을 한국 이용자 관점에서 짚습니다.",
    points: ["지원 생태계에 포함됩니다.", "공식 경로를 확인하세요."],
  };
  const safe = evaluateBrandQuality({
    ...article,
    visuals: [safeVisual, { ...safeVisual, headline: "공식 경로 확인하기" }],
  });
  assert.equal(safe.checks.find((item) => item.id === "visual_density")?.status, "pass");

  const dense = evaluateBrandQuality({
    ...article,
    visuals: [
      { ...safeVisual, headline: "가".repeat(21) },
      { ...safeVisual, points: ["나".repeat(27), "공식 경로를 확인하세요."] },
    ],
  });
  assert.equal(dense.checks.find((item) => item.id === "visual_density")?.status, "review");
});

test("passes a three-to-five slide tutorial structure", () => {
  const report = evaluateBrandQuality({
    clientId: "yellow",
    contentKind: "tutorial",
    sourceText: "공식 문서는 거래 당사자가 메시지를 교환하고 정산 상태를 확인하는 절차를 설명합니다.",
    series: {
      series_title_en: "Trading Flow",
      series_subtitle_kr: "거래 흐름 한눈에 이해하기",
    },
    lessonCount: 4,
  });
  assert.equal(report.status, "pass");
  assert.equal(report.score, 100);
});

test("flags internal branding, wrong Squid naming, and hype language", () => {
  const report = evaluateBrandQuality({
    clientId: "squid",
    contentKind: "daily_news",
    sourceText: "Squid가 공식 업데이트를 공개했습니다.",
    headline: "CoinEasy가 전하는 Squid Router 게임체인저",
    bodyLines: ["역대급 업데이트를 확인하세요"],
    channelCopy: { telegram: "Squid 소식", x: "Squid 소식" },
    templateStyle: "classic",
  });
  const check = report.checks.find((item) => item.id === "brand_terms");
  assert.equal(check?.status, "review");
  assert.equal(check?.severity, "critical");
  assert.match(check?.detail || "", /CoinEasy/);
  assert.match(check?.detail || "", /Squid Router/);
});

test("flags generic publisher copy and over-dense Squid cards", () => {
  const report = evaluateBrandQuality({
    clientId: "squid",
    contentKind: "daily_news",
    sourceText: "Have you explored Canton yet? With Squid, it's easy.",
    headline: "Squid로 Canton Network를 간편하게 탐색할 수 있습니다",
    bodyLines: [
      "Squid가 지원하는 생태계에 Canton도 있습니다",
      "자세한 내용과 전체 맥락은 원문에서 확인해 주세요",
      "최신 소식을 한국 사용자 관점에서 소개합니다",
    ],
    channelCopy: { telegram: "Squid 소식", x: "Squid 소식" },
    templateStyle: "classic",
  });

  assert.equal(report.checks.find((item) => item.id === "text_density")?.status, "review");
  const terms = report.checks.find((item) => item.id === "brand_terms");
  assert.equal(terms?.status, "review");
  assert.match(terms?.detail || "", /간편하게 탐색할 수 있습니다/);
  assert.match(terms?.detail || "", /전체 맥락/);
});

test("aligns Squid QA density with the classic PNG and editable SVG geometry", () => {
  const base = {
    clientId: "squid" as const,
    contentKind: "daily_news" as const,
    sourceText: "Squid 공식 원문에 근거한 충분한 길이의 업데이트입니다.",
    channelCopy: { telegram: "Squid 업데이트", x: "Squid 업데이트" },
    templateStyle: "classic",
  };
  const safe = evaluateBrandQuality({
    ...base,
    headline: "가".repeat(24),
    bodyLines: ["나".repeat(21), "다".repeat(21)],
  });
  assert.equal(safe.checks.find((item) => item.id === "text_density")?.status, "pass");

  for (const input of [
    { headline: "가".repeat(25), bodyLines: ["짧은본문"] },
    { headline: "짧은제목", bodyLines: ["나".repeat(24)] },
  ]) {
    const report = evaluateBrandQuality({ ...base, ...input });
    assert.equal(report.checks.find((item) => item.id === "text_density")?.status, "review");
  }
});

test("does not treat official handles or source URLs as public brand prose", () => {
  const report = evaluateBrandQuality({
    clientId: "squid",
    contentKind: "daily_news",
    sourceText: "Squid가 공식 업데이트를 공개했습니다.",
    headline: "Squid 공식 업데이트",
    bodyLines: ["핵심 내용을 확인하세요"],
    channelCopy: {
      telegram: "Squid 소식\nhttps://example.com/CoinEasy/source",
      x: "Squid 소식 @SquidRouter",
    },
    templateStyle: "classic",
  });
  assert.equal(
    report.checks.find((item) => item.id === "brand_terms")?.status,
    "pass",
  );
});

test("flags claim-like metrics that are absent from the source", () => {
  const report = evaluateBrandQuality({
    clientId: "yellow",
    contentKind: "daily_news",
    sourceText: "공식 업데이트에서 정산 흐름 개선을 발표했습니다.",
    headline: "정산 시간이 25% 줄었습니다",
    bodyLines: ["공식 업데이트의 핵심 내용을 확인하세요"],
    channelCopy: { telegram: "정산 시간이 25% 줄었습니다.", x: "정산 시간이 25% 줄었습니다." },
    templateStyle: "classic",
  });
  assert.equal(
    report.checks.find((item) => item.id === "source_metrics")?.status,
    "review",
  );
  assert.ok(report.score < 100);
});

test("accepts claim-like metrics when the same metric exists in the source", () => {
  const report = evaluateBrandQuality({
    clientId: "babylon",
    contentKind: "daily_news",
    sourceText: "공식 자료는 참여자가 25% 증가했다고 설명합니다.",
    headline: "참여자가 25% 증가했습니다",
    bodyLines: ["공식 자료에 공개된 수치입니다"],
    channelCopy: { telegram: "참여자가 25% 증가했습니다.", x: "참여자가 25% 증가했습니다." },
    templateStyle: "classic",
  });
  assert.equal(
    report.checks.find((item) => item.id === "source_metrics")?.status,
    "pass",
  );
});

test("flags unsafe Squid subtitle placement and missing remix source imagery", () => {
  const report = evaluateBrandQuality({
    clientId: "squid",
    contentKind: "daily_news",
    sourceText: "Squid가 크로스체인 업데이트를 공개했습니다.",
    headline: "크로스체인 업데이트",
    bodyLines: ["공식 원문 기준 핵심 내용"],
    channelCopy: { telegram: "Squid 업데이트", x: "Squid 업데이트" },
    templateStyle: "remix",
    sourceImageUsed: false,
    sourceLogoVisible: true,
    visualLocalizationStatus: "unsafe_placement",
  });
  const check = report.checks.find((item) => item.id === "visual_integrity");
  assert.equal(check?.status, "review");
  assert.equal(check?.severity, "critical");
});

test("fails closed on every unsafe Squid copy-localization result", () => {
  for (const visualLocalizationStatus of ["unsafe_placement", "cleanup_failed"]) {
    const report = evaluateBrandQuality({
      clientId: "squid",
      contentKind: "daily_news",
      sourceText: "Squid가 공식 크로스체인 업데이트를 공개했습니다.",
      headline: "크로스체인 업데이트",
      bodyLines: ["공식 원문 기준 핵심 내용"],
      channelCopy: { telegram: "Squid 업데이트", x: "Squid 업데이트" },
      templateStyle: "remix",
      sourceImageUsed: true,
      sourceLogoVisible: true,
      visualLocalizationStatus,
    });
    const check = report.checks.find((item) => item.id === "visual_integrity");
    assert.equal(check?.status, "review", visualLocalizationStatus);
    assert.equal(check?.severity, "critical", visualLocalizationStatus);
    assert.equal(report.status, "review", visualLocalizationStatus);
    assert.equal(report.human_review_required, true, visualLocalizationStatus);
  }
});

test("flags channel copy that exceeds X weighted limits", () => {
  const report = evaluateBrandQuality({
    clientId: "origintrail",
    contentKind: "daily_news",
    sourceText: "공식 업데이트를 공개했습니다.",
    headline: "공식 업데이트",
    bodyLines: ["핵심 내용을 확인하세요"],
    channelCopy: { telegram: "공식 업데이트", x: "한".repeat(141) },
    templateStyle: "classic",
  });
  assert.equal(
    report.checks.find((item) => item.id === "channel_limits")?.status,
    "review",
  );
});

test("produces deterministic reports for idempotent catalog retries", () => {
  const input = {
    clientId: "yellow" as const,
    contentKind: "daily_news" as const,
    sourceText: "공식 업데이트를 공개했습니다.",
    headline: "공식 업데이트",
    bodyLines: ["핵심 내용을 확인하세요"],
    channelCopy: { telegram: "공식 업데이트", x: "공식 업데이트" },
    templateStyle: "classic",
  };
  assert.deepEqual(evaluateBrandQuality(input), evaluateBrandQuality(input));
});
