import assert from "node:assert/strict";
import test from "node:test";
import {
  evaluateBrandQuality,
  type BrandQaClient,
} from "../netlify/functions/_shared/brand-quality.mts";

const clients: BrandQaClient[] = ["yellow", "origintrail", "squid", "babylon"];

test("passes source-grounded daily news for all four client brands", () => {
  for (const clientId of clients) {
    const report = evaluateBrandQuality({
      clientId,
      contentKind: "daily_news",
      sourceText: "공식 업데이트에서 새로운 검증 흐름과 사용자 안내를 공개했습니다.",
      headline: "공식 업데이트의 핵심 변화",
      bodyLines: ["검증 흐름을 더 명확하게 정리", "사용자가 확인할 기준을 안내"],
      channelCopy: {
        telegram: "공식 업데이트의 핵심 변화를 정리했습니다.",
        x: "공식 업데이트의 핵심 변화를 확인하세요.",
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
