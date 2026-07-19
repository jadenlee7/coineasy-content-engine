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
  },
  origintrail: {
    name: "OriginTrail",
    hashtags: ["#OriginTrail", "#TRAC", "#DKG", "#Web3"],
  },
  squid: {
    name: "Squid",
    hashtags: ["#SquidRouter", "#CrossChain", "#SquidKorea", "#Web3"],
  },
  babylon: {
    name: "Babylon",
    hashtags: ["#Babylon", "#BitcoinStaking", "#BTC", "#BabylonKorea"],
  },
} as const;

function cleanText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
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
    x: sourceContent.trim(),
  };
}
