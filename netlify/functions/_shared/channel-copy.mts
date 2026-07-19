type NewsCardSpec = {
  label?: unknown;
  headline?: unknown;
  body_lines?: unknown;
};

type ChannelCopy = {
  telegram: string;
  x: string;
};

type XStyle = "analytical" | "causal" | "minimal" | "guide";

const CLIENT_COPY = {
  yellow: {
    name: "Yellow",
    hashtags: ["#Yellow", "#YellowNetwork", "#YellowKorea", "#Web3"],
    xStyle: "analytical" as XStyle,
  },
  origintrail: {
    name: "OriginTrail",
    hashtags: ["#OriginTrail", "#TRAC", "#DKG", "#Web3"],
    xStyle: "causal" as XStyle,
  },
  squid: {
    name: "Squid",
    hashtags: ["#SquidRouter", "#CrossChain", "#SquidKorea", "#Web3"],
    xStyle: "minimal" as XStyle,
  },
  babylon: {
    name: "Babylon",
    hashtags: ["#Babylon", "#BitcoinStaking", "#BTC", "#BabylonKorea"],
    xStyle: "guide" as XStyle,
  },
} as const;

function cleanText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function truncateText(value: string, maxLength: number): string {
  if (value.length <= maxLength) return value;
  return `${value.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`;
}

function buildXPost(
  style: XStyle,
  headline: string,
  bodyLines: string[],
  sourceContent: string,
): string {
  const sourceTokens = Array.from(
    new Set(cleanText(sourceContent).match(/(?:^|\s)([@#$][A-Za-z0-9_]+)/g)?.map((token) => token.trim()) || []),
  );
  const sourceIsShort = cleanText(sourceContent).length <= 120;
  const bodyLimit = style === "minimal" && sourceIsShort ? 1 : 2;
  const core = [truncateText(headline, 118)];

  for (const line of bodyLines.slice(0, bodyLimit)) {
    const prefix = style === "causal" ? "→ " : style === "guide" ? "• " : "";
    const item = `${prefix}${truncateText(line, style === "minimal" ? 80 : 88)}`;
    if ([...core, item].join("\n\n").length <= 250) core.push(item);
  }

  let result = truncateText(core.join("\n\n"), 280);
  const missingTokens = sourceTokens.filter((token) => !result.includes(token));
  if (missingTokens.length) {
    const tokenLine = missingTokens.join(" ");
    if (`${result}\n\n${tokenLine}`.length <= 280) result = `${result}\n\n${tokenLine}`;
  }
  return result;
}

export function buildChannelCopy(
  clientId: keyof typeof CLIENT_COPY,
  spec: NewsCardSpec,
  sourceContent: string,
  sourceUrl: string,
): ChannelCopy {
  const client = CLIENT_COPY[clientId];
  const label = cleanText(spec.label) || "업데이트";
  const headline = cleanText(spec.headline) || `${client.name} 최신 업데이트`;
  const bodyLines = Array.isArray(spec.body_lines)
    ? spec.body_lines.map(cleanText).filter(Boolean).slice(0, 3)
    : [];

  const telegramSections = [
    `📢 ${client.name} Korea | ${label}`,
    headline,
  ];
  if (bodyLines.length) {
    telegramSections.push(bodyLines.map((line) => `▪️ ${line}`).join("\n"));
  }
  telegramSections.push("👉 자세한 내용과 전체 맥락은 원문에서 확인해 주세요.");
  if (sourceUrl) {
    telegramSections.push(`🔗 원문: ${sourceUrl}`);
  }
  telegramSections.push(client.hashtags.join(" "));

  return {
    telegram: telegramSections.join("\n\n"),
    x: buildXPost(client.xStyle, headline, bodyLines, sourceContent),
  };
}
