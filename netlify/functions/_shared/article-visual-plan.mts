export const ARTICLE_VISUAL_MOTIFS = [
  "network",
  "layers",
  "flow",
  "signal",
  "event",
  "asset",
] as const;

export type ArticleVisualMotif = typeof ARTICLE_VISUAL_MOTIFS[number];
export type ArticleVisualRole = "overview" | "explainer";

export type ArticleVisualBrief = {
  id: "visual-1" | "visual-2";
  after_section_id: string;
  role: ArticleVisualRole;
  motif: ArticleVisualMotif;
  eyebrow: string;
  headline: string;
  caption: string;
  points: string[];
};

export type ArticleVisualSection = {
  id: string;
  heading: string;
  body: string;
};

const MOTIF_SET = new Set<string>(ARTICLE_VISUAL_MOTIFS);

function cleanText(value: unknown, maximum: number): string {
  return typeof value === "string"
    ? value.replace(/\s+/g, " ").trim().slice(0, maximum)
    : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function inferArticleVisualMotif(value: string): ArticleVisualMotif {
  const normalized = value.toLowerCase();
  const groups: Array<[ArticleVisualMotif, string[]]> = [
    ["event", ["demo", "event", "award", "launch", "행사", "데모", "상금", "일정", "출시"]],
    ["network", ["graph", "agent", "network", "node", "지식", "그래프", "네트워크", "에이전트"]],
    ["asset", ["bitcoin", "btc", "collateral", "staking", "담보", "비트코인", "스테이킹", "자산"]],
    ["flow", ["cross-chain", "routing", "bridge", "payment", "라우팅", "크로스체인", "브리지", "결제"]],
    ["layers", ["layer", "stack", "memory", "계층", "스택", "메모리", "구조"]],
    ["signal", ["signal", "data", "update", "성과", "데이터", "업데이트", "지표"]],
  ];
  return groups.find(([, keywords]) => keywords.some(keyword => normalized.includes(keyword)))?.[0]
    || "signal";
}

function fallbackVisuals(
  title: string,
  sections: ArticleVisualSection[],
  takeaways: string[],
): ArticleVisualBrief[] {
  const targetIndexes = [0, Math.min(2, sections.length - 1)];
  const labels = ["핵심 맥락", "작동 구조"];
  const roles: ArticleVisualRole[] = ["overview", "explainer"];
  return targetIndexes.map((sectionIndex, index) => {
    const section = sections[sectionIndex];
    const pointStart = index === 0 ? 0 : Math.max(0, takeaways.length - 3);
    const selectedPoints = takeaways.slice(pointStart, pointStart + 3);
    const points = selectedPoints.length >= 2
      ? selectedPoints
      : takeaways.slice(0, 3);
    const safePoints = points.length >= 2
      ? points.map(point => cleanText(point, 100))
      : [
        cleanText(section.body, 100) || cleanText(section.heading, 100),
        cleanText(title, 100),
      ];
    return {
      id: `visual-${index + 1}` as ArticleVisualBrief["id"],
      after_section_id: section.id,
      role: roles[index],
      motif: inferArticleVisualMotif(
        `${title} ${section.heading} ${safePoints.join(" ")}`,
      ),
      eyebrow: labels[index],
      headline: cleanText(section.heading, 70) || cleanText(title, 70),
      caption: safePoints[0],
      points: safePoints.slice(0, 3),
    };
  });
}

export function deriveArticleVisuals(input: {
  title: unknown;
  sections: unknown;
  key_takeaways: unknown;
  visuals?: unknown;
}): ArticleVisualBrief[] {
  const title = cleanText(input.title, 200);
  const sections = (Array.isArray(input.sections) ? input.sections : [])
    .map((section, index): ArticleVisualSection | null => {
      if (!isRecord(section)) return null;
      const heading = cleanText(section.heading, 80);
      const body = cleanText(section.body, 1_500);
      if (!heading || !body) return null;
      return {
        id: cleanText(section.id, 80) || `section-${index + 1}`,
        heading,
        body,
      };
    })
    .filter((section): section is ArticleVisualSection => section !== null);
  const takeaways = (Array.isArray(input.key_takeaways) ? input.key_takeaways : [])
    .map(item => cleanText(item, 180))
    .filter(Boolean)
    .slice(0, 5);
  if (!title || sections.length < 3 || takeaways.length < 2) return [];

  const fallback = fallbackVisuals(title, sections, takeaways);
  if (!Array.isArray(input.visuals) || input.visuals.length !== 2) return fallback;

  const normalized = input.visuals.map((raw, index): ArticleVisualBrief | null => {
    if (!isRecord(raw)) return null;
    const motif = cleanText(raw.motif, 20);
    const headline = cleanText(raw.headline, 70);
    const caption = cleanText(raw.caption, 200);
    const eyebrow = cleanText(raw.eyebrow, 32);
    const rawPoints = Array.isArray(raw.points) ? raw.points : [];
    const points = rawPoints.map(point => cleanText(point, 100)).filter(Boolean).slice(0, 4);
    if (!MOTIF_SET.has(motif) || !headline || !caption || !eyebrow || points.length < 2) {
      return null;
    }
    return {
      id: `visual-${index + 1}` as ArticleVisualBrief["id"],
      after_section_id: fallback[index].after_section_id,
      role: index === 0 ? "overview" : "explainer",
      motif: motif as ArticleVisualMotif,
      eyebrow,
      headline,
      caption,
      points,
    };
  });
  return normalized.every((visual): visual is ArticleVisualBrief => visual !== null)
    ? normalized
    : fallback;
}

export function articleVisualFilename(
  clientId: string,
  visualId: "hero" | ArticleVisualBrief["id"],
  extension: "svg" | "png",
): string {
  const dimensions = visualId === "hero" ? "1200x630" : "1200x675";
  return `${clientId}-article-${visualId}-${dimensions}.${extension}`;
}
