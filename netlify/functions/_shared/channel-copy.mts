type NewsCardSpec = {
  label?: unknown;
  headline?: unknown;
  body_lines?: unknown;
};

type ChannelCopy = {
  telegram: string;
  x: string;
};

const CLIENT_COPY = {
  yellow: {
    name: "Yellow",
    hashtags: ["#Yellow", "#YellowNetwork", "#YellowKorea", "#Web3"],
    xHashtags: ["#Yellow", "#YellowKorea"],
  },
  origintrail: {
    name: "OriginTrail",
    hashtags: ["#OriginTrail", "#TRAC", "#DKG", "#Web3"],
    xHashtags: ["#OriginTrail", "#DKG"],
  },
  squid: {
    name: "Squid",
    hashtags: ["#SquidRouter", "#CrossChain", "#SquidKorea", "#Web3"],
    xHashtags: ["#SquidRouter", "#SquidKorea"],
  },
  babylon: {
    name: "Babylon",
    hashtags: ["#Babylon", "#BitcoinStaking", "#BTC", "#BabylonKorea"],
    xHashtags: ["#Babylon", "#BabylonKorea"],
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
  headline: string,
  bodyLines: string[],
  sourceUrl: string,
  hashtags: readonly string[],
): string {
  const footer = [sourceUrl ? `🔗 원문 확인: ${sourceUrl}` : "", hashtags.join(" ")]
    .filter(Boolean)
    .join("\n\n");
  const available = Math.max(80, 280 - (footer ? footer.length + 2 : 0));
  const core = [`📌 ${truncateText(headline, Math.min(118, available - 3))}`];
  for (const line of bodyLines.slice(0, 2)) {
    const item = `• ${truncateText(line, 72)}`;
    if ([...core, item].join("\n\n").length <= available) core.push(item);
  }
  const coreText = truncateText(core.join("\n\n"), available);
  return footer ? `${coreText}\n\n${footer}` : coreText;
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
    x: buildXPost(headline, bodyLines, sourceUrl, client.xHashtags),
  };
}
