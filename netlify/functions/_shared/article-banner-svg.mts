import {
  escapeXml,
  wrapSvgText,
  type EditableClientId,
} from "./editable-svg.mts";
import type {
  ArticleVisualBrief,
  ArticleVisualMotif,
} from "./article-visual-plan.mts";

export type ArticleBannerInput = {
  title: string;
  lead: string;
  sourceUrl?: string;
  date?: string;
  motif?: ArticleVisualMotif;
};

export type ArticleInlineVisualInput = {
  visual: ArticleVisualBrief;
  sourceUrl?: string;
  date?: string;
};

type ArticleBannerBrand = {
  name: string;
  primary: string;
  accent: string;
  background: string;
  surface: string;
  displayFont: string;
  bodyFont: string;
  logoIsSymbol?: boolean;
};

const ARTICLE_BANNER_BRANDS: Record<EditableClientId, ArticleBannerBrand> = {
  yellow: {
    name: "Yellow Network",
    primary: "#FDDA16",
    accent: "#FFF6A3",
    background: "#101114",
    surface: "#202126",
    displayFont: "Pretendard",
    bodyFont: "Pretendard",
  },
  origintrail: {
    name: "OriginTrail Korea",
    primary: "#A993FF",
    accent: "#6D4AFF",
    background: "#071A39",
    surface: "#102957",
    displayFont: "Gmarket Sans",
    bodyFont: "Pretendard",
  },
  squid: {
    name: "Squid",
    primary: "#E6FA36",
    accent: "#C59AEA",
    background: "#160A27",
    surface: "#2A1744",
    displayFont: "Bagoss Condensed",
    bodyFont: "Pretendard",
  },
  babylon: {
    name: "Babylon Korea",
    primary: "#F28A52",
    accent: "#FFB375",
    background: "#082E3D",
    surface: "#124C60",
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

function logoMarkup(
  brand: ArticleBannerBrand,
  logoDataUrl: string | undefined,
  x = 72,
  y = 42,
): string {
  if (logoDataUrl) {
    return brand.logoIsSymbol
      ? `<image id="Brand-Logo" x="${x}" y="${y}" width="54" height="54" href="${escapeXml(logoDataUrl)}" preserveAspectRatio="xMidYMid meet"/>
  <text id="Brand-Name" x="${x + 70}" y="${y + 36}" fill="#FFFFFF" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="22" font-weight="800">${escapeXml(brand.name)}</text>`
      : `<image id="Brand-Logo" x="${x}" y="${y}" width="190" height="54" href="${escapeXml(logoDataUrl)}" preserveAspectRatio="xMinYMid meet"/>`;
  }
  return `<text id="Brand-Logo-Fallback" x="${x}" y="${y + 37}" fill="#FFFFFF" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="24" font-weight="800">${escapeXml(brand.name)}</text>`;
}

function motifMarkup(
  motif: ArticleVisualMotif,
  brand: ArticleBannerBrand,
  prefix: string,
  x: number,
  y: number,
): string {
  const primary = brand.primary;
  const accent = brand.accent;
  const commonStart = `<g id="${prefix}-Motif-${motif}" transform="translate(${x} ${y})">`;
  if (motif === "network") {
    return `${commonStart}
  <path d="M38 158L116 72L205 118L294 47L382 132M116 72L163 196L294 47M205 118L345 210M163 196L345 210" stroke="${primary}" stroke-opacity=".42" stroke-width="3"/>
  <circle cx="38" cy="158" r="17" fill="${brand.surface}" stroke="${accent}" stroke-width="4"/>
  <circle cx="116" cy="72" r="24" fill="${primary}"/>
  <circle cx="205" cy="118" r="19" fill="${brand.surface}" stroke="${primary}" stroke-width="4"/>
  <circle cx="294" cy="47" r="14" fill="${accent}"/>
  <circle cx="163" cy="196" r="13" fill="${accent}"/>
  <circle cx="345" cy="210" r="23" fill="${brand.surface}" stroke="${accent}" stroke-width="4"/>
  <circle cx="382" cy="132" r="10" fill="${primary}"/>
  <circle cx="116" cy="72" r="34" stroke="${primary}" stroke-opacity=".2" stroke-width="2"/>
</g>`;
  }
  if (motif === "layers") {
    return `${commonStart}
  <path d="M57 170L202 105L365 168L220 236Z" fill="${accent}" fill-opacity=".22" stroke="${accent}" stroke-width="3"/>
  <path d="M57 117L202 52L365 115L220 183Z" fill="${primary}" fill-opacity=".18" stroke="${primary}" stroke-width="3"/>
  <path d="M57 67L202 2L365 65L220 133Z" fill="${brand.surface}" stroke="#FFFFFF" stroke-opacity=".54" stroke-width="3"/>
  <path d="M202 2L202 52M57 67L57 117M365 65L365 115" stroke="#FFFFFF" stroke-opacity=".28" stroke-width="2" stroke-dasharray="6 8"/>
  <circle cx="203" cy="65" r="23" fill="${primary}"/>
  <path d="M192 65L200 73L216 55" stroke="${brand.background}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>
</g>`;
  }
  if (motif === "flow") {
    return `${commonStart}
  <path d="M24 176C86 176 91 54 156 54C221 54 216 195 284 195C344 195 344 91 400 91" stroke="${primary}" stroke-width="12" stroke-linecap="round"/>
  <path d="M24 107C82 107 102 218 166 218C228 218 240 27 306 27C356 27 370 57 400 57" stroke="${accent}" stroke-opacity=".72" stroke-width="5" stroke-linecap="round"/>
  <circle cx="24" cy="176" r="18" fill="${brand.surface}" stroke="#FFFFFF" stroke-width="4"/>
  <circle cx="156" cy="54" r="24" fill="${primary}"/>
  <circle cx="284" cy="195" r="19" fill="${accent}"/>
  <circle cx="400" cy="91" r="16" fill="${brand.surface}" stroke="${primary}" stroke-width="4"/>
  <path d="M389 80L401 91L389 102" stroke="${primary}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
</g>`;
  }
  if (motif === "event") {
    return `${commonStart}
  <rect x="48" y="26" width="322" height="202" rx="28" fill="${brand.surface}" stroke="#FFFFFF" stroke-opacity=".42" stroke-width="3"/>
  <path d="M48 82H370" stroke="#FFFFFF" stroke-opacity=".28" stroke-width="3"/>
  <rect x="81" y="3" width="18" height="51" rx="9" fill="${primary}"/>
  <rect x="319" y="3" width="18" height="51" rx="9" fill="${primary}"/>
  <path d="M96 157L145 121L193 159L249 98L320 157" stroke="${accent}" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="96" cy="157" r="10" fill="${primary}"/>
  <circle cx="193" cy="159" r="10" fill="${primary}"/>
  <circle cx="320" cy="157" r="10" fill="${primary}"/>
  <path d="M91 197H327" stroke="${primary}" stroke-opacity=".45" stroke-width="4" stroke-linecap="round"/>
</g>`;
  }
  if (motif === "asset") {
    return `${commonStart}
  <circle cx="210" cy="120" r="105" fill="${primary}" fill-opacity=".08" stroke="${primary}" stroke-opacity=".34" stroke-width="3"/>
  <circle cx="210" cy="120" r="72" fill="${brand.surface}" stroke="${accent}" stroke-width="5"/>
  <circle cx="210" cy="120" r="44" fill="${primary}"/>
  <path d="M210 91V149M192 105H219C232 105 232 122 219 122H195M195 122H222C237 122 237 140 222 140H192" stroke="${brand.background}" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M210 15V0M210 240V225M105 120H88M332 120H315M136 46L124 34M296 206L284 194M284 46L296 34M124 206L136 194" stroke="#FFFFFF" stroke-opacity=".46" stroke-width="4" stroke-linecap="round"/>
  <circle cx="345" cy="42" r="16" fill="${accent}"/>
</g>`;
  }
  return `${commonStart}
  <path d="M18 194C58 194 63 128 102 128C141 128 144 169 183 169C223 169 225 63 268 63C311 63 313 136 354 136C377 136 387 111 406 89" stroke="${primary}" stroke-width="8" stroke-linecap="round"/>
  <path d="M18 225H406M18 176H406M18 127H406M18 78H406M18 29H406" stroke="#FFFFFF" stroke-opacity=".08" stroke-width="2"/>
  <rect x="37" y="77" width="30" height="116" rx="15" fill="${accent}" fill-opacity=".72"/>
  <rect x="111" y="111" width="30" height="82" rx="15" fill="${primary}" fill-opacity=".82"/>
  <rect x="185" y="50" width="30" height="143" rx="15" fill="${accent}" fill-opacity=".55"/>
  <rect x="259" y="95" width="30" height="98" rx="15" fill="${primary}" fill-opacity=".72"/>
  <rect x="333" y="28" width="30" height="165" rx="15" fill="${accent}" fill-opacity=".65"/>
</g>`;
}

function sharedDefinitions(brand: ArticleBannerBrand, width: number, height: number): string {
  return `<defs>
  <linearGradient id="Background-Glow" x1="${width}" y1="0" x2="${Math.round(width * .45)}" y2="${height}" gradientUnits="userSpaceOnUse">
    <stop stop-color="${brand.accent}" stop-opacity=".24"/>
    <stop offset=".48" stop-color="${brand.primary}" stop-opacity=".08"/>
    <stop offset="1" stop-color="${brand.background}" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="Panel-Sheen" x1="0" y1="0" x2="1" y2="1">
    <stop stop-color="#FFFFFF" stop-opacity=".1"/>
    <stop offset="1" stop-color="#FFFFFF" stop-opacity=".015"/>
  </linearGradient>
  <pattern id="Editorial-Grid" width="38" height="38" patternUnits="userSpaceOnUse">
    <path d="M38 0H0V38" stroke="#FFFFFF" stroke-opacity=".042"/>
  </pattern>
  <clipPath id="Canvas-Clip"><rect width="${width}" height="${height}"/></clipPath>
</defs>`;
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
  const motif = input.motif || "signal";
  const titleLines = wrapSvgText(title, 17, 3);
  const leadLines = wrapSvgText(lead, 44, 2);
  const titleFontSize = titleLines.length === 3 ? 49 : 54;
  const titleLineHeight = titleLines.length === 3 ? 59 : 65;
  const leadY = Math.min(475, 220 + titleLines.length * titleLineHeight + 34);

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" fill="none" role="img" aria-label="${escapeXml(title)}">
  ${sharedDefinitions(brand, 1200, 630)}
  <g id="Canvas" clip-path="url(#Canvas-Clip)">
    <rect id="Background" width="1200" height="630" fill="${brand.background}"/>
    <rect id="Grid" width="1200" height="630" fill="url(#Editorial-Grid)"/>
    <rect id="Glow" width="1200" height="630" fill="url(#Background-Glow)"/>
    <path id="Accent-Rail" d="M0 0H14V630H0Z" fill="${brand.primary}"/>
    <path id="Top-Accent" d="M14 0H340L294 14H14V0Z" fill="${brand.primary}"/>
    <circle id="Ambient-Ring" cx="1116" cy="42" r="188" stroke="${brand.primary}" stroke-opacity=".11" stroke-width="2"/>
  </g>
  <g id="Header">
    ${logoMarkup(brand, logoDataUrl)}
    <g id="Editorial-Label">
      <rect x="924" y="48" width="204" height="44" rx="22" fill="${brand.primary}"/>
      <text x="1026" y="77" text-anchor="middle" fill="${brand.background}" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="900" letter-spacing="2.4">ARTICLE / INSIGHT</text>
    </g>
    <line x1="72" y1="125" x2="1128" y2="125" stroke="#FFFFFF" stroke-opacity=".16"/>
  </g>
  <g id="Article-Copy">
    <text id="Story-Index" x="72" y="179" fill="${brand.primary}" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="850" letter-spacing="2.6">STORY 01</text>
    ${textLayers(
      "Article-Title",
      titleLines,
      72,
      231,
      titleLineHeight,
      `fill="#FFFFFF" font-family="${escapeXml(brand.displayFont)}, ${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="${titleFontSize}" font-weight="850" letter-spacing="-1.9"`,
    )}
    ${textLayers(
      "Article-Lead",
      leadLines,
      72,
      leadY,
      29,
      `fill="#FFFFFF" fill-opacity=".67" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="19" font-weight="500"`,
    )}
  </g>
  <g id="Visual-Panel">
    <rect x="756" y="150" width="372" height="334" rx="34" fill="${brand.surface}" stroke="#FFFFFF" stroke-opacity=".14" stroke-width="2"/>
    <rect x="756" y="150" width="372" height="334" rx="34" fill="url(#Panel-Sheen)"/>
    <text x="790" y="190" fill="#FFFFFF" fill-opacity=".48" font-family="Inter, Pretendard, sans-serif" font-size="12" font-weight="800" letter-spacing="2.2">VISUAL SIGNAL</text>
    <circle cx="1092" cy="185" r="7" fill="${brand.primary}"/>
    ${motifMarkup(motif, brand, "Hero", 734, 215)}
  </g>
  <g id="Footer">
    <line x1="72" y1="548" x2="1128" y2="548" stroke="#FFFFFF" stroke-opacity=".16"/>
    <circle cx="80" cy="584" r="7" fill="${brand.primary}"/>
    <text x="99" y="590" fill="#FFFFFF" fill-opacity=".58" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="700" letter-spacing=".5">${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1128" y="590" text-anchor="end" fill="#FFFFFF" fill-opacity=".58" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="700">${escapeXml(date)}</text>
  </g>
</svg>`;
}

export function buildArticleInlineVisualSvg(
  clientId: EditableClientId,
  input: ArticleInlineVisualInput,
  logoDataUrl?: string,
): string {
  const brand = ARTICLE_BANNER_BRANDS[clientId];
  const visual = input.visual;
  const headlineLines = wrapSvgText(cleanText(visual.headline, 70), 17, 3);
  const captionLines = wrapSvgText(cleanText(visual.caption, 200), 43, 3);
  const points = visual.points.map(point => cleanText(point, 100)).filter(Boolean).slice(0, 3);
  const eyebrow = cleanText(visual.eyebrow, 32).toUpperCase();
  const eyebrowFontSize = eyebrow.length > 18 ? 11 : eyebrow.length > 14 ? 12 : 14;
  const eyebrowLetterSpacing = eyebrow.length > 18 ? 1.2 : eyebrow.length > 14 ? 1.6 : 2.2;
  const date = cleanText(input.date, 40);
  const pointMarkup = points.map((point, index) => {
    const y = 410 + index * 64;
    const pointLines = wrapSvgText(point, 31, 2);
    const textY = pointLines.length === 1 ? y + 34 : y + 22;
    const lines = pointLines.map((line, lineIndex) => (
      `<text id="Visual-Point-${index + 1}-Line-${lineIndex + 1}" x="697" y="${textY + lineIndex * 18}" fill="#FFFFFF" fill-opacity=".82" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="13.5" font-weight="650">${escapeXml(line)}</text>`
    )).join("\n  ");
    return `<g id="Visual-Point-${index + 1}">
  <rect x="650" y="${y}" width="478" height="56" rx="14" fill="#FFFFFF" fill-opacity="${index === 0 ? ".1" : ".055"}" stroke="#FFFFFF" stroke-opacity=".1"/>
  <circle cx="676" cy="${y + 28}" r="8" fill="${index === 1 ? brand.accent : brand.primary}"/>
  ${lines}
</g>`;
  }).join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675" fill="none" role="img" aria-label="${escapeXml(visual.headline)}">
  ${sharedDefinitions(brand, 1200, 675)}
  <g id="Canvas" clip-path="url(#Canvas-Clip)">
    <rect id="Background" width="1200" height="675" fill="${brand.background}"/>
    <rect id="Grid" width="1200" height="675" fill="url(#Editorial-Grid)"/>
    <rect id="Glow" width="1200" height="675" fill="url(#Background-Glow)"/>
    <path id="Accent-Rail" d="M0 0H14V675H0Z" fill="${brand.primary}"/>
    <path id="Corner-Accent" d="M1064 675H1200V539L1064 675Z" fill="${brand.primary}" fill-opacity=".9"/>
  </g>
  <g id="Header">
    ${logoMarkup(brand, logoDataUrl)}
    <g id="Visual-Label">
      <rect x="914" y="47" width="214" height="42" rx="21" fill="${brand.primary}"/>
      <text x="1021" y="75" text-anchor="middle" fill="${brand.background}" font-family="Inter, Pretendard, sans-serif" font-size="${eyebrowFontSize}" font-weight="900" letter-spacing="${eyebrowLetterSpacing}">${escapeXml(eyebrow)}</text>
    </g>
    <line x1="72" y1="124" x2="1128" y2="124" stroke="#FFFFFF" stroke-opacity=".16"/>
  </g>
  <g id="Editorial-Copy">
    <text x="72" y="171" fill="${brand.primary}" font-family="Inter, Pretendard, sans-serif" font-size="14" font-weight="850" letter-spacing="2.4">${escapeXml(visual.role === "overview" ? "CONTEXT / 01" : "EXPLAINER / 02")}</text>
    ${textLayers(
      "Visual-Headline",
      headlineLines,
      72,
      229,
      57,
      `fill="#FFFFFF" font-family="${escapeXml(brand.displayFont)}, ${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="47" font-weight="850" letter-spacing="-1.7"`,
    )}
    ${textLayers(
      "Visual-Caption",
      captionLines,
      72,
      Math.min(500, 249 + headlineLines.length * 57 + 42),
      28,
      `fill="#FFFFFF" fill-opacity=".66" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="18" font-weight="500"`,
    )}
  </g>
  <g id="Diagram-Panel">
    <rect x="624" y="145" width="530" height="456" rx="34" fill="${brand.surface}" stroke="#FFFFFF" stroke-opacity=".14" stroke-width="2"/>
    <rect x="624" y="145" width="530" height="456" rx="34" fill="url(#Panel-Sheen)"/>
    ${motifMarkup(visual.motif, brand, `Inline-${visual.id}`, 674, 164)}
    ${pointMarkup}
  </g>
  <g id="Footer">
    <text x="72" y="631" fill="#FFFFFF" fill-opacity=".46" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="700" letter-spacing="1.2">SOURCE-LOCKED EDITORIAL VISUAL · ${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1008" y="631" text-anchor="end" fill="#FFFFFF" fill-opacity=".46" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="700">${escapeXml(date)}</text>
  </g>
</svg>`;
}
