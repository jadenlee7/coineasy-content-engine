import {
  escapeXml,
  wrapSvgText,
  type EditableClientId,
} from "./editable-svg.mts";

export type ArticleBannerInput = {
  title: string;
  lead: string;
  sourceUrl?: string;
  date?: string;
};

type ArticleBannerBrand = {
  name: string;
  primary: string;
  accent: string;
  background: string;
  displayFont: string;
  bodyFont: string;
  logoIsSymbol?: boolean;
};

const ARTICLE_BANNER_BRANDS: Record<EditableClientId, ArticleBannerBrand> = {
  yellow: {
    name: "Yellow Network",
    primary: "#FDDA16",
    accent: "#FFF06A",
    background: "#17181B",
    displayFont: "Pretendard",
    bodyFont: "Pretendard",
  },
  origintrail: {
    name: "OriginTrail Korea",
    primary: "#8E73FF",
    accent: "#6344DF",
    background: "#0C2246",
    displayFont: "Gmarket Sans",
    bodyFont: "Pretendard",
  },
  squid: {
    name: "Squid",
    primary: "#E6FA36",
    accent: "#BC8EE4",
    background: "#1A0E2E",
    displayFont: "Bagoss Condensed",
    bodyFont: "Pretendard",
  },
  babylon: {
    name: "Babylon Korea",
    primary: "#E57A45",
    accent: "#F7931A",
    background: "#123F51",
    displayFont: "Inter",
    bodyFont: "Pretendard",
    logoIsSymbol: true,
  },
};

function cleanText(value: string | undefined, maxLength: number): string {
  return typeof value === "string"
    ? value.replace(/\s+/g, " ").trim().slice(0, maxLength)
    : "";
}

function sourceLabel(value: string | undefined): string {
  const source = cleanText(value, 2_048);
  if (!source) return "OFFICIAL SOURCE";
  try {
    const url = new URL(source);
    return url.hostname.replace(/^www\./, "").toUpperCase();
  } catch {
    return "OFFICIAL SOURCE";
  }
}

function textLayers(
  id: string,
  lines: string[],
  x: number,
  y: number,
  lineHeight: number,
  attributes: string,
): string {
  return lines.map((line, index) => (
    `<text id="${id}-Line-${index + 1}" x="${x}" y="${y + index * lineHeight}" ${attributes}>${escapeXml(line)}</text>`
  )).join("\n");
}

export function buildArticleBannerSvg(
  clientId: EditableClientId,
  input: ArticleBannerInput,
  logoDataUrl?: string,
): string {
  const brand = ARTICLE_BANNER_BRANDS[clientId];
  const title = cleanText(input.title, 200) || "새로운 소식을 전합니다";
  const lead = cleanText(input.lead, 800);
  const date = cleanText(input.date, 40);
  const titleLines = wrapSvgText(title, 25, 3);
  const leadLines = wrapSvgText(lead, 68, 2);
  const titleFontSize = titleLines.length === 3 ? 58 : 64;
  const titleLineHeight = titleLines.length === 3 ? 70 : 76;
  const leadY = Math.max(458, 222 + titleLines.length * titleLineHeight + 34);
  const leadMarkup = leadLines.length
    ? textLayers(
      "Article-Lead",
      leadLines,
      78,
      leadY,
      31,
      `fill="#FFFFFF" fill-opacity="0.72" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="22" font-weight="500"`,
    )
    : "";
  const logo = logoDataUrl
    ? brand.logoIsSymbol
      ? `<image id="Brand-Logo" x="78" y="55" width="62" height="62" href="${escapeXml(logoDataUrl)}" preserveAspectRatio="xMidYMid meet"/>
    <text id="Brand-Name" x="158" y="95" fill="#FFFFFF" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="26" font-weight="800">${escapeXml(brand.name)}</text>`
      : `<image id="Brand-Logo" x="78" y="55" width="190" height="62" href="${escapeXml(logoDataUrl)}" preserveAspectRatio="xMinYMid meet"/>`
    : `<text id="Brand-Logo-Fallback" x="78" y="96" fill="#FFFFFF" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="27" font-weight="800">${escapeXml(brand.name)}</text>`;

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" fill="none" role="img" aria-label="${escapeXml(title)}">
  <defs>
    <linearGradient id="Background-Glow" x1="1200" y1="0" x2="615" y2="630" gradientUnits="userSpaceOnUse">
      <stop stop-color="${brand.accent}" stop-opacity="0.28"/>
      <stop offset="0.56" stop-color="${brand.primary}" stop-opacity="0.08"/>
      <stop offset="1" stop-color="${brand.background}" stop-opacity="0"/>
    </linearGradient>
    <pattern id="Editorial-Grid" width="44" height="44" patternUnits="userSpaceOnUse">
      <path d="M44 0H0V44" stroke="#FFFFFF" stroke-opacity="0.045"/>
    </pattern>
    <clipPath id="Canvas-Clip"><rect width="1200" height="630" rx="0"/></clipPath>
  </defs>
  <g id="Canvas" clip-path="url(#Canvas-Clip)">
    <rect id="Background" width="1200" height="630" fill="${brand.background}"/>
    <rect id="Grid" width="1200" height="630" fill="url(#Editorial-Grid)"/>
    <rect id="Glow" width="1200" height="630" fill="url(#Background-Glow)"/>
    <circle id="Accent-Orbit-Outer" cx="1105" cy="112" r="224" stroke="${brand.primary}" stroke-opacity="0.16" stroke-width="2"/>
    <circle id="Accent-Orbit-Inner" cx="1105" cy="112" r="142" fill="${brand.primary}" fill-opacity="0.08" stroke="${brand.accent}" stroke-opacity="0.34" stroke-width="2"/>
    <path id="Accent-Rail" d="M0 0H18V630H0Z" fill="${brand.primary}"/>
    <path id="Corner-Marker" d="M1094 630H1200V524L1094 630Z" fill="${brand.primary}" fill-opacity="0.92"/>
  </g>
  <g id="Header">
    ${logo}
    <g id="Article-Label">
      <rect x="935" y="58" width="185" height="48" rx="24" fill="${brand.primary}"/>
      <text x="1027.5" y="89" text-anchor="middle" fill="#101114" font-family="Inter, Pretendard, sans-serif" font-size="17" font-weight="800" letter-spacing="2.2">ARTICLE</text>
    </g>
    <line x1="78" y1="142" x2="1122" y2="142" stroke="#FFFFFF" stroke-opacity="0.18"/>
  </g>
  <g id="Article-Copy">
    ${textLayers(
      "Article-Title",
      titleLines,
      78,
      222,
      titleLineHeight,
      `fill="#FFFFFF" font-family="${escapeXml(brand.displayFont)}, ${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="${titleFontSize}" font-weight="800" letter-spacing="-1.8"`,
    )}
    ${leadMarkup}
  </g>
  <g id="Footer">
    <line x1="78" y1="548" x2="1122" y2="548" stroke="#FFFFFF" stroke-opacity="0.18"/>
    <circle cx="87" cy="584" r="8" fill="${brand.primary}"/>
    <text x="108" y="590" fill="#FFFFFF" fill-opacity="0.64" font-family="Inter, Pretendard, sans-serif" font-size="17" font-weight="650" letter-spacing="0.6">${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1078" y="590" text-anchor="end" fill="#FFFFFF" fill-opacity="0.64" font-family="Inter, Pretendard, sans-serif" font-size="17" font-weight="650">${escapeXml(date)}</text>
  </g>
</svg>`;
}
