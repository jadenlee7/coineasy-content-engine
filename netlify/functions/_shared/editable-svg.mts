export type EditableClientId = "yellow" | "origintrail" | "squid" | "babylon";
export type EditableTemplateStyle = "remix" | "classic" | "editorial" | "signal";
export type SquidCreativeFamily =
  | "editorial_big_type"
  | "milestone_metric"
  | "status_progress"
  | "product_proof"
  | "worldbuilding";

export const SQUID_GENERATED_EDITABLE_TEMPLATE_VERSION = "squid-generated-gtm@4";
export const SQUID_GENERATED_EDITABLE_PROFILE_ID = "squid/full-bleed-character-type";
export const SQUID_GENERATED_EDITABLE_PROFILE_VERSION = 1;

const SQUID_GENERATED_EDITABLE_FAMILIES = new Set<string>([
  "editorial_big_type",
  "milestone_metric",
  "status_progress",
  "product_proof",
]);

export function isSupportedSquidGeneratedEditableSpec(value: unknown): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const spec = value as Record<string, unknown>;
  return spec.render_strategy === "generated_gtm"
    && SQUID_GENERATED_EDITABLE_FAMILIES.has(String(spec.creative_family || ""))
    && spec.template_version === SQUID_GENERATED_EDITABLE_TEMPLATE_VERSION
    && spec.visual_design_profile_id === SQUID_GENERATED_EDITABLE_PROFILE_ID
    && spec.visual_design_profile_version === SQUID_GENERATED_EDITABLE_PROFILE_VERSION;
}

export function effectiveEditableTemplateStyle(
  clientId: EditableClientId,
  templateStyle: EditableTemplateStyle,
): EditableTemplateStyle {
  return clientId === "squid" && (templateStyle === "editorial" || templateStyle === "signal")
    ? "classic"
    : templateStyle;
}

export type EditableCardAssets = {
  logoDark?: string;
  logoLight?: string;
  sourceImage?: string;
  squidFormLanguage?: string;
  squidSquib?: string;
  squidBubbles?: string;
};

type EditableSpec = {
  label?: unknown;
  headline?: unknown;
  body_lines?: unknown;
  date?: unknown;
  source_url?: unknown;
  theme?: unknown;
  source_logo_visible?: unknown;
  source_text_visible?: unknown;
  translation_regions?: unknown;
  source_crop_bottom?: unknown;
  source_image_width?: unknown;
  source_image_height?: unknown;
  source_background_color?: unknown;
  output_width?: unknown;
  output_height?: unknown;
  output_policy?: unknown;
  render_strategy?: unknown;
  creative_family?: unknown;
  visual_metric?: unknown;
};

type NormalizedTranslationRegion = {
  sourceText: string;
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  sourceX: number;
  sourceY: number;
  sourceWidth: number;
  sourceHeight: number;
  align: "left" | "center" | "right";
  fontRole: "display" | "body";
  fontSize: number;
  scaleX: number;
  textColor: string;
  sourceLineCount: number;
};

type NormalizedSpec = {
  label: string;
  headline: string;
  bodyLines: string[];
  date: string;
  sourceUrl: string;
  theme: "dark" | "yellow";
  sourceLogoVisible: boolean;
  sourceTextVisible: boolean;
  translationRegions: NormalizedTranslationRegion[];
  sourceImageWidth: number;
  sourceImageHeight: number;
  sourceBackgroundColor: string;
  outputWidth: number;
  outputHeight: number;
  outputPolicy: "official_source_native_v1" | "legacy_square";
  renderStrategy: "source_remix" | "generated_gtm" | "";
  creativeFamily: string;
  visualMetric: string;
};

type Brand = {
  id: EditableClientId;
  name: string;
  primary: string;
  dark: string;
  accent: string;
  ink: string;
  font: string;
  displayFont: string;
};

const BRANDS: Record<EditableClientId, Brand> = {
  yellow: {
    id: "yellow",
    name: "Yellow Network",
    primary: "#FDDA16",
    dark: "#000000",
    accent: "#FDDA16",
    ink: "#19191C",
    font: "Pretendard",
    displayFont: "Pretendard",
  },
  origintrail: {
    id: "origintrail",
    name: "OriginTrail Korea",
    primary: "#6344DF",
    dark: "#0C2246",
    accent: "#6344DF",
    ink: "#0C2246",
    font: "Gmarket Sans",
    displayFont: "Gmarket Sans",
  },
  squid: {
    id: "squid",
    name: "Squid",
    primary: "#E6FA36",
    dark: "#1A0E2E",
    accent: "#BC8EE4",
    ink: "#000000",
    font: "Pretendard",
    displayFont: "Bagoss Condensed",
  },
  babylon: {
    id: "babylon",
    name: "Babylon Korea",
    primary: "#CE6533",
    dark: "#12495E",
    accent: "#F7931A",
    ink: "#12495E",
    font: "Inter",
    displayFont: "Inter",
  },
};

const SQUID_GENERATED_TOKENS = {
  lavender: "#BC8EE4",
  lavenderLight: "#E6CCFC",
  black: "#000000",
  white: "#FFFFFF",
} as const;

export function escapeXml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
  })[character] || character);
}

function cleanText(value: unknown, maxLength: number): string {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : "";
}

function boundedNumber(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
}

function normalizedColor(value: unknown, fallback: string): string {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value)
    ? value.toUpperCase()
    : fallback;
}

type PercentBox = { x: number; y: number; width: number; height: number };

function strictPercentBox(
  value: Record<string, unknown>,
  keys: [string, string, string, string],
  minimumWidth = 6,
  minimumHeight = 3,
): PercentBox | null {
  const parsed = keys.map((key) => {
    if (typeof value[key] === "boolean") return Number.NaN;
    return typeof value[key] === "number" ? value[key] : Number(value[key]);
  });
  if (parsed.some((number) => !Number.isFinite(number))) return null;
  const [x, y, width, height] = parsed;
  if (
    x < 0 || y < 0 || width < minimumWidth || height < minimumHeight
    || x + width > 100 || y + height > 100
  ) return null;
  return { x, y, width, height };
}

function normalizeSpec(spec: EditableSpec): NormalizedSpec {
  const bodyLines = Array.isArray(spec.body_lines)
    ? spec.body_lines
      .map((line) => cleanText(line, 240))
      .filter(Boolean)
      .slice(0, 3)
    : [];
  const sourceTextVisible = spec.source_text_visible === true;
  const translationRegions: NormalizedTranslationRegion[] = [];
  let invalidTranslationRegions = false;
  if (sourceTextVisible && Array.isArray(spec.translation_regions)) {
    if (spec.translation_regions.length > 4) invalidTranslationRegions = true;
    for (const rawRegion of spec.translation_regions) {
      if (invalidTranslationRegions) break;
      if (!rawRegion || typeof rawRegion !== "object" || Array.isArray(rawRegion)) {
        invalidTranslationRegions = true;
        break;
      }
      const region = rawRegion as Record<string, unknown>;
      const sourceText = cleanText(region.source_text, 240);
      const text = cleanText(region.text, 240);
      const target = strictPercentBox(region, ["x", "y", "width", "height"]);
      const source = strictPercentBox(region, ["source_x", "source_y", "source_width", "source_height"]);
      if (!target || !source) {
        invalidTranslationRegions = true;
        break;
      }
      const sameTarget = ["x", "y", "width", "height"].every((key) => (
        Math.abs(target[key as keyof PercentBox] - source[key as keyof PercentBox]) <= 0.01
      ));
      if (!sameTarget) {
        invalidTranslationRegions = true;
        break;
      }
      const candidate: NormalizedTranslationRegion = {
        sourceText,
        text,
        x: target.x,
        y: target.y,
        width: target.width,
        height: target.height,
        sourceX: source.x,
        sourceY: source.y,
        sourceWidth: source.width,
        sourceHeight: source.height,
        align: region.align === "center" || region.align === "right" ? region.align : "left",
        fontRole: region.font_role === "body" ? "body" : "display",
        fontSize: boundedNumber(region.font_size, 5.2, 2, 12),
        scaleX: boundedNumber(region.scale_x, 1, 0.85, 1.35),
        textColor: normalizedColor(region.text_color, "#FFFFFF"),
        sourceLineCount: Math.round(boundedNumber(
          region.source_line_count,
          sourceText.split(/\n+/).filter(Boolean).length || 1,
          1,
          2,
        )),
      };
      const explicitLines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
      const overlapsExisting = translationRegions.some((existing) => (
        candidate.x < existing.x + existing.width
        && candidate.x + candidate.width > existing.x
        && candidate.y < existing.y + existing.height
        && candidate.y + candidate.height > existing.y
      ));
      if (!sourceText || !text || explicitLines.length > 2 || overlapsExisting) {
        invalidTranslationRegions = true;
        break;
      }
      translationRegions.push(candidate);
    }
  }
  if (invalidTranslationRegions) translationRegions.length = 0;
  const sourceImageWidth = boundedNumber(spec.source_image_width, 1080, 1, 10_000);
  const sourceImageHeight = boundedNumber(spec.source_image_height, 1080, 1, 10_000);
  const submittedOutputWidth = Math.round(boundedNumber(spec.output_width, 1080, 1, 1_800));
  const submittedOutputHeight = Math.round(boundedNumber(spec.output_height, 1080, 1, 1_800));
  const nativeScale = Math.min(1, 1_200 / Math.max(sourceImageWidth, sourceImageHeight));
  const sourceNativeOutput = spec.output_policy === "official_source_native_v1"
    && Number.isSafeInteger(sourceImageWidth)
    && Number.isSafeInteger(sourceImageHeight)
    && sourceImageWidth <= 1_800
    && sourceImageHeight <= 1_800
    && submittedOutputWidth === Math.max(1, Math.round(sourceImageWidth * nativeScale))
    && submittedOutputHeight === Math.max(1, Math.round(sourceImageHeight * nativeScale));
  return {
    label: cleanText(spec.label, 40) || "업데이트",
    headline: cleanText(spec.headline, 280) || "새로운 소식을 전합니다",
    bodyLines,
    date: cleanText(spec.date, 24),
    sourceUrl: cleanText(spec.source_url, 2_048),
    theme: spec.theme === "yellow" ? "yellow" : "dark",
    sourceLogoVisible: spec.source_logo_visible === true,
    sourceTextVisible: translationRegions.length > 0,
    translationRegions,
    sourceImageWidth,
    sourceImageHeight,
    sourceBackgroundColor: normalizedColor(spec.source_background_color, "#1A0E2E"),
    outputWidth: sourceNativeOutput ? submittedOutputWidth : 1080,
    outputHeight: sourceNativeOutput ? submittedOutputHeight : 1080,
    outputPolicy: sourceNativeOutput
      ? "official_source_native_v1"
      : "legacy_square",
    renderStrategy: spec.render_strategy === "source_remix" || spec.render_strategy === "generated_gtm"
      ? spec.render_strategy
      : "",
    creativeFamily: cleanText(spec.creative_family, 40),
    visualMetric: cleanText(spec.visual_metric, 32),
  };
}

function characterUnits(character: string): number {
  if (/\s/.test(character)) return 0.55;
  if (/[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]/.test(character)) return 1.85;
  if (/[A-Z0-9]/.test(character)) return 1.1;
  return 0.92;
}

type WrappedSvgText = {
  lines: string[];
  truncated: boolean;
};

function wrapSvgTextDetailed(value: string, maxUnits: number, maxLines: number): WrappedSvgText {
  const lines: string[] = [];
  let current = "";
  let units = 0;
  let truncated = false;
  const words = value.trim().split(/\s+/).filter(Boolean);
  outer: for (const word of words) {
    const wordUnits = [...word].reduce((sum, character) => sum + characterUnits(character), 0);
    const spacing = current ? characterUnits(" ") : 0;
    if (current && units + spacing + wordUnits > maxUnits) {
      lines.push(current);
      current = "";
      units = 0;
      if (lines.length === maxLines) {
        truncated = true;
        break;
      }
    }
    if (wordUnits <= maxUnits) {
      if (current) {
        current += " ";
        units += characterUnits(" ");
      }
      current += word;
      units += wordUnits;
      continue;
    }
    for (const character of word) {
      const nextUnits = characterUnits(character);
      if (current && units + nextUnits > maxUnits) {
        lines.push(current);
        current = "";
        units = 0;
        if (lines.length === maxLines) {
          truncated = true;
          break outer;
        }
      }
      current += character;
      units += nextUnits;
    }
  }
  if (current.trim() && lines.length < maxLines) lines.push(current.trim());
  if (!lines.length) lines.push(value.slice(0, 1));
  if (truncated && lines.length) {
    const last = lines.length - 1;
    lines[last] = `${lines[last].replace(/[.…]+$/, "")}…`;
  }
  return { lines, truncated };
}

export function wrapSvgText(value: string, maxUnits: number, maxLines: number): string[] {
  return wrapSvgTextDetailed(value, maxUnits, maxLines).lines;
}

type FittedSvgText = {
  lines: string[];
  fontSize: number;
  lineHeight: number;
  truncated: boolean;
};

export function fitSvgText(
  value: string,
  options: {
    maxWidth: number;
    maxLines: number;
    maxFontSize: number;
    minFontSize: number;
    lineHeightRatio: number;
  },
): FittedSvgText {
  const maximum = Math.max(options.maxFontSize, options.minFontSize);
  const minimum = Math.min(options.maxFontSize, options.minFontSize);
  let fallback: FittedSvgText | null = null;
  for (let fontSize = maximum; fontSize >= minimum; fontSize -= 2) {
    // characterUnits() models a Hangul glyph as 1.85 units. Reserving a small
    // safety margin here maps those units back to real SVG em width without
    // letting negative tracking or a trailing ellipsis touch the canvas edge.
    const maxUnits = options.maxWidth * 1.75 / fontSize;
    const wrapped = wrapSvgTextDetailed(value, maxUnits, options.maxLines);
    const candidate = {
      lines: wrapped.lines,
      fontSize,
      lineHeight: Math.round(fontSize * options.lineHeightRatio),
      truncated: wrapped.truncated,
    };
    fallback = candidate;
    if (!wrapped.truncated) return candidate;
  }
  return fallback || {
    lines: [value.slice(0, 1)],
    fontSize: minimum,
    lineHeight: Math.round(minimum * options.lineHeightRatio),
    truncated: value.length > 1,
  };
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

function imageLayer(id: string, href: string | undefined, x: number, y: number, width: number, height: number): string {
  if (!href) return "";
  return `<image id="${id}" x="${x}" y="${y}" width="${width}" height="${height}" href="${escapeXml(href)}" preserveAspectRatio="xMidYMid meet"/>`;
}

type LogoSlot = "classic" | "editorial" | "signal" | "remix";
type LogoBox = { width: number; height: number };

const OFFICIAL_LOGO_BOXES: Record<LogoSlot, Record<EditableClientId, LogoBox>> = {
  classic: {
    yellow: { width: 118, height: 44 },
    origintrail: { width: 164, height: 71 },
    squid: { width: 88, height: 50 },
    babylon: { width: 48, height: 48 },
  },
  editorial: {
    yellow: { width: 150, height: 50 },
    origintrail: { width: 196, height: 84 },
    squid: { width: 108, height: 61 },
    babylon: { width: 56, height: 56 },
  },
  signal: {
    yellow: { width: 142, height: 46 },
    origintrail: { width: 196, height: 84 },
    squid: { width: 110, height: 62 },
    babylon: { width: 56, height: 56 },
  },
  remix: {
    yellow: { width: 160, height: 49 },
    origintrail: { width: 151, height: 65 },
    squid: { width: 110, height: 62 },
    babylon: { width: 54, height: 54 },
  },
};

function logoLayer(
  brand: Brand,
  href: string | undefined,
  right: number,
  centerY: number,
  slot: LogoSlot,
  color: string,
): string {
  const box = OFFICIAL_LOGO_BOXES[slot][brand.id];
  const x = right - box.width;
  const y = centerY - box.height / 2;
  if (href) return imageLayer("Brand-Logo", href, x, y, box.width, box.height);
  return `<text id="Brand-Logo-Fallback" x="${right}" y="${centerY + 8}" text-anchor="end" fill="${color}" font-family="${escapeXml(brand.font)}, sans-serif" font-size="24" font-weight="800">${escapeXml(brand.name)}</text>`;
}

function logoChipLayer(
  brand: Brand,
  href: string | undefined,
  right = 1036,
  centerY = 753,
): string {
  const box = OFFICIAL_LOGO_BOXES.remix[brand.id];
  const paddingX = 10;
  const paddingY = 7;
  const chipWidth = box.width + paddingX * 2;
  const chipHeight = box.height + paddingY * 2;
  const x = right - chipWidth;
  const y = centerY - chipHeight / 2;
  return `<g id="Official-Logo-Safe-Area">
  <rect id="Logo-Chip" x="${x}" y="${y}" width="${chipWidth}" height="${chipHeight}" rx="14" fill="#FFFFFF" fill-opacity="0.05" stroke="#FFFFFF" stroke-opacity="0.18"/>
  ${logoLayer(brand, href, right - paddingX, centerY, "remix", "#FFFFFF")}
</g>`;
}

function footer(spec: NormalizedSpec, y: number, color: string, x = 96, maxSourceLength = 86): string {
  const source = spec.sourceUrl.length > maxSourceLength ? `${spec.sourceUrl.slice(0, maxSourceLength - 1)}…` : spec.sourceUrl;
  return `<g id="Footer">
    <line id="Footer-Divider" x1="${x}" y1="${y - 30}" x2="${1080 - x}" y2="${y - 30}" stroke="${color}" stroke-opacity="0.22"/>
    <text id="Date" x="${x}" y="${y}" fill="${color}" fill-opacity="0.58" font-family="Pretendard, sans-serif" font-size="16" font-weight="600">${escapeXml(spec.date)}</text>
    <text id="Source-URL" x="${1080 - x}" y="${y}" text-anchor="end" fill="${color}" fill-opacity="0.58" font-family="Pretendard, sans-serif" font-size="16" font-weight="500">${escapeXml(source.replace(/^https?:\/\//, ""))}</text>
  </g>`;
}

function classicSvg(brand: Brand, spec: NormalizedSpec, assets: EditableCardAssets): string {
  const isYellow = spec.theme === "yellow";
  const background = isYellow ? brand.accent : brand.dark;
  const logo = isYellow ? assets.logoLight : assets.logoDark;
  const headlineLines = wrapSvgText(spec.headline, 34, 3);
  const bodyStart = Math.max(460, 258 + headlineLines.length * 62 + 38);
  const body = spec.bodyLines.map((line, index) => {
    const y = bodyStart + index * 96;
    const lines = wrapSvgText(line, 65, 2);
    return `<g id="Body-Item-${index + 1}">
      <rect id="Body-Item-${index + 1}-Background" x="96" y="${y}" width="888" height="82" rx="14" fill="#F2F2F2"/>
      <circle id="Body-Item-${index + 1}-Bullet" cx="126" cy="${y + 41}" r="5" fill="${brand.ink}"/>
      ${textLayers(`Body-Item-${index + 1}-Text`, lines, 150, y + (lines.length === 1 ? 50 : 34), 29, `fill="${brand.ink}" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="22" font-weight="650"`)}
    </g>`;
  }).join("\n");
  return `<rect id="Canvas-Background" width="1080" height="1080" fill="${background}"/>
  <g id="Header">${logoLayer(brand, logo, 1032, 70, "classic", isYellow ? brand.ink : "#FFFFFF")}</g>
  <g id="Main-Card">
    <rect id="Main-Card-Background" x="48" y="116" width="984" height="916" rx="20" fill="#FFFFFF"/>
    <g id="Label"><rect id="Label-Background" x="96" y="172" width="150" height="48" rx="8" fill="${isYellow ? brand.dark : brand.primary}"/><text id="Label-Text" x="171" y="203" text-anchor="middle" fill="${isYellow ? brand.primary : brand.ink}" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="18" font-weight="750">${escapeXml(spec.label)}</text></g>
    <g id="Headline">${textLayers("Headline", headlineLines, 96, 278, 62, `fill="${brand.ink}" font-family="${escapeXml(brand.displayFont)}, ${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="52" font-weight="800"`)}</g>
    <g id="Body">${body}</g>
    ${footer(spec, 986, "#70747B")}
  </g>`;
}

function compactSourceLabel(value: string): string {
  try {
    const url = new URL(value);
    const account = url.pathname.split("/").filter(Boolean)[0];
    return `${url.hostname.replace(/^www\./, "").toUpperCase()}${account ? ` / ${account.toUpperCase()}` : ""}`;
  } catch {
    return "OFFICIAL SOURCE";
  }
}

function squidClassicSvg(
  brand: Brand,
  spec: NormalizedSpec,
  assets: EditableCardAssets,
): string {
  if (
    !assets.logoLight
    || !assets.squidFormLanguage
    || !assets.squidSquib
    || !assets.squidBubbles
  ) {
    throw new Error("official_squid_classic_assets_required");
  }
  const headlineLayout = fitSvgText(spec.headline, {
    maxWidth: 960,
    maxLines: 2,
    maxFontSize: 94,
    minFontSize: 64,
    lineHeightRatio: 1.04,
  });
  const headlineLines = headlineLayout.lines;
  const bodyLines = spec.bodyLines.slice(0, 2).flatMap((line) => (
    wrapSvgText(line, 43, 1)
  ));
  const body = bodyLines.map((line, index) => (
    `<text id="Body-Line-${index + 1}" x="60" y="${834 + index * 42}" fill="#1C0F3D" font-family="Pretendard, sans-serif" font-size="32" font-weight="560" letter-spacing="-1">${escapeXml(line)}</text>`
  )).join("\n");
  return `<defs>
    <radialGradient id="Squid-Background-Glow" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(390 1015) rotate(-88) scale(500 560)">
      <stop stop-color="#FFFFFF" stop-opacity=".76"/>
      <stop offset=".5" stop-color="#FFFFFF" stop-opacity=".24"/>
      <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <g id="Squid-Figma-Daily-News">
    <rect id="Canvas-Background" width="1080" height="1080" fill="#E6CCFC"/>
    <rect id="Canvas-Glow" width="1080" height="1080" fill="url(#Squid-Background-Glow)"/>
    <image id="Squid-Official-Bubbles" x="652" y="8" width="420" height="420" href="${escapeXml(assets.squidBubbles)}" preserveAspectRatio="xMidYMid meet" opacity=".46"/>
    <image id="Squid-Official-SQUIB" x="430" y="-116" width="720" height="720" href="${escapeXml(assets.squidSquib)}" preserveAspectRatio="xMidYMid meet"/>
  </g>
  <g id="Header">
    <image id="Brand-Logo" x="60" y="44" width="148" height="59" href="${escapeXml(assets.logoLight)}" preserveAspectRatio="xMinYMid meet"/>
  </g>
  <g id="Story">
    <text id="Eyebrow" x="60" y="464" fill="#1C0F3D" fill-opacity=".74" font-family="Inter, Pretendard, sans-serif" font-size="22" font-weight="800" letter-spacing="1.8">${escapeXml(spec.label.toUpperCase())}</text>
    ${textLayers("Headline", headlineLines, 60, 565, headlineLayout.lineHeight, `fill="#000000" font-family="${escapeXml(brand.displayFont)}, Pretendard, sans-serif" font-size="${headlineLayout.fontSize}" font-weight="900" letter-spacing="-3.2"`)}
  </g>
  <g id="Support">
    <rect id="Lime-Divider" x="60" y="770" width="320" height="8" rx="4" fill="#EFFF5A"/>
    ${body}
  </g>
  <g id="Footer">
    <text id="CoinEasy" x="60" y="1038" fill="#1C0F3D" fill-opacity=".56" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="750" letter-spacing="1.2">COINEASY / KOREA</text>
    <text id="Source-URL" x="260" y="1038" fill="#1C0F3D" fill-opacity=".62" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="650">${escapeXml(compactSourceLabel(spec.sourceUrl))}</text>
    <text id="Date" x="1020" y="1038" text-anchor="end" fill="#1C0F3D" fill-opacity=".56" font-family="Inter, Pretendard, sans-serif" font-size="15" font-weight="650">${escapeXml(spec.date)}</text>
  </g>`;
}

type SquidGeneratedFamily = Exclude<SquidCreativeFamily, "worldbuilding">;

type SquidGeneratedAssets = Required<Pick<
  EditableCardAssets,
  "squidFormLanguage" | "squidSquib" | "squidBubbles"
>> & {
  family: SquidGeneratedFamily;
};

const SQUID_GENERATED_ROOT_IDS: Record<SquidGeneratedFamily, string> = {
  editorial_big_type: "Squid-Generated-Editorial-Big-Type",
  milestone_metric: "Squid-Generated-Milestone-Metric",
  status_progress: "Squid-Generated-Status-Progress",
  product_proof: "Squid-Generated-Product-Proof",
};

function requiredSquidGeneratedAssets(
  assets: EditableCardAssets,
  family: SquidGeneratedFamily,
): SquidGeneratedAssets {
  if (
    !assets.squidFormLanguage
    || !assets.squidSquib
    || !assets.squidBubbles
  ) {
    throw new Error(`official_squid_generated_assets_required:${family}`);
  }
  return {
    family,
    squidFormLanguage: assets.squidFormLanguage,
    squidSquib: assets.squidSquib,
    squidBubbles: assets.squidBubbles,
  };
}

function squidGeneratedStageSvg(
  brand: Brand,
  spec: NormalizedSpec,
  assets: SquidGeneratedAssets,
): string {
  const hasMetric = assets.family === "milestone_metric" && Boolean(spec.visualMetric);
  const maximumHeadlineSize = hasMetric ? 160 : 168;
  const headline = fitSvgText(spec.headline, {
    // The v4 composition treats the Korean headline as the lower display
    // object. Its tight leading and overlap with SQUIB are intentional.
    // This is a virtual measurement width calibrated against the HTML
    // renderer's condensed display face and negative tracking. It keeps the
    // common two-line composition at 126px while the 24-character QA boundary
    // still fits without an ellipsis.
    maxWidth: 1200,
    maxLines: 2,
    maxFontSize: maximumHeadlineSize,
    minFontSize: 74,
    lineHeightRatio: .82,
  });
  const metric = hasMetric
    ? fitSvgText(spec.visualMetric, {
      maxWidth: 440,
      maxLines: 1,
      maxFontSize: 184,
      minFontSize: 104,
      lineHeightRatio: .83,
    })
    : null;
  const headlineX = headline.fontSize >= 150 ? 120 : 68;
  const headlineLineHeight = headline.fontSize >= 150
    ? Math.round(headline.fontSize * 1.04)
    : headline.lineHeight;
  return `<defs>
    <linearGradient id="Squid-Base-Lavender" x1="-26.75" y1="29.70" x2="1106.75" y2="1050.30" gradientUnits="userSpaceOnUse">
      <stop stop-color="#C99AF0"/>
      <stop offset=".54" stop-color="${SQUID_GENERATED_TOKENS.lavender}"/>
      <stop offset="1" stop-color="${SQUID_GENERATED_TOKENS.lavenderLight}"/>
    </linearGradient>
    <radialGradient id="Squid-Full-Bleed-Lavender-Halo" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(756 421.2) rotate(90) scale(1069.15 931.68)">
      <stop stop-color="${SQUID_GENERATED_TOKENS.white}" stop-opacity=".98"/>
      <stop offset=".18" stop-color="${SQUID_GENERATED_TOKENS.white}" stop-opacity=".76"/>
      <stop offset=".5" stop-color="${SQUID_GENERATED_TOKENS.white}" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="Squid-Lower-White-Bloom" cx="0" cy="0" r="1" gradientUnits="userSpaceOnUse" gradientTransform="translate(378 928.8) rotate(90) scale(1092.06 1549.95)">
      <stop stop-color="${SQUID_GENERATED_TOKENS.white}" stop-opacity=".9"/>
      <stop offset=".29" stop-color="${SQUID_GENERATED_TOKENS.lavenderLight}" stop-opacity=".42"/>
      <stop offset=".66" stop-color="${SQUID_GENERATED_TOKENS.lavender}" stop-opacity="0"/>
    </radialGradient>
    <filter id="Squid-SQUIB-Shadow" x="-20%" y="-20%" width="140%" height="150%" color-interpolation-filters="sRGB">
      <feDropShadow dx="0" dy="26" stdDeviation="15" flood-color="#573F6F" flood-opacity=".2"/>
    </filter>
  </defs>
  <g id="${SQUID_GENERATED_ROOT_IDS[assets.family]}" data-creative-family="${assets.family}">
    <rect id="Canvas-Background" width="1080" height="1080" fill="url(#Squid-Base-Lavender)"/>
    <rect id="Lower-White-Bloom" width="1080" height="1080" fill="url(#Squid-Lower-White-Bloom)"/>
    <rect id="Full-Bleed-Lavender-Halo" width="1080" height="1080" fill="url(#Squid-Full-Bleed-Lavender-Halo)"/>
    <image id="Squid-Official-Form-Language" x="-340" y="-360" width="1760" height="1760" href="${escapeXml(assets.squidFormLanguage)}" preserveAspectRatio="xMidYMid meet" opacity=".24" transform="rotate(74 540 520)"/>
    <image id="Squid-Official-Bubbles" x="690" y="220" width="520" height="520" href="${escapeXml(assets.squidBubbles)}" preserveAspectRatio="xMidYMid meet" opacity=".88"/>
    <g id="Oversized-Brand-Type" aria-hidden="true">
      <text id="Stage-Word-Top" x="170" y="206" transform="translate(170 0) scale(1.48 1) translate(-170 0)" fill="${SQUID_GENERATED_TOKENS.black}" font-family="${escapeXml(brand.displayFont)}, Pretendard, sans-serif" font-size="220" font-weight="900" letter-spacing="-10">Squid</text>
    </g>
    <image id="Squid-Official-SQUIB" x="-110" y="-40" width="1200" height="1200" href="${escapeXml(assets.squidSquib)}" preserveAspectRatio="xMidYMid meet" filter="url(#Squid-SQUIB-Shadow)"/>
    <g id="Story">
      ${metric ? `<g id="Metric">${textLayers("Metric", metric.lines, 1020, 360, metric.lineHeight, `text-anchor="end" fill="${SQUID_GENERATED_TOKENS.black}" font-family="${escapeXml(brand.displayFont)}, Pretendard, sans-serif" font-size="${metric.fontSize}" font-weight="900" letter-spacing="-8"`)}</g>` : ""}
      <g id="Headline">${textLayers("Headline", headline.lines, headlineX, 704 + Math.round(headline.fontSize * .76), headlineLineHeight, `fill="${SQUID_GENERATED_TOKENS.black}" font-family="${escapeXml(brand.displayFont)}, Pretendard, sans-serif" font-size="${headline.fontSize}" font-weight="900" letter-spacing="-5.2"`)}</g>
    </g>
  </g>`;
}

function squidEditorialBigTypeSvg(
  brand: Brand,
  spec: NormalizedSpec,
  assets: SquidGeneratedAssets,
): string {
  return squidGeneratedStageSvg(brand, spec, assets);
}

function squidMilestoneMetricSvg(
  brand: Brand,
  spec: NormalizedSpec,
  assets: SquidGeneratedAssets,
): string {
  return squidGeneratedStageSvg(brand, spec, assets);
}

function squidStatusProgressSvg(
  brand: Brand,
  spec: NormalizedSpec,
  assets: SquidGeneratedAssets,
): string {
  return squidGeneratedStageSvg(brand, spec, assets);
}

function squidProductProofSvg(
  brand: Brand,
  spec: NormalizedSpec,
  assets: SquidGeneratedAssets,
): string {
  return squidGeneratedStageSvg(brand, spec, assets);
}

function squidGeneratedSvg(
  brand: Brand,
  spec: NormalizedSpec,
  assets: EditableCardAssets,
): string {
  if (spec.creativeFamily === "worldbuilding") {
    throw new Error("approved_squid_worldbuilding_assets_required");
  }
  const family = spec.creativeFamily as Exclude<SquidCreativeFamily, "worldbuilding">;
  const officialAssets = requiredSquidGeneratedAssets(assets, family);
  switch (officialAssets.family) {
    case "editorial_big_type":
      return squidEditorialBigTypeSvg(brand, spec, officialAssets);
    case "milestone_metric":
      return squidMilestoneMetricSvg(brand, spec, officialAssets);
    case "status_progress":
      return squidStatusProgressSvg(brand, spec, officialAssets);
    case "product_proof":
      return squidProductProofSvg(brand, spec, officialAssets);
    default:
      throw new Error("invalid_squid_creative_family");
  }
}

function editorialSvg(brand: Brand, spec: NormalizedSpec, assets: EditableCardAssets): string {
  const isYellow = spec.theme === "yellow";
  const background = isYellow ? brand.accent : brand.dark;
  const foreground = isYellow ? brand.ink : "#FFFFFF";
  const logo = isYellow ? assets.logoLight : assets.logoDark;
  const headlineLines = wrapSvgText(spec.headline, 27, 4);
  const body = spec.bodyLines.map((line, index) => {
    const x = 64 + index * 317;
    const lines = wrapSvgText(line, 22, 4);
    return `<g id="Body-Item-${index + 1}">
      <rect id="Body-Item-${index + 1}-Background" x="${x}" y="755" width="301" height="176" rx="18" fill="${foreground}" fill-opacity="0.08" stroke="${foreground}" stroke-opacity="0.18"/>
      <text id="Body-Item-${index + 1}-Number" x="${x + 22}" y="790" fill="${foreground}" fill-opacity="0.48" font-family="Inter, sans-serif" font-size="14" font-weight="800">0${index + 1}</text>
      ${textLayers(`Body-Item-${index + 1}-Text`, lines, x + 22, 827, 30, `fill="${foreground}" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="20" font-weight="650"`)}
    </g>`;
  }).join("\n");
  return `<rect id="Canvas-Background" width="1080" height="1080" fill="${background}"/>
  <g id="Header">
    ${logoLayer(brand, logo, 1016, 87, "editorial", foreground)}
    <line id="Header-Divider" x1="64" y1="158" x2="1016" y2="158" stroke="${foreground}" stroke-opacity="0.2" stroke-width="2"/>
  </g>
  <g id="Label"><rect id="Label-Background" x="64" y="208" width="148" height="48" rx="24" fill="${isYellow ? brand.dark : brand.primary}"/><text id="Label-Text" x="138" y="239" text-anchor="middle" fill="${isYellow ? "#FFFFFF" : brand.ink}" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="18" font-weight="800">${escapeXml(spec.label)}</text></g>
  <g id="Headline">${textLayers("Headline", headlineLines, 64, 344, 78, `fill="${foreground}" font-family="${escapeXml(brand.displayFont)}, ${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="72" font-weight="800"`)}</g>
  <g id="Body">${body}</g>
  ${footer(spec, 1012, foreground, 64, 72)}`;
}

function signalSvg(brand: Brand, spec: NormalizedSpec, assets: EditableCardAssets): string {
  const headlineLines = wrapSvgText(spec.headline, 29, 3);
  const bodyStart = Math.max(470, 306 + headlineLines.length * 68 + 28);
  const body = spec.bodyLines.map((line, index) => {
    const y = bodyStart + index * 84;
    const lines = wrapSvgText(line, 60, 2);
    return `<g id="Body-Item-${index + 1}">
      <rect id="Body-Item-${index + 1}-Background" x="124" y="${y}" width="850" height="72" fill="#F3F4F2"/>
      <rect id="Body-Item-${index + 1}-Accent" x="124" y="${y}" width="4" height="72" fill="${brand.primary}"/>
      <circle id="Body-Item-${index + 1}-Number-Background" cx="158" cy="${y + 36}" r="14" fill="${brand.accent}"/>
      <text id="Body-Item-${index + 1}-Number" x="158" y="${y + 41}" text-anchor="middle" fill="${brand.ink}" font-family="Inter, sans-serif" font-size="13" font-weight="800">${index + 1}</text>
      ${textLayers(`Body-Item-${index + 1}-Text`, lines, 190, y + (lines.length === 1 ? 45 : 29), 28, `fill="${brand.ink}" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="21" font-weight="650"`)}
    </g>`;
  }).join("\n");
  return `<rect id="Canvas-Background" width="1080" height="1080" fill="#ECEEEB"/>
  <g id="Card-Frame"><rect id="Card-Background" x="46" y="46" width="988" height="988" rx="28" fill="#FFFFFF"/><rect id="Brand-Rail" x="46" y="46" width="28" height="988" rx="14" fill="${brand.primary}"/></g>
  <g id="Header"><path id="Header-Background" d="M74 46H1006C1021.46 46 1034 58.54 1034 74V196H74V46Z" fill="${brand.dark}"/>${logoLayer(brand, assets.logoDark, 978, 121, "signal", "#FFFFFF")}</g>
  <g id="Content">
    <g id="Label"><rect id="Label-Background" x="124" y="258" width="148" height="42" rx="8" fill="${brand.primary}"/><text id="Label-Text" x="198" y="285" text-anchor="middle" fill="${brand.ink}" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="17" font-weight="800">${escapeXml(spec.label)}</text></g>
    <text id="Signal-Number" x="974" y="298" text-anchor="end" fill="${brand.primary}" font-family="${escapeXml(brand.displayFont)}, sans-serif" font-size="52" font-weight="800">01</text>
    <g id="Headline">${textLayers("Headline", headlineLines, 124, 374, 68, `fill="${brand.ink}" font-family="${escapeXml(brand.displayFont)}, ${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="60" font-weight="800"`)}</g>
    <g id="Body">${body}</g>
    ${footer(spec, 984, "#6F746D", 124, 72)}
  </g>`;
}

function remixSvg(brand: Brand, spec: NormalizedSpec, assets: EditableCardAssets): string {
  const headlineLines = wrapSvgText(spec.headline, 43, 2);
  const bodyLines = spec.bodyLines.slice(0, 2);
  const body = bodyLines.map((line, index) => {
    const x = 44 + index * 496;
    const lines = wrapSvgText(line, 39, 2);
    return `<g id="Insight-${index + 1}"><circle id="Insight-${index + 1}-Bullet" cx="${x + 4}" cy="951" r="4" fill="${brand.primary}"/>${textLayers(`Insight-${index + 1}-Text`, lines, x + 18, 957, 26, `fill="#FFFFFF" fill-opacity="0.78" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="18" font-weight="600"`)}</g>`;
  }).join("\n");
  const visual = assets.sourceImage
    ? `<rect id="Source-Visual-Background" x="0" y="0" width="1080" height="710" fill="#111820"/>${imageLayer("Source-Visual", assets.sourceImage, 0, 0, 1080, 710)}<rect id="Source-Visual-Shade" x="0" y="0" width="1080" height="710" fill="#05080C" fill-opacity="0.18"/>`
    : `<rect id="Source-Visual-Placeholder" x="0" y="0" width="1080" height="710" fill="#17242B"/>`;
  const panelLogo = spec.sourceLogoVisible
    ? ""
    : logoChipLayer(brand, assets.logoDark);
  return `<rect id="Canvas-Background" width="1080" height="1080" fill="${brand.dark}"/>
  <g id="Source-Visual-Layer">${visual}</g>
  <g id="Localized-Content-Panel"><rect id="Panel-Background" x="0" y="710" width="1080" height="370" fill="${brand.dark}"/><rect id="Panel-Accent" x="44" y="710" width="992" height="5" rx="2.5" fill="${brand.primary}"/>
    <g id="Label"><rect id="Label-Background" x="44" y="738" width="148" height="38" rx="8" fill="${brand.primary}"/><text id="Label-Text" x="118" y="763" text-anchor="middle" fill="${brand.ink}" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="16" font-weight="800">${escapeXml(spec.label)}</text></g>
    ${panelLogo}
    <g id="Headline">${textLayers("Headline", headlineLines, 44, 839, 52, `fill="#FFFFFF" font-family="${escapeXml(brand.displayFont)}, ${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="45" font-weight="800"`)}</g>
    <g id="Insights">${body}</g>
    ${footer(spec, 1054, "#FFFFFF", 44, 74)}
  </g>`;
}

function squidTranslationSvg(brand: Brand, spec: NormalizedSpec, assets: EditableCardAssets): string {
  const sourceRatio = spec.sourceImageWidth / spec.sourceImageHeight;
  const canvasRatio = spec.outputWidth / spec.outputHeight;
  const frame = spec.outputPolicy === "official_source_native_v1"
    ? { x: 0, y: 0, width: spec.outputWidth, height: spec.outputHeight }
    : sourceRatio >= canvasRatio
    ? {
      x: 0,
      y: (spec.outputHeight - spec.outputWidth / sourceRatio) / 2,
      width: spec.outputWidth,
      height: spec.outputWidth / sourceRatio,
    }
    : {
      x: (spec.outputWidth - spec.outputHeight * sourceRatio) / 2,
      y: 0,
      width: spec.outputHeight * sourceRatio,
      height: spec.outputHeight,
    };
  const localized = Boolean(assets.sourceImage) && spec.sourceTextVisible && spec.translationRegions.length > 0;
  const visual = assets.sourceImage
    ? imageLayer("Source-Visual", assets.sourceImage, frame.x, frame.y, frame.width, frame.height)
    : `<rect id="Source-Visual-Placeholder" x="0" y="0" width="${spec.outputWidth}" height="${spec.outputHeight}" fill="${brand.dark}"/>`;
  const replacementLayers = localized
    ? spec.translationRegions.map((region, index) => {
      const x = frame.x + frame.width * region.x / 100;
      const y = frame.y + frame.height * region.y / 100;
      const width = frame.width * region.width / 100;
      const height = frame.height * region.height / 100;
      // The browser template emits the initial CSS size at two decimals. Start
      // from that same value so its 0.92 fit loop and this SVG fit loop share
      // identical font-size math.
      let fontSize = Number((frame.width * region.fontSize / 100 * 0.72).toFixed(2));
      const minFontSize = Math.max(14, frame.width * 0.02);
      let lineHeight = fontSize * 1.02;
      const paragraphs = region.text.split(/\n+/).map((value) => value.trim()).filter(Boolean);
      if (
        paragraphs.length < 1 || paragraphs.length > 2
        || paragraphs.length > region.sourceLineCount
      ) return null;
      const lines = paragraphs;
      const renderedWidth = () => Math.max(...lines.map((line) => (
        [...line].reduce((sum, character) => sum + characterUnits(character), 0)
        * fontSize * region.scaleX / 2.0
      )));
      while (true) {
        if ((renderedWidth() <= width && lines.length * lineHeight <= height) || fontSize <= minFontSize) break;
        fontSize = Math.max(minFontSize, fontSize * 0.92);
        lineHeight = fontSize * 1.02;
      }
      const fits = renderedWidth() <= width && lines.length * lineHeight <= height;
      if (!fits) return null;
      const blockHeight = lines.length * lineHeight;
      // SVG uses an explicit text baseline rather than the browser's flex line
      // box. Match the browser span's +0.22em transparent-caption offset.
      const firstBaseline = Number((
        y + Math.max(fontSize, (height - blockHeight) / 2 + fontSize)
        + fontSize * 0.22
      ).toFixed(2));
      const textX = region.align === "center" ? x + width / 2 : region.align === "right" ? x + width : x;
      const textAnchor = region.align === "center" ? "middle" : region.align === "right" ? "end" : "start";
      const font = region.fontRole === "display" ? brand.displayFont : brand.font;
      const regionId = `Korean-Translation-Region-${index + 1}`;
      const horizontalTransform = `translate(${textX.toFixed(2)} 0) scale(${region.scaleX.toFixed(2)} 1) translate(${(-textX).toFixed(2)} 0)`;
      const translation = `<g id="${regionId}">${textLayers(
        `${regionId}-Text`,
        lines,
        textX,
        firstBaseline,
        lineHeight,
        `transform="${horizontalTransform}" text-anchor="${textAnchor}" fill="${region.textColor}" stroke="#100D16" stroke-opacity="0.76" stroke-width="1" stroke-linejoin="round" paint-order="stroke fill" font-family="${escapeXml(font)}, ${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="${fontSize.toFixed(2)}" font-weight="800" letter-spacing="-${(fontSize * 0.035).toFixed(2)}"`,
      )}</g>`;
      return translation;
    })
    : [];
  const completeReplacement = replacementLayers.length > 0 && replacementLayers.every(Boolean);
  const translations = completeReplacement
    ? replacementLayers.join("\n")
    : "";
  return `<rect id="Canvas-Background" width="${spec.outputWidth}" height="${spec.outputHeight}" fill="${spec.sourceBackgroundColor}"/>
  <g id="Source-Visual-Layer">${visual}</g>
  <g id="Korean-Translation-Layer">${translations}</g>`;
}

export function buildEditableSvg(
  clientId: EditableClientId,
  templateStyle: EditableTemplateStyle,
  rawSpec: EditableSpec,
  assets: EditableCardAssets = {},
): string {
  const brand = BRANDS[clientId];
  const spec = normalizeSpec(rawSpec);
  const effectiveTemplateStyle = effectiveEditableTemplateStyle(clientId, templateStyle);
  const sourceNativeSquid = clientId === "squid" && effectiveTemplateStyle === "remix";
  const requestedGeneratedSquid = clientId === "squid"
    && effectiveTemplateStyle === "classic"
    && spec.renderStrategy === "generated_gtm";
  const generatedSquidFamily = requestedGeneratedSquid
    && isSupportedSquidGeneratedEditableSpec(rawSpec);
  if (requestedGeneratedSquid && !generatedSquidFamily) {
    throw new Error("unsupported_squid_generated_profile");
  }
  const canvasWidth = sourceNativeSquid ? spec.outputWidth : 1080;
  const canvasHeight = sourceNativeSquid ? spec.outputHeight : 1080;
  const content = generatedSquidFamily
    ? squidGeneratedSvg(brand, spec, assets)
    : effectiveTemplateStyle === "editorial"
    ? editorialSvg(brand, spec, assets)
    : effectiveTemplateStyle === "signal"
      ? signalSvg(brand, spec, assets)
      : effectiveTemplateStyle === "remix"
        ? clientId === "squid"
          ? squidTranslationSvg(brand, spec, assets)
          : remixSvg(brand, spec, assets)
        : clientId === "squid"
          ? squidClassicSvg(brand, spec, assets)
          : classicSvg(brand, spec, assets);
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="${canvasWidth}" height="${canvasHeight}" viewBox="0 0 ${canvasWidth} ${canvasHeight}" role="img" aria-labelledby="Title Description">
  <title id="Title">${escapeXml(brand.name)} editable Korean news card</title>
  <desc id="Description">Figma-editable localized news card. Text, shapes, official logo, and source image are separate named layers.</desc>
  <metadata>Localized News Card · Figma Editable · ${effectiveTemplateStyle}</metadata>
  ${content}
</svg>`;
}
