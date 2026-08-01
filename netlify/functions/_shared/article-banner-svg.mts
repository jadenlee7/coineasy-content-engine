import {
  escapeXml,
  fitSvgText,
  wrapSvgText,
  type EditableClientId,
} from "./editable-svg.mts";
import type {
  ArticleVisualBrief,
  ArticleVisualMotif,
} from "./article-visual-plan.mts";
import {
  OFFICIAL_BRAND_ASSETS,
  type ArticleHeroBrandAssets,
} from "./official-brand-assets.mts";

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
  editorialLabel: string;
  signalLabel: string;
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
    editorialLabel: "YELLOW / MARKET BRIEF",
    signalLabel: "MARKET STRUCTURE",
  },
  origintrail: {
    name: "OriginTrail Korea",
    primary: "#6344DF",
    accent: "#A993FF",
    background: "#0C2246",
    surface: "#173665",
    displayFont: "Gmarket Sans",
    bodyFont: "Pretendard",
    editorialLabel: "ORIGINTRAIL / TRUST BRIEF",
    signalLabel: "TRUST MAP",
  },
  squid: {
    name: "Squid",
    primary: "#EFFF5A",
    accent: "#E6CCFC",
    background: "#1A0E2E",
    surface: "#2E1B48",
    displayFont: "Bagoss Condensed",
    bodyFont: "Pretendard",
    editorialLabel: "SQUID / PRODUCT NOTE",
    signalLabel: "ROUTE IN MOTION",
  },
  babylon: {
    name: "Babylon Korea",
    primary: "#CE6533",
    accent: "#F7931A",
    background: "#12495E",
    surface: "#1A5C73",
    displayFont: "Inter",
    bodyFont: "Pretendard",
    editorialLabel: "BABYLON / BITCOIN BRIEF",
    signalLabel: "BITCOIN MECHANISM",
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
  clientId: EditableClientId,
  brand: ArticleBannerBrand,
  logoDataUrl: string,
  x = 72,
): string {
  if (!logoDataUrl) throw new Error("official_logo_required");
  const layout = OFFICIAL_BRAND_ASSETS[clientId].article;
  const officialLogo = `<image id="Brand-Official-Logo" x="${x}" y="${layout.y}" width="${layout.width}" height="${layout.height}" href="${escapeXml(logoDataUrl)}" preserveAspectRatio="xMinYMid meet"/>`;
  if (layout.localMarketName) {
    return `<g id="Brand-Official-Lockup">
  ${officialLogo}
  <text id="Brand-Local-Market-Name" x="${x + layout.width + 14}" y="${layout.y + 39}" fill="#FFFFFF" font-family="${escapeXml(brand.bodyFont)}, Pretendard, sans-serif" font-size="22" font-weight="800">${escapeXml(layout.localMarketName)}</text>
</g>`;
  }
  return officialLogo;
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
  <linearGradient id="OriginTrail-Network-Glow" x1="730" y1="110" x2="1132" y2="536" gradientUnits="userSpaceOnUse">
    <stop stop-color="#A993FF" stop-opacity=".34"/>
    <stop offset=".55" stop-color="#6344DF" stop-opacity=".12"/>
    <stop offset="1" stop-color="#0C2246" stop-opacity="0"/>
  </linearGradient>
  <filter id="Editorial-Shadow" x="-20%" y="-20%" width="140%" height="160%" color-interpolation-filters="sRGB">
    <feDropShadow dx="0" dy="14" stdDeviation="18" flood-color="#000000" flood-opacity=".14"/>
  </filter>
  <filter id="Orb-Shadow" x="-40%" y="-40%" width="180%" height="190%" color-interpolation-filters="sRGB">
    <feDropShadow dx="0" dy="20" stdDeviation="18" flood-color="#65408F" flood-opacity=".28"/>
  </filter>
  <clipPath id="Canvas-Clip"><rect width="${width}" height="${height}"/></clipPath>
</defs>`;
}

function squidDefinitions(width: number, height: number): string {
  return `<defs>
  <filter id="Orb-Shadow" x="-40%" y="-40%" width="180%" height="190%" color-interpolation-filters="sRGB">
    <feDropShadow dx="0" dy="18" stdDeviation="17" flood-color="#65408F" flood-opacity=".24"/>
  </filter>
  <clipPath id="Canvas-Clip"><rect width="${width}" height="${height}"/></clipPath>
</defs>`;
}

function brandAtmosphereMarkup(
  clientId: EditableClientId,
  brand: ArticleBannerBrand,
  height: number,
): string {
  if (clientId === "yellow") {
    return `<g id="Brand-Atmosphere-Yellow" opacity=".38">
  <path d="M842 0V${height}M940 0V${height}M1038 0V${height}" stroke="${brand.primary}" stroke-opacity=".08"/>
  <path d="M804 116H1162M804 214H1162M804 312H1162" stroke="#FFFFFF" stroke-opacity=".045"/>
  <path d="M1045 22L1178 22L1126 74L993 74Z" fill="${brand.primary}" fill-opacity=".18"/>
  <circle cx="1090" cy="116" r="5" fill="${brand.primary}"/>
</g>`;
  }
  if (clientId === "origintrail") {
    return `<g id="Brand-Atmosphere-OriginTrail" opacity=".34">
  <path d="M825 48L914 19L1010 83L1117 31M914 19L957 148L1117 31M1010 83L1151 151" stroke="${brand.primary}" stroke-opacity=".28" stroke-width="2"/>
  <circle cx="825" cy="48" r="8" fill="${brand.surface}" stroke="${brand.accent}" stroke-width="2"/>
  <circle cx="914" cy="19" r="11" fill="${brand.primary}"/>
  <circle cx="1010" cy="83" r="7" fill="${brand.accent}"/>
  <circle cx="1117" cy="31" r="9" fill="${brand.surface}" stroke="${brand.primary}" stroke-width="2"/>
  <circle cx="1151" cy="151" r="5" fill="${brand.accent}"/>
</g>`;
  }
  if (clientId === "squid") {
    return `<g id="Brand-Atmosphere-Squid" opacity=".4">
  <path d="M812 34C895 5 934 96 1017 59C1089 27 1111 75 1192 38" stroke="${brand.primary}" stroke-opacity=".25" stroke-width="8" stroke-linecap="round"/>
  <path d="M862 99C918 60 971 145 1032 105C1087 69 1128 132 1184 101" stroke="${brand.accent}" stroke-opacity=".28" stroke-width="4" stroke-linecap="round"/>
  <circle cx="821" cy="36" r="11" fill="${brand.primary}" fill-opacity=".5"/>
  <circle cx="1181" cy="102" r="8" fill="${brand.accent}" fill-opacity=".72"/>
</g>`;
  }
  return `<g id="Brand-Atmosphere-Babylon" opacity=".36">
  <path d="M874 18L1001 18L1061 70L934 70Z" fill="${brand.primary}" fill-opacity=".2" stroke="${brand.primary}" stroke-opacity=".35"/>
  <path d="M934 70L1061 70L1121 122L994 122Z" fill="${brand.accent}" fill-opacity=".12" stroke="${brand.accent}" stroke-opacity=".3"/>
  <path d="M994 122L1121 122L1181 174L1054 174Z" fill="${brand.surface}" stroke="#FFFFFF" stroke-opacity=".15"/>
  <path d="M1001 18V64M1061 70V116M1121 122V168" stroke="#FFFFFF" stroke-opacity=".14" stroke-dasharray="5 7"/>
</g>`;
}

type ArticleHeroCopy = {
  title: string;
  lead: string;
  date: string;
  motif: ArticleVisualMotif;
  titleLines: string[];
  leadLines: string[];
  titleFontSize: number;
  titleLineHeight: number;
  leadY: number;
};

function articleHeroCopy(
  input: ArticleBannerInput,
  titleMaxUnits: number,
  leadMaxUnits: number,
  titleY: number,
): ArticleHeroCopy {
  const title = cleanText(input.title, 200) || "새로운 소식을 전합니다";
  const lead = cleanText(input.lead, 800);
  const titleLines = wrapSvgText(title, titleMaxUnits, 3);
  const leadLines = wrapSvgText(lead, leadMaxUnits, 2);
  const titleFontSize = titleLines.length === 3 ? 43 : titleLines.length === 2 ? 49 : 54;
  const titleLineHeight = titleLines.length === 3 ? 51 : titleLines.length === 2 ? 58 : 64;
  return {
    title,
    lead,
    date: cleanText(input.date, 40),
    motif: input.motif || "signal",
    titleLines,
    leadLines,
    titleFontSize,
    titleLineHeight,
    leadY: Math.min(466, titleY + (titleLines.length - 1) * titleLineHeight + 92),
  };
}

function squidTopicEyebrow(...values: Array<string | undefined>): string {
  const source = cleanText(values.filter(Boolean).join(" "), 800);
  const topics: Array<[RegExp, string]> = [
    [/\bcanton\b/i, "CANTON × SQUID"],
    [/\bxrp\b/i, "XRP × SQUID"],
    [/\b(?:\$?quid|tge)\b/i, "$QUID"],
    [/\b(?:bitcoin|btc)\b|비트코인/i, "BITCOIN × SQUID"],
    [/\bsolana\b/i, "SOLANA × SQUID"],
    [/\bsui\b/i, "SUI × SQUID"],
    [/\bethereum\b|이더리움/i, "ETHEREUM × SQUID"],
    [/cross[ -]?chain|크로스[ -]?체인/i, "CROSS-CHAIN × SQUID"],
    [/liquidity|유동성/i, "LIQUIDITY × SQUID"],
    [/routing|라우팅/i, "ROUTING × SQUID"],
    [/bridge|브리지/i, "BRIDGE × SQUID"],
    [/swap|스왑/i, "SWAP × SQUID"],
  ];
  const topic = topics.find(([pattern]) => pattern.test(source));
  if (topic) return topic[1];
  return "SQUID × KOREA";
}

function squidOfficialWorldMarkup(
  motif: ArticleVisualMotif,
  assets: ArticleHeroBrandAssets,
  mode: "hero" | "inline",
): string {
  const prefix = mode === "hero" ? "Hero" : "Inline";
  const compositionId = `Squid-${prefix}-Composition-${motif}`;
  const art = (values: {
    haze: string;
    form: string;
    bubbles: string;
    squib: string;
    accent: string;
  }) => `<g id="${compositionId}">
  ${values.haze}
  <image id="Squid-Official-Form-Language-${prefix}" ${values.form} href="${escapeXml(assets.formLanguage)}" preserveAspectRatio="xMidYMid meet"/>
  <image id="Squid-Official-Bubbles-${prefix}" ${values.bubbles} href="${escapeXml(assets.bubbles)}" preserveAspectRatio="xMidYMid meet"/>
  ${values.accent}
  <image id="Squid-Official-SQUIB-${prefix}" ${values.squib} href="${escapeXml(assets.squib)}" preserveAspectRatio="xMidYMid meet" filter="url(#Orb-Shadow)"/>
</g>`;

  if (mode === "hero") {
    if (motif === "flow") {
      return art({
        haze: '<ellipse cx="1000" cy="158" rx="260" ry="202" fill="#FFFFFF" fill-opacity=".72"/>',
        form: 'x="692" y="-238" width="690" height="690" opacity=".54" transform="rotate(12 1037 107)"',
        bubbles: 'x="792" y="-8" width="382" height="382" opacity=".42"',
        squib: 'x="810" y="-76" width="466" height="466" transform="rotate(5 1043 157)"',
        accent: '<path d="M736 179C840 132 936 209 1044 151C1103 119 1148 126 1200 98" stroke="#EFFF5A" stroke-width="14" stroke-linecap="round"/>',
      });
    }
    if (motif === "network") {
      return art({
        haze: '<circle cx="1010" cy="150" r="226" fill="#FFFFFF" fill-opacity=".68"/>',
        form: 'x="760" y="-164" width="578" height="578" opacity=".5" transform="rotate(-18 1049 125)"',
        bubbles: 'x="730" y="-24" width="430" height="430" opacity=".66"',
        squib: 'x="838" y="-56" width="430" height="430" transform="rotate(-4 1053 159)"',
        accent: '<circle cx="782" cy="197" r="18" fill="#EFFF5A"/><circle cx="1170" cy="250" r="11" fill="#EFFF5A"/>',
      });
    }
    if (motif === "layers") {
      return art({
        haze: '<ellipse cx="1008" cy="126" rx="286" ry="190" fill="#FFFFFF" fill-opacity=".7"/>',
        form: 'x="718" y="-96" width="640" height="640" opacity=".46" transform="rotate(28 1038 224)"',
        bubbles: 'x="846" y="-72" width="354" height="354" opacity=".4"',
        squib: 'x="792" y="-58" width="446" height="446" transform="rotate(-9 1015 165)"',
        accent: '<path d="M786 234H1170M820 258H1138" stroke="#EFFF5A" stroke-width="7" stroke-linecap="round" stroke-opacity=".86"/>',
      });
    }
    if (motif === "event") {
      return art({
        haze: '<ellipse cx="1014" cy="137" rx="242" ry="212" fill="#FFFFFF" fill-opacity=".74"/>',
        form: 'x="692" y="-194" width="680" height="680" opacity=".5" transform="rotate(-4 1032 146)"',
        bubbles: 'x="760" y="-2" width="430" height="430" opacity=".46"',
        squib: 'x="844" y="-96" width="468" height="468" transform="rotate(8 1078 138)"',
        accent: '<path d="M732 66H852" stroke="#EFFF5A" stroke-width="16" stroke-linecap="round"/><circle cx="1180" cy="226" r="16" fill="#EFFF5A"/>',
      });
    }
    if (motif === "asset") {
      return art({
        haze: '<circle cx="1019" cy="137" r="224" fill="#EFFF5A" fill-opacity=".74"/><circle cx="1019" cy="137" r="178" fill="#FFFFFF" fill-opacity=".9"/>',
        form: 'x="744" y="-112" width="594" height="594" opacity=".38" transform="rotate(18 1041 185)"',
        bubbles: 'x="728" y="-28" width="430" height="430" opacity=".48"',
        squib: 'x="826" y="-68" width="448" height="448" transform="rotate(-5 1050 156)"',
        accent: '<circle cx="784" cy="220" r="10" fill="#000000"/><circle cx="1182" cy="80" r="8" fill="#000000"/>',
      });
    }
    return art({
      haze: '<ellipse cx="1002" cy="145" rx="270" ry="206" fill="#FFFFFF" fill-opacity=".72"/>',
      form: 'x="710" y="-214" width="690" height="690" opacity=".48" transform="rotate(-12 1055 131)"',
      bubbles: 'x="750" y="-18" width="408" height="408" opacity=".5"',
      squib: 'x="820" y="-72" width="456" height="456" transform="rotate(3 1048 156)"',
      accent: '<path d="M754 235C892 205 999 249 1176 192" stroke="#EFFF5A" stroke-width="12" stroke-linecap="round"/>',
    });
  }

  if (motif === "flow") {
    return art({
      haze: '<ellipse cx="997" cy="222" rx="272" ry="236" fill="#FFFFFF" fill-opacity=".74"/>',
      form: 'x="666" y="-116" width="720" height="720" opacity=".54" transform="rotate(15 1026 244)"',
      bubbles: 'x="726" y="48" width="470" height="470" opacity=".45"',
      squib: 'x="792" y="8" width="520" height="520" transform="rotate(6 1052 268)"',
      accent: '<path d="M690 306C820 249 925 342 1059 263C1113 231 1160 226 1200 205" stroke="#EFFF5A" stroke-width="17" stroke-linecap="round"/>',
    });
  }
  if (motif === "network") {
    return art({
      haze: '<circle cx="1007" cy="219" r="244" fill="#FFFFFF" fill-opacity=".7"/>',
      form: 'x="730" y="-60" width="640" height="640" opacity=".48" transform="rotate(-20 1050 260)"',
      bubbles: 'x="692" y="42" width="500" height="500" opacity=".7"',
      squib: 'x="824" y="12" width="492" height="492" transform="rotate(-4 1070 258)"',
      accent: '<circle cx="730" cy="308" r="20" fill="#EFFF5A"/><circle cx="1180" cy="365" r="13" fill="#EFFF5A"/>',
    });
  }
  if (motif === "layers") {
    return art({
      haze: '<ellipse cx="1006" cy="196" rx="294" ry="232" fill="#FFFFFF" fill-opacity=".72"/>',
      form: 'x="686" y="-48" width="720" height="720" opacity=".46" transform="rotate(29 1046 312)"',
      bubbles: 'x="806" y="8" width="416" height="416" opacity=".42"',
      squib: 'x="772" y="12" width="514" height="514" transform="rotate(-9 1029 269)"',
      accent: '<path d="M720 351H1180M760 383H1140" stroke="#EFFF5A" stroke-width="8" stroke-linecap="round"/>',
    });
  }
  if (motif === "event") {
    return art({
      haze: '<ellipse cx="1020" cy="204" rx="258" ry="250" fill="#FFFFFF" fill-opacity=".76"/>',
      form: 'x="674" y="-106" width="730" height="730" opacity=".5" transform="rotate(-5 1039 259)"',
      bubbles: 'x="700" y="58" width="500" height="500" opacity=".48"',
      squib: 'x="824" y="-12" width="530" height="530" transform="rotate(9 1089 253)"',
      accent: '<path d="M716 84H872" stroke="#EFFF5A" stroke-width="18" stroke-linecap="round"/><circle cx="1180" cy="342" r="18" fill="#EFFF5A"/>',
    });
  }
  if (motif === "asset") {
    return art({
      haze: '<circle cx="1010" cy="218" r="248" fill="#EFFF5A" fill-opacity=".76"/><circle cx="1010" cy="218" r="196" fill="#FFFFFF" fill-opacity=".92"/>',
      form: 'x="718" y="-42" width="650" height="650" opacity=".36" transform="rotate(18 1043 283)"',
      bubbles: 'x="692" y="28" width="510" height="510" opacity=".5"',
      squib: 'x="810" y="4" width="512" height="512" transform="rotate(-5 1066 260)"',
      accent: '<circle cx="730" cy="350" r="11" fill="#000000"/><circle cx="1184" cy="116" r="9" fill="#000000"/>',
    });
  }
  return art({
    haze: '<ellipse cx="1004" cy="216" rx="280" ry="244" fill="#FFFFFF" fill-opacity=".74"/>',
    form: 'x="678" y="-124" width="730" height="730" opacity=".48" transform="rotate(-12 1043 241)"',
    bubbles: 'x="708" y="44" width="486" height="486" opacity=".52"',
    squib: 'x="808" y="0" width="518" height="518" transform="rotate(3 1067 259)"',
    accent: '<path d="M710 363C862 330 982 382 1182 310" stroke="#EFFF5A" stroke-width="14" stroke-linecap="round"/>',
  });
}

function yellowArticleHero(
  input: ArticleBannerInput,
  logoDataUrl: string,
): string {
  const brand = ARTICLE_BANNER_BRANDS.yellow;
  const copy = articleHeroCopy(input, 19.5, 37, 230);
  const motifBrand = {
    ...brand,
    primary: "#FDDA16",
    accent: "#19191C",
    background: "#19191C",
    surface: "#FFFFFF",
  };
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" fill="none" role="img" aria-label="${escapeXml(copy.title)}">
  ${sharedDefinitions(brand, 1200, 630)}
  <g id="Editorial-Hero-v2" clip-path="url(#Canvas-Clip)">
    <rect id="Background" width="1200" height="630" fill="#FDDA16"/>
    <circle cx="1168" cy="20" r="190" fill="#FFE95B" fill-opacity=".72"/>
    <path d="M28 155H1172M28 475H1172" stroke="#19191C" stroke-opacity=".06"/>
    <rect id="Hero-Yellow-Studio" x="28" y="26" width="1144" height="578" rx="34" fill="#FFFFFF" stroke="#19191C" stroke-opacity=".08" filter="url(#Editorial-Shadow)"/>
    <path d="M28 60C28 41.2223 43.2223 26 62 26H395L351 40H28V60Z" fill="#FDDA16"/>
  </g>
  <g id="Header">
    <g id="Editorial-Label">
      <rect x="76" y="58" width="186" height="38" rx="7" fill="#19191C"/>
      <text x="169" y="83" text-anchor="middle" fill="#FDDA16" font-family="Inter, Pretendard, sans-serif" font-size="12" font-weight="900" letter-spacing="1.5">YELLOW / ARTICLE 01</text>
    </g>
    ${logoMarkup("yellow", brand, logoDataUrl, 914)}
    <line x1="76" y1="124" x2="1124" y2="124" stroke="#19191C" stroke-opacity=".12"/>
  </g>
  <g id="Article-Copy">
    <text id="Story-Index" x="76" y="174" fill="#64646B" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="800" letter-spacing="1.9">KOREA MARKET INTELLIGENCE</text>
    ${textLayers(
      "Article-Title",
      copy.titleLines,
      76,
      230,
      copy.titleLineHeight,
      `fill="#19191C" font-family="Pretendard, sans-serif" font-size="${copy.titleFontSize}" font-weight="900" letter-spacing="-1.7"`,
    )}
    ${textLayers(
      "Article-Lead",
      copy.leadLines,
      76,
      copy.leadY,
      29,
      `fill="#555555" font-family="Pretendard, sans-serif" font-size="17" font-weight="550" letter-spacing="-.15"`,
    )}
  </g>
  <g id="Visual-Panel">
    <rect x="790" y="154" width="334" height="348" rx="26" fill="#F0F0F5"/>
    <text x="820" y="192" fill="#77777E" font-family="Inter, Pretendard, sans-serif" font-size="11.5" font-weight="900" letter-spacing="1.7">${escapeXml(brand.signalLabel)}</text>
    <circle cx="1090" cy="184" r="8" fill="#FDDA16" stroke="#19191C" stroke-width="2"/>
    <g transform="translate(802 222) scale(.7)">
      ${motifMarkup(copy.motif, motifBrand, "Hero", 0, 0)}
    </g>
    <g id="Visual-Metadata">
      <rect x="816" y="448" width="128" height="32" rx="16" fill="#FFFFFF"/>
      <circle cx="834" cy="464" r="5" fill="#FDDA16"/>
      <text x="847" y="469" fill="#19191C" font-family="Inter, Pretendard, sans-serif" font-size="9.8" font-weight="850" letter-spacing=".6">SOURCE LOCKED</text>
      <rect x="956" y="448" width="144" height="32" rx="16" fill="#19191C"/>
      <text x="1028" y="469" text-anchor="middle" fill="#FFFFFF" font-family="Inter, Pretendard, sans-serif" font-size="9.8" font-weight="850" letter-spacing=".7">COINEASY / KOREA</text>
    </g>
  </g>
  <g id="Footer">
    <line x1="76" y1="544" x2="1124" y2="544" stroke="#19191C" stroke-opacity=".12"/>
    <text x="76" y="577" fill="#64646B" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="750" letter-spacing=".5">${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1124" y="577" text-anchor="end" fill="#64646B" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="750">${escapeXml(copy.date)}</text>
  </g>
</svg>`;
}

function squidArticleHero(
  input: ArticleBannerInput,
  logoDataUrl: string,
  heroAssets: ArticleHeroBrandAssets | null | undefined,
): string {
  if (!heroAssets?.formLanguage || !heroAssets.squib || !heroAssets.bubbles) {
    throw new Error("official_squid_hero_assets_required");
  }
  const brand = ARTICLE_BANNER_BRANDS.squid;
  const title = cleanText(input.title, 200) || "새로운 소식을 전합니다";
  const lead = cleanText(input.lead, 800);
  const motif = input.motif || "signal";
  const titleLayout = fitSvgText(title, {
    maxWidth: 840,
    maxLines: 2,
    maxFontSize: 72,
    minFontSize: 56,
    lineHeightRatio: 1.12,
  });
  const titleLines = titleLayout.lines;
  const leadLines = wrapSvgText(lead, 54, 2);
  const titleFontSize = titleLayout.fontSize;
  const titleLineHeight = titleLayout.lineHeight;
  const dividerY = 260 + (titleLines.length - 1) * titleLineHeight + 39;
  const leadY = dividerY + 51;
  const topicEyebrow = squidTopicEyebrow(title, lead);
  const date = cleanText(input.date, 40);
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" fill="none" role="img" aria-label="${escapeXml(title)}">
  ${squidDefinitions(1200, 630)}
  <g id="Editorial-Hero-v2" clip-path="url(#Canvas-Clip)">
    <rect id="Background" width="1200" height="630" fill="#E6CCFC"/>
    <radialGradient id="Squid-White-Haze" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(438 618) rotate(-70) scale(470 610)">
      <stop stop-color="#FFFFFF" stop-opacity=".96"/>
      <stop offset=".62" stop-color="#FFFFFF" stop-opacity=".36"/>
      <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
    </radialGradient>
    <rect width="1200" height="630" fill="url(#Squid-White-Haze)"/>
    ${squidOfficialWorldMarkup(motif, heroAssets, "hero")}
  </g>
  <g id="Header">
    ${logoMarkup("squid", brand, logoDataUrl, 60)}
    <text id="Article-Date" x="60" y="130" fill="#3D2E5C" fill-opacity=".82" font-family="Inter, Pretendard, sans-serif" font-size="16" font-weight="650" letter-spacing=".5">${escapeXml(date)}</text>
  </g>
  <g id="Article-Copy">
    <text id="Story-Topic" x="60" y="178" fill="#1C0F3D" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="850" letter-spacing="1.65">${escapeXml(topicEyebrow)}</text>
    ${textLayers(
      "Article-Title",
      titleLines,
      60,
      260,
      titleLineHeight,
      `fill="#000000" font-family="Bagoss Condensed, Pretendard, sans-serif" font-size="${titleFontSize}" font-weight="900" letter-spacing="-2"`,
    )}
    <rect id="Squid-Lime-Divider" x="60" y="${dividerY}" width="282" height="8" fill="#EFFF5A"/>
    ${textLayers(
      "Article-Lead",
      leadLines,
      60,
      leadY,
      31,
      `fill="#1C0F3D" fill-opacity=".86" font-family="Pretendard, sans-serif" font-size="19" font-weight="560" letter-spacing="-.15"`,
    )}
  </g>
  <g id="Footer">
    <line x1="60" y1="568" x2="1140" y2="568" stroke="#1C0F3D" stroke-opacity=".18"/>
    <text x="60" y="600" fill="#1C0F3D" fill-opacity=".68" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="700" letter-spacing=".4">${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1140" y="600" text-anchor="end" fill="#1C0F3D" fill-opacity=".68" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="700">COINEASY · KOREA GTM</text>
  </g>
</svg>`;
}

function originTrailArticleHero(
  input: ArticleBannerInput,
  logoDataUrl: string,
): string {
  const brand = ARTICLE_BANNER_BRANDS.origintrail;
  const copy = articleHeroCopy(input, 19.5, 37, 234);
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" fill="none" role="img" aria-label="${escapeXml(copy.title)}">
  ${sharedDefinitions(brand, 1200, 630)}
  <g id="Editorial-Hero-v2" clip-path="url(#Canvas-Clip)">
    <rect id="Background" width="1200" height="630" fill="#0C2246"/>
    <rect width="1200" height="630" fill="url(#Editorial-Grid)"/>
    <ellipse cx="1005" cy="292" rx="424" ry="370" fill="url(#OriginTrail-Network-Glow)"/>
    <path d="M0 0H18V630H0Z" fill="#6344DF"/>
    <path d="M18 0H390L338 16H18V0Z" fill="#6344DF"/>
  </g>
  <g id="Header">
    ${logoMarkup("origintrail", brand, logoDataUrl, 72)}
    <g id="Editorial-Label">
      <rect x="874" y="48" width="254" height="42" rx="21" fill="#6344DF"/>
      <text x="1001" y="75" text-anchor="middle" fill="#FFFFFF" font-family="Inter, Pretendard, sans-serif" font-size="11.5" font-weight="900" letter-spacing="1.25">TRUSTED KNOWLEDGE / 01</text>
    </g>
    <line x1="72" y1="126" x2="1128" y2="126" stroke="#FFFFFF" stroke-opacity=".15"/>
  </g>
  <g id="Article-Copy">
    <text id="Story-Index" x="72" y="178" fill="#A993FF" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="850" letter-spacing="2">ORIGINTRAIL KOREA / TRUST BRIEF</text>
    ${textLayers(
      "Article-Title",
      copy.titleLines,
      72,
      234,
      copy.titleLineHeight,
      `fill="#FFFFFF" font-family="Gmarket Sans, Pretendard, sans-serif" font-size="${copy.titleFontSize}" font-weight="850" letter-spacing="-1.7"`,
    )}
    ${textLayers(
      "Article-Lead",
      copy.leadLines,
      72,
      copy.leadY,
      29,
      `fill="#FFFFFF" fill-opacity=".68" font-family="Gmarket Sans, Pretendard, sans-serif" font-size="17" font-weight="500"`,
    )}
  </g>
  <g id="Hero-OriginTrail-Knowledge-Graph">
    <path d="M787 215L884 146L976 202L1082 129M884 146L917 294L1082 129M976 202L1119 295M917 294L1045 416M787 215L829 390L917 294M829 390L1045 416L1119 295" stroke="#A993FF" stroke-opacity=".52" stroke-width="3"/>
    <path d="M787 215L976 202L1045 416" stroke="#6344DF" stroke-width="9" stroke-linecap="round" stroke-opacity=".62"/>
    <circle cx="787" cy="215" r="22" fill="#173665" stroke="#A993FF" stroke-width="4"/>
    <circle cx="884" cy="146" r="30" fill="#6344DF"/>
    <circle cx="976" cy="202" r="18" fill="#A993FF"/>
    <circle cx="1082" cy="129" r="14" fill="#FFFFFF"/>
    <circle cx="917" cy="294" r="27" fill="#173665" stroke="#FFFFFF" stroke-opacity=".72" stroke-width="4"/>
    <circle cx="829" cy="390" r="16" fill="#A993FF"/>
    <circle cx="1045" cy="416" r="31" fill="#6344DF" stroke="#A993FF" stroke-width="4"/>
    <circle cx="1119" cy="295" r="12" fill="#FFFFFF"/>
    <circle cx="884" cy="146" r="44" stroke="#A993FF" stroke-opacity=".2" stroke-width="2"/>
    <text x="917" y="300" text-anchor="middle" fill="#FFFFFF" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="900">DKG</text>
  </g>
  <g id="Footer">
    <line x1="72" y1="548" x2="1128" y2="548" stroke="#FFFFFF" stroke-opacity=".15"/>
    <circle cx="80" cy="584" r="7" fill="#6344DF"/>
    <text x="99" y="590" fill="#FFFFFF" fill-opacity=".62" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="750" letter-spacing=".45">${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1128" y="590" text-anchor="end" fill="#FFFFFF" fill-opacity=".62" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="750">${escapeXml(copy.date)}</text>
  </g>
</svg>`;
}

function babylonArticleHero(
  input: ArticleBannerInput,
  logoDataUrl: string,
): string {
  const brand = ARTICLE_BANNER_BRANDS.babylon;
  const copy = articleHeroCopy(input, 19.5, 37, 234);
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630" fill="none" role="img" aria-label="${escapeXml(copy.title)}">
  ${sharedDefinitions(brand, 1200, 630)}
  <g id="Editorial-Hero-v2" clip-path="url(#Canvas-Clip)">
    <rect id="Background" width="1200" height="630" fill="#12495E"/>
    <circle cx="1098" cy="86" r="266" fill="#CE6533" fill-opacity=".11"/>
    <path d="M0 538C214 493 373 603 586 548C812 489 970 566 1200 506V630H0V538Z" fill="#0B3B4D"/>
    <image id="Hero-Babylon-Symbol" x="815" y="-96" width="470" height="470" href="${escapeXml(logoDataUrl)}" preserveAspectRatio="xMidYMid meet" opacity=".28"/>
    <path d="M26 0H424L376 16H26V0Z" fill="#CE6533"/>
  </g>
  <g id="Header">
    ${logoMarkup("babylon", brand, logoDataUrl, 72)}
    <g id="Editorial-Label">
      <rect x="904" y="48" width="224" height="42" rx="21" fill="#CE6533"/>
      <text x="1016" y="75" text-anchor="middle" fill="#FFFFFF" font-family="Inter, Pretendard, sans-serif" font-size="11.5" font-weight="900" letter-spacing="1.2">BITCOIN NATIVE / 01</text>
    </g>
    <line x1="72" y1="126" x2="1128" y2="126" stroke="#FFFFFF" stroke-opacity=".16"/>
  </g>
  <g id="Article-Copy">
    <g id="Story-Index">
      <rect x="72" y="153" width="126" height="36" rx="18" fill="#FFFFFF" fill-opacity=".1"/>
      <text x="135" y="177" text-anchor="middle" fill="#F7B58F" font-family="Inter, Pretendard, sans-serif" font-size="12" font-weight="850" letter-spacing="1.1">BTCfi · BRIEF</text>
    </g>
    ${textLayers(
      "Article-Title",
      copy.titleLines,
      72,
      234,
      copy.titleLineHeight,
      `fill="#FFFFFF" font-family="Inter, Pretendard, sans-serif" font-size="${copy.titleFontSize}" font-weight="850" letter-spacing="-1.8"`,
    )}
    ${textLayers(
      "Article-Lead",
      copy.leadLines,
      72,
      copy.leadY,
      29,
      `fill="#FFFFFF" fill-opacity=".68" font-family="Inter, Pretendard, sans-serif" font-size="17" font-weight="520"`,
    )}
  </g>
  <g id="Hero-Babylon-Proof-Panel">
    <rect x="818" y="360" width="310" height="142" rx="18" fill="#F5E0D6" stroke="#FFFFFF" stroke-opacity=".8" stroke-width="2" filter="url(#Editorial-Shadow)"/>
    <text x="846" y="400" fill="#CE6533" font-family="Inter, Pretendard, sans-serif" font-size="11.5" font-weight="900" letter-spacing="1.5">${escapeXml(brand.signalLabel)}</text>
    <path d="M846 426H1100" stroke="#CE6533" stroke-opacity=".32"/>
    <circle cx="857" cy="460" r="9" fill="#CE6533"/>
    <text x="880" y="466" fill="#12495E" font-family="Inter, Pretendard, sans-serif" font-size="13.5" font-weight="800">NATIVE BTC</text>
    <circle cx="992" cy="460" r="9" fill="#F7931A"/>
    <text x="1015" y="466" fill="#12495E" font-family="Inter, Pretendard, sans-serif" font-size="13.5" font-weight="800">LOCKED</text>
  </g>
  <g id="Footer">
    <line x1="72" y1="548" x2="1128" y2="548" stroke="#FFFFFF" stroke-opacity=".16"/>
    <circle cx="80" cy="584" r="7" fill="#CE6533"/>
    <text x="99" y="590" fill="#FFFFFF" fill-opacity=".62" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="750" letter-spacing=".45">${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1128" y="590" text-anchor="end" fill="#FFFFFF" fill-opacity=".62" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="750">${escapeXml(copy.date)}</text>
  </g>
</svg>`;
}

export function buildArticleBannerSvg(
  clientId: EditableClientId,
  input: ArticleBannerInput,
  logoDataUrl: string,
  heroAssets?: ArticleHeroBrandAssets | null,
): string {
  if (clientId === "yellow") return yellowArticleHero(input, logoDataUrl);
  if (clientId === "squid") return squidArticleHero(input, logoDataUrl, heroAssets);
  if (clientId === "origintrail") return originTrailArticleHero(input, logoDataUrl);
  return babylonArticleHero(input, logoDataUrl);
}

function squidArticleInlineVisual(
  input: ArticleInlineVisualInput,
  logoDataUrl: string,
  assets: ArticleHeroBrandAssets | null | undefined,
): string {
  if (!assets?.formLanguage || !assets.squib || !assets.bubbles) {
    throw new Error("official_squid_inline_assets_required");
  }
  const brand = ARTICLE_BANNER_BRANDS.squid;
  const visual = input.visual;
  const headline = cleanText(visual.headline, 70) || "Squid 핵심 업데이트";
  const caption = cleanText(visual.caption, 200);
  const headlineLines = wrapSvgText(headline, 19, 2);
  const captionLines = wrapSvgText(caption, 48, 2);
  const points = visual.points
    .map(point => cleanText(point, 100))
    .filter(Boolean)
    .slice(0, 2);
  const topicEyebrow = squidTopicEyebrow(
    visual.eyebrow,
    headline,
    caption,
    ...points,
  );
  const headlineFontSize = headlineLines.length === 2 ? 51 : 58;
  const headlineLineHeight = headlineLines.length === 2 ? 61 : 68;
  const dividerY = 244 + (headlineLines.length - 1) * headlineLineHeight + 35;
  const captionY = dividerY + 48;
  const evidenceMarkup = points.map((point, index) => {
    const x = 60 + index * 335;
    const lines = wrapSvgText(point, 25, 2);
    return `<g id="Squid-Evidence-${index + 1}">
  <circle cx="${x + 7}" cy="514" r="7" fill="#EFFF5A" stroke="#000000" stroke-width="2"/>
  ${textLayers(
    `Squid-Evidence-${index + 1}`,
    lines,
    x + 27,
    520,
    23,
    'fill="#1C0F3D" font-family="Pretendard, sans-serif" font-size="15" font-weight="650" letter-spacing="-.1"',
  )}
</g>`;
  }).join("\n");

  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675" viewBox="0 0 1200 675" fill="none" role="img" aria-label="${escapeXml(headline)}">
  ${squidDefinitions(1200, 675)}
  <g id="Squid-Inline-Canvas" clip-path="url(#Canvas-Clip)">
    <rect id="Background" width="1200" height="675" fill="#E6CCFC"/>
    <radialGradient id="Squid-Inline-White-Haze" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(400 662) rotate(-68) scale(500 650)">
      <stop stop-color="#FFFFFF" stop-opacity=".98"/>
      <stop offset=".64" stop-color="#FFFFFF" stop-opacity=".34"/>
      <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
    </radialGradient>
    <rect width="1200" height="675" fill="url(#Squid-Inline-White-Haze)"/>
    ${squidOfficialWorldMarkup(visual.motif, assets, "inline")}
  </g>
  <g id="Header">
    ${logoMarkup("squid", brand, logoDataUrl, 60)}
    <text id="Visual-Date" x="60" y="132" fill="#3D2E5C" fill-opacity=".82" font-family="Inter, Pretendard, sans-serif" font-size="16" font-weight="650" letter-spacing=".5">${escapeXml(cleanText(input.date, 40))}</text>
  </g>
  <g id="Squid-Editorial-Copy">
    <text id="Squid-Visual-Topic" x="60" y="178" fill="#1C0F3D" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="850" letter-spacing="1.65">${escapeXml(topicEyebrow)}</text>
    ${textLayers(
      "Visual-Headline",
      headlineLines,
      60,
      244,
      headlineLineHeight,
      `fill="#000000" font-family="Bagoss Condensed, Pretendard, sans-serif" font-size="${headlineFontSize}" font-weight="900" letter-spacing="-1.9"`,
    )}
    <rect id="Squid-Lime-Divider" x="60" y="${dividerY}" width="264" height="8" fill="#EFFF5A"/>
    ${textLayers(
      "Visual-Caption",
      captionLines,
      60,
      captionY,
      28,
      'fill="#1C0F3D" fill-opacity=".84" font-family="Pretendard, sans-serif" font-size="18" font-weight="560" letter-spacing="-.15"',
    )}
  </g>
  <g id="Squid-Evidence-Lines">
    ${evidenceMarkup}
  </g>
  <g id="Footer">
    <line x1="60" y1="620" x2="1140" y2="620" stroke="#1C0F3D" stroke-opacity=".18"/>
    <text x="60" y="651" fill="#1C0F3D" fill-opacity=".68" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="700" letter-spacing=".4">공식 원문 기반 · ${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1140" y="651" text-anchor="end" fill="#1C0F3D" fill-opacity=".68" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="700">COINEASY · KOREA GTM</text>
  </g>
</svg>`;
}

export function buildArticleInlineVisualSvg(
  clientId: EditableClientId,
  input: ArticleInlineVisualInput,
  logoDataUrl: string,
  brandAssets?: ArticleHeroBrandAssets | null,
): string {
  if (clientId === "squid") {
    return squidArticleInlineVisual(input, logoDataUrl, brandAssets);
  }
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
    ${brandAtmosphereMarkup(clientId, brand, 675)}
    <path id="Accent-Rail" d="M0 0H14V675H0Z" fill="${brand.primary}"/>
    <path id="Corner-Accent" d="M1064 675H1200V539L1064 675Z" fill="${brand.primary}" fill-opacity=".9"/>
  </g>
  <g id="Header">
    ${logoMarkup(clientId, brand, logoDataUrl)}
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
    <text x="72" y="631" fill="#FFFFFF" fill-opacity=".46" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="700" letter-spacing=".7">공식 원문 기반 에디토리얼 비주얼 · ${escapeXml(sourceLabel(input.sourceUrl))}</text>
    <text x="1008" y="631" text-anchor="end" fill="#FFFFFF" fill-opacity=".46" font-family="Inter, Pretendard, sans-serif" font-size="13" font-weight="700">${escapeXml(date)}</text>
  </g>
</svg>`;
}
