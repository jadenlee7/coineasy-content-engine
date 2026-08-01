export type BrandQaClient = "yellow" | "origintrail" | "squid" | "babylon";
export type BrandQaContentKind = "daily_news" | "article" | "tutorial";
export type BrandQaStatus = "pass" | "review";
export type BrandQaSeverity = "critical" | "warning" | "info";

export type BrandQaCheck = {
  id: string;
  status: BrandQaStatus;
  severity: BrandQaSeverity;
  label: string;
  detail: string;
};

export type BrandQaReport = {
  schema_version: "1.0";
  policy_version: "brand-qa@1";
  client_id: BrandQaClient;
  content_kind: BrandQaContentKind;
  status: BrandQaStatus;
  score: number;
  human_review_required: true;
  checks: BrandQaCheck[];
};

type ArticleSection = {
  heading?: unknown;
  body?: unknown;
};

type ArticleVisual = {
  eyebrow?: unknown;
  headline?: unknown;
  caption?: unknown;
  points?: unknown;
};

export type BrandQualityInput = {
  clientId: BrandQaClient;
  contentKind: BrandQaContentKind;
  sourceText?: unknown;
  headline?: unknown;
  bodyLines?: unknown;
  title?: unknown;
  lead?: unknown;
  sections?: unknown;
  keyTakeaways?: unknown;
  visuals?: unknown;
  series?: unknown;
  lessonCount?: unknown;
  channelCopy?: unknown;
  templateStyle?: unknown;
  sourceImageUsed?: unknown;
  sourceLogoVisible?: unknown;
  visualLocalizationStatus?: unknown;
};

const GENERIC_HYPE_TERMS = [
  "게임체인저",
  "대박",
  "무조건",
  "혁신적인",
  "압도적인",
  "역대급",
];

const FINANCIAL_PROMISE_TERMS = [
  "고수익",
  "무위험",
  "수익 보장",
  "원금 보장",
  "확정 수익",
];

const SQUID_GENERIC_EDITORIAL_TERMS = [
  "간편하게 탐색할 수 있습니다",
  "소식을 전합니다",
  "소개합니다",
  "핵심 변화",
  "최신 소식",
  "전체 맥락",
];

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(text).filter(Boolean) : [];
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function pass(
  id: string,
  label: string,
  detail: string,
  severity: BrandQaSeverity = "info",
): BrandQaCheck {
  return { id, status: "pass", severity, label, detail };
}

function review(
  id: string,
  label: string,
  detail: string,
  severity: BrandQaSeverity = "warning",
): BrandQaCheck {
  return { id, status: "review", severity, label, detail };
}

function generatedText(input: BrandQualityInput): string {
  const sections = Array.isArray(input.sections)
    ? input.sections.flatMap((value) => {
      const section = objectValue(value) as ArticleSection;
      return [text(section.heading), text(section.body)];
    })
    : [];
  const visuals = Array.isArray(input.visuals)
    ? input.visuals.flatMap((value) => {
      const visual = objectValue(value) as ArticleVisual;
      return [
        text(visual.eyebrow),
        text(visual.headline),
        text(visual.caption),
        ...stringArray(visual.points),
      ];
    })
    : [];
  const series = objectValue(input.series);
  const channelCopy = objectValue(input.channelCopy);
  return [
    text(input.headline),
    ...stringArray(input.bodyLines),
    text(input.title),
    text(input.lead),
    ...sections,
    ...stringArray(input.keyTakeaways),
    ...visuals,
    text(series.series_title_en),
    text(series.series_subtitle_kr),
    text(channelCopy.telegram),
    text(channelCopy.x),
  ].filter(Boolean).join("\n");
}

export function xWeightedLength(value: unknown): number {
  const singleWeightRanges = [
    [0x0000, 0x10ff],
    [0x2000, 0x200d],
    [0x2010, 0x201f],
    [0x2032, 0x2037],
  ];
  return Array.from(text(value).normalize("NFC")).reduce((total, character) => {
    const codepoint = character.codePointAt(0) || 0;
    const singleWeight = singleWeightRanges.some(
      ([start, end]) => start <= codepoint && codepoint <= end,
    );
    return total + (singleWeight ? 1 : 2);
  }, 0);
}

function logoChecks(input: BrandQualityInput): BrandQaCheck[] {
  const remix = input.templateStyle === "remix";
  const sourceLogoVisible = input.sourceLogoVisible === true;
  const officialLogoDetail = input.clientId === "squid" && remix
    ? "Squid 원본 크리에이티브는 합성 로고 없이 보존하는 렌더 정책을 사용합니다."
    : "서버 렌더러가 저장소의 클라이언트 공식 로고 자산만 사용하도록 제한되어 있습니다.";
  const duplicateDetail = remix && sourceLogoVisible
    ? "원본 공식 로고가 감지되어 서버 렌더러의 추가 로고 배치가 억제됩니다."
    : input.clientId === "squid" && remix
      ? "Squid 리믹스는 원본 구도를 보존하며 별도 로고를 합성하지 않습니다."
      : "렌더 정책상 공식 로고는 지정 안전영역에 한 번만 배치됩니다.";
  return [
    pass("official_logo", "공식 로고 정책", officialLogoDetail),
    pass("duplicate_logo", "중복 로고 방지", duplicateDetail),
  ];
}

function visualIntegrityCheck(input: BrandQualityInput): BrandQaCheck {
  if (input.templateStyle !== "remix") {
    return pass(
      "visual_integrity",
      "원본 비주얼 무결성",
      "리믹스가 아닌 브랜드 템플릿 생성으로 원본 이미지 의존성이 없습니다.",
    );
  }
  if (input.sourceImageUsed !== true) {
    return review(
      "visual_integrity",
      "원본 비주얼 무결성",
      "리믹스 결과에 원본 이미지 사용 확인값이 없습니다. 게시 전 이미지 출처와 합성 결과를 확인해야 합니다.",
      "critical",
    );
  }
  if (input.clientId !== "squid") {
    return pass(
      "visual_integrity",
      "원본 비주얼 무결성",
      "원본 이미지가 리믹스 결과에 반영되었습니다.",
    );
  }
  const localizationStatus = text(input.visualLocalizationStatus);
  if (["unsafe_placement", "cleanup_failed"].includes(localizationStatus)) {
    return review(
      "visual_integrity",
      "Squid 원본 구도·자막",
      localizationStatus === "unsafe_placement"
        ? "한국어 자막을 원본 위치에 안전하게 배치하지 못했습니다. 원문 자막과 구도를 직접 확인해야 합니다."
        : "원문 자막 픽셀을 깔끔하게 제거하지 못했습니다. 게시 전 원본 잔상을 직접 확인해야 합니다.",
      "critical",
    );
  }
  if (!["translated", "no_text"].includes(localizationStatus)) {
    return review(
      "visual_integrity",
      "Squid 원본 구도·자막",
      "Squid 리믹스의 자막 현지화 상태가 확인되지 않았습니다.",
      "critical",
    );
  }
  return pass(
    "visual_integrity",
    "Squid 원본 구도·자막",
    localizationStatus === "translated"
      ? "원본 구도를 유지하고 확인된 영역의 문구만 한국어로 교체했습니다."
      : "번역 대상 문구가 없어 원본 구도를 그대로 유지했습니다.",
  );
}

function densityCheck(input: BrandQualityInput): BrandQaCheck {
  if (input.contentKind === "daily_news") {
    const headline = text(input.headline);
    const lines = stringArray(input.bodyLines);
    const totalLength = headline.length + lines.reduce((total, line) => total + line.length, 0);
    const squid = input.clientId === "squid";
    const headlineLimit = squid ? 28 : 72;
    const totalLimit = squid ? 70 : 240;
    const valid = Boolean(headline)
      && headline.length <= headlineLimit
      && lines.length >= 1
      && lines.length <= (squid ? 2 : 3)
      && lines.every((line) => line.length <= (squid ? 23 : 70))
      && totalLength <= totalLimit;
    return valid
      ? pass(
        "text_density",
        "카드 문구 밀도",
        `헤드라인 ${headline.length}자, 본문 ${lines.length}줄로 브랜드 카드 가독성 범위 안입니다.`,
      )
      : review(
        "text_density",
        "카드 문구 밀도",
        `헤드라인 ${headline.length}자, 본문 ${lines.length}줄, 전체 ${totalLength}자입니다. 카드의 줄바꿈과 안전영역을 직접 확인해야 합니다.`,
      );
  }

  if (input.contentKind === "article") {
    const title = text(input.title);
    const lead = text(input.lead);
    const sections = Array.isArray(input.sections) ? input.sections.map(objectValue) : [];
    const takeaways = stringArray(input.keyTakeaways);
    const valid = Boolean(title)
      && title.length <= 110
      && Boolean(lead)
      && lead.length <= 400
      && sections.length >= 3
      && sections.length <= 5
      && sections.every((section) => (
        text(section.heading).length >= 1
        && text(section.heading).length <= 100
        && text(section.body).length >= 1
        && text(section.body).length <= 3_000
      ))
      && takeaways.length >= 3
      && takeaways.length <= 5;
    return valid
      ? pass(
        "text_density",
        "아티클 구성 밀도",
        `제목 ${title.length}자, 리드 ${lead.length}자, 본문 ${sections.length}개 섹션으로 편집 범위 안입니다.`,
      )
      : review(
        "text_density",
        "아티클 구성 밀도",
        `제목 ${title.length}자, 리드 ${lead.length}자, 본문 ${sections.length}개 섹션입니다. 제목·리드·3~5개 섹션 구조를 확인해야 합니다.`,
      );
  }

  const series = objectValue(input.series);
  const title = text(series.series_title_en);
  const subtitle = text(series.series_subtitle_kr);
  const lessonCount = Number(input.lessonCount);
  const valid = title.length <= 80
    && subtitle.length <= 60
    && Number.isSafeInteger(lessonCount)
    && lessonCount >= 3
    && lessonCount <= 5;
  return valid
    ? pass(
      "text_density",
      "튜토리얼 학습 밀도",
      `${lessonCount}장 구성과 시리즈 제목 길이가 학습용 캐러셀 범위 안입니다.`,
    )
    : review(
      "text_density",
      "튜토리얼 학습 밀도",
      `${Number.isFinite(lessonCount) ? lessonCount : 0}장 구성입니다. 3~5장 학습 흐름과 각 장의 줄바꿈을 확인해야 합니다.`,
    );
}

function squidArticleVisualDensityCheck(input: BrandQualityInput): BrandQaCheck {
  const visuals = Array.isArray(input.visuals)
    ? input.visuals.map(objectValue)
    : [];
  const valid = visuals.length === 2 && visuals.every((visual) => {
    const points = stringArray(visual.points);
    return text(visual.headline).length >= 1
      && text(visual.headline).length <= 20
      && text(visual.caption).length >= 1
      && text(visual.caption).length <= 50
      && points.length === 2
      && points.every((point) => point.length <= 26);
  });
  return valid
    ? pass(
      "visual_density",
      "Squid 본문 비주얼 밀도",
      "본문 비주얼 2개의 제목·설명·근거 문구가 Squid 안전영역 안입니다.",
    )
    : review(
      "visual_density",
      "Squid 본문 비주얼 밀도",
      "각 비주얼은 제목 20자, 설명 50자, 26자 이하 포인트 2개로 줄여야 잘림 없이 표시됩니다.",
    );
}

function channelLimitCheck(input: BrandQualityInput): BrandQaCheck {
  const channelCopy = objectValue(input.channelCopy);
  const telegram = text(channelCopy.telegram);
  const x = text(channelCopy.x);
  if (!telegram && !x) {
    return input.contentKind === "tutorial"
      ? pass(
        "channel_limits",
        "채널 길이",
        "튜토리얼 생성은 별도 채널 게시문을 만들지 않습니다.",
      )
      : review(
        "channel_limits",
        "채널 길이",
        "텔레그램·X 게시문이 비어 있습니다.",
      );
  }
  const xLength = xWeightedLength(x);
  const valid = telegram.length <= 4_000 && xLength <= 280;
  return valid
    ? pass(
      "channel_limits",
      "채널 길이",
      `텔레그램 ${telegram.length}자, X 가중 ${xLength}/280자로 게시 제한 안입니다.`,
    )
    : review(
      "channel_limits",
      "채널 길이",
      `텔레그램 ${telegram.length}/4000자, X 가중 ${xLength}/280자입니다. 초과 문구를 줄여야 합니다.`,
      "critical",
    );
}

function brandTermsCheck(input: BrandQualityInput, output: string): BrandQaCheck {
  // Handles and source URLs are identifiers, not public-facing brand prose.
  const proseOutput = output
    .replace(/\bhttps?:\/\/[^\s]+/giu, "")
    .replace(/@[a-z0-9_]+/giu, "");
  const findings: string[] = [];
  if (/\bCoinEasy\b/i.test(proseOutput)) findings.push("공개 문구에 CoinEasy 내부 브랜드명 포함");
  if (input.clientId === "squid" && /\bSquid\s+Router\b/i.test(proseOutput)) {
    findings.push("Squid 공식 표기 대신 Squid Router 사용");
  }
  if (input.clientId === "squid") {
    for (const term of SQUID_GENERIC_EDITORIAL_TERMS) {
      if (proseOutput.includes(term)) findings.push(`Squid 비권장 표현 '${term}'`);
    }
  }
  for (const term of GENERIC_HYPE_TERMS) {
    if (proseOutput.includes(term)) findings.push(`과장 표현 '${term}'`);
  }
  if (input.clientId === "yellow" || input.clientId === "babylon") {
    for (const term of FINANCIAL_PROMISE_TERMS) {
      if (proseOutput.includes(term)) findings.push(`금융 오인 가능 표현 '${term}'`);
    }
  }
  return findings.length
    ? review(
      "brand_terms",
      "브랜드명·톤",
      `${findings.slice(0, 4).join(" · ")}. 공식 한국어 팀 관점으로 수정해야 합니다.`,
      "critical",
    )
    : pass(
      "brand_terms",
      "브랜드명·톤",
      "내부 브랜드 노출, 잘못된 공식 표기, 과장·수익 보장 표현이 감지되지 않았습니다.",
    );
}

function metricTokens(value: string): string[] {
  return value.match(
    /\d+(?:[.,]\d+)?\s*(?:%|배|억|만|달러|USD|BTC|ETH|명|개\s*(?:체인|네트워크|프로젝트)|시간|일)/giu,
  ) || [];
}

function normalizedMetric(value: string): string {
  return value.replace(/[\s,]/g, "").toLocaleLowerCase("en-US");
}

function sourceMetricsCheck(sourceText: string, output: string): BrandQaCheck {
  const sourceMetrics = new Set(metricTokens(sourceText).map(normalizedMetric));
  const unsupported = [...new Set(
    metricTokens(output)
      .map(normalizedMetric)
      .filter((metric) => !sourceMetrics.has(metric)),
  )];
  return unsupported.length
    ? review(
      "source_metrics",
      "수치 근거",
      `원문에서 같은 표기를 찾지 못한 수치가 있습니다: ${unsupported.slice(0, 5).join(", ")}.`,
      "critical",
    )
    : pass(
      "source_metrics",
      "수치 근거",
      "생성 문구의 주장형 수치가 원문 수치 범위를 벗어나지 않았습니다.",
    );
}

function sourcePresenceCheck(sourceText: string): BrandQaCheck {
  return sourceText.length >= 10
    ? pass(
      "source_present",
      "원문 근거",
      `검사에 사용할 원문 ${sourceText.length}자가 연결되어 있습니다.`,
    )
    : review(
      "source_present",
      "원문 근거",
      "검사에 사용할 원문이 너무 짧습니다. 게시 전 공식 원문을 다시 확인해야 합니다.",
      "critical",
    );
}

export function evaluateBrandQuality(input: BrandQualityInput): BrandQaReport {
  const sourceText = text(input.sourceText);
  const output = generatedText(input);
  const checks = [
    ...logoChecks(input),
    visualIntegrityCheck(input),
    densityCheck(input),
    ...(input.clientId === "squid" && input.contentKind === "article"
      ? [squidArticleVisualDensityCheck(input)]
      : []),
    channelLimitCheck(input),
    brandTermsCheck(input, output),
    sourceMetricsCheck(sourceText, output),
    sourcePresenceCheck(sourceText),
  ];
  const deductions: Record<BrandQaSeverity, number> = {
    critical: 25,
    warning: 10,
    info: 5,
  };
  const score = Math.max(
    0,
    100 - checks
      .filter((check) => check.status === "review")
      .reduce((total, check) => total + deductions[check.severity], 0),
  );
  return {
    schema_version: "1.0",
    policy_version: "brand-qa@1",
    client_id: input.clientId,
    content_kind: input.contentKind,
    status: checks.some((check) => check.status === "review") ? "review" : "pass",
    score,
    human_review_required: true,
    checks,
  };
}
