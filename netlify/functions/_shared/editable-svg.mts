export type EditableClientId = "yellow" | "origintrail" | "squid" | "babylon";
export type EditableTemplateStyle = "remix" | "classic" | "editorial" | "signal";

export type EditableCardAssets = {
  logoDark?: string;
  logoLight?: string;
  sourceImage?: string;
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
};

type NormalizedTranslationRegion = {
  text: string;
  x: number;
  y: number;
  width: number;
  height: number;
  align: "left" | "center" | "right";
  fontRole: "display" | "body";
  fontSize: number;
  scaleX: number;
  textColor: string;
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
};

type Brand = {
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
    name: "Yellow Network",
    primary: "#FDDA16",
    dark: "#000000",
    accent: "#FDDA16",
    ink: "#19191C",
    font: "Pretendard",
    displayFont: "Pretendard",
  },
  origintrail: {
    name: "OriginTrail Korea",
    primary: "#6344DF",
    dark: "#0C2246",
    accent: "#6344DF",
    ink: "#0C2246",
    font: "Gmarket Sans",
    displayFont: "Gmarket Sans",
  },
  squid: {
    name: "Squid",
    primary: "#E6FA36",
    dark: "#1A0E2E",
    accent: "#BC8EE4",
    ink: "#000000",
    font: "Pretendard",
    displayFont: "Bagoss Condensed",
  },
  babylon: {
    name: "Babylon Korea",
    primary: "#CE6533",
    dark: "#12495E",
    accent: "#F7931A",
    ink: "#12495E",
    font: "Inter",
    displayFont: "Inter",
  },
};

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
      const text = cleanText(region.text, 240);
      const x = boundedNumber(region.x, 8, 0, 99);
      const y = boundedNumber(region.y, 8, 0, 99);
      const candidate: NormalizedTranslationRegion = {
        text,
        x,
        y,
        width: boundedNumber(region.width, 84, 1, 100 - x),
        height: boundedNumber(region.height, 20, 1, 100 - y),
        align: region.align === "center" || region.align === "right" ? region.align : "left",
        fontRole: region.font_role === "body" ? "body" : "display",
        fontSize: boundedNumber(region.font_size, 5.2, 2, 12),
        scaleX: boundedNumber(region.scale_x, 1, 0.85, 1.35),
        textColor: normalizedColor(region.text_color, "#FFFFFF"),
      };
      const explicitLines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
      const overlapsExisting = translationRegions.some((existing) => (
        candidate.x < existing.x + existing.width
        && candidate.x + candidate.width > existing.x
        && candidate.y < existing.y + existing.height
        && candidate.y + candidate.height > existing.y
      ));
      if (!text || explicitLines.length > 2 || candidate.width < 24 || candidate.height < 12 || overlapsExisting) {
        invalidTranslationRegions = true;
        break;
      }
      translationRegions.push(candidate);
    }
  }
  if (invalidTranslationRegions) translationRegions.length = 0;
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
    sourceImageWidth: boundedNumber(spec.source_image_width, 1080, 1, 10_000),
    sourceImageHeight: boundedNumber(spec.source_image_height, 1080, 1, 10_000),
  };
}

function characterUnits(character: string): number {
  if (/\s/.test(character)) return 0.55;
  if (/[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]/.test(character)) return 1.85;
  if (/[A-Z0-9]/.test(character)) return 1.1;
  return 0.92;
}

export function wrapSvgText(value: string, maxUnits: number, maxLines: number): string[] {
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
  return lines;
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

function logoLayer(brand: Brand, href: string | undefined, x: number, y: number, width: number, height: number, color: string): string {
  if (href) return imageLayer("Brand-Logo", href, x, y, width, height);
  return `<text id="Brand-Logo-Fallback" x="${x + width}" y="${y + height * 0.7}" text-anchor="end" fill="${color}" font-family="${escapeXml(brand.font)}, sans-serif" font-size="24" font-weight="800">${escapeXml(brand.name)}</text>`;
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
  <g id="Header">${logoLayer(brand, logo, 914, 48, 118, 44, isYellow ? brand.ink : "#FFFFFF")}</g>
  <g id="Main-Card">
    <rect id="Main-Card-Background" x="48" y="116" width="984" height="916" rx="20" fill="#FFFFFF"/>
    <g id="Label"><rect id="Label-Background" x="96" y="172" width="150" height="48" rx="8" fill="${isYellow ? brand.dark : brand.primary}"/><text id="Label-Text" x="171" y="203" text-anchor="middle" fill="${isYellow ? brand.primary : brand.ink}" font-family="${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="18" font-weight="750">${escapeXml(spec.label)}</text></g>
    <g id="Headline">${textLayers("Headline", headlineLines, 96, 278, 62, `fill="${brand.ink}" font-family="${escapeXml(brand.displayFont)}, ${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="52" font-weight="800"`)}</g>
    <g id="Body">${body}</g>
    ${footer(spec, 986, "#70747B")}
  </g>`;
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
    ${logoLayer(brand, logo, 874, 58, 142, 58, foreground)}
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
  <g id="Header"><path id="Header-Background" d="M74 46H1006C1021.46 46 1034 58.54 1034 74V196H74V46Z" fill="${brand.dark}"/>${logoLayer(brand, assets.logoDark, 840, 90, 138, 62, "#FFFFFF")}</g>
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
    : `<g id="Official-Logo-Safe-Area"><rect id="Logo-Chip" x="908" y="728" width="128" height="50" rx="14" fill="#FFFFFF" fill-opacity="0.05" stroke="#FFFFFF" stroke-opacity="0.18"/>${logoLayer(brand, assets.logoDark, 916, 735, 112, 36, "#FFFFFF")}</g>`;
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
  const frame = sourceRatio >= 1
    ? { x: 0, y: (1080 - 1080 / sourceRatio) / 2, width: 1080, height: 1080 / sourceRatio }
    : { x: (1080 - 1080 * sourceRatio) / 2, y: 0, width: 1080 * sourceRatio, height: 1080 };
  const localized = spec.sourceTextVisible && spec.translationRegions.length > 0;
  const visual = assets.sourceImage
    ? imageLayer("Source-Visual", assets.sourceImage, frame.x, frame.y, frame.width, frame.height)
    : `<rect id="Source-Visual-Placeholder" x="0" y="0" width="1080" height="1080" fill="${brand.dark}"/>`;
  const translationLayers = localized
    ? spec.translationRegions.map((region, index) => {
      const x = frame.x + frame.width * region.x / 100;
      const y = frame.y + frame.height * region.y / 100;
      const width = frame.width * region.width / 100;
      const height = frame.height * region.height / 100;
      let fontSize = frame.width * region.fontSize / 100 * 0.72;
      const minFontSize = Math.max(14, frame.width * 0.02);
      let lineHeight = fontSize * 1.02;
      let lines: string[] = [];
      const paragraphs = region.text.split(/\n+/).map((value) => value.trim()).filter(Boolean);
      if (paragraphs.length > 2) return "";
      while (true) {
        const maxUnits = Math.max(6, width / Math.max(fontSize * region.scaleX, 1) * 2.5);
        const maxLines = Math.max(1, Math.min(2, Math.floor(height / Math.max(lineHeight, 1))));
        lines = [];
        for (const paragraph of paragraphs) {
          if (lines.length >= maxLines) break;
          lines.push(...wrapSvgText(paragraph, maxUnits, maxLines - lines.length));
        }
        const truncated = lines.at(-1)?.endsWith("…") === true;
        if ((!truncated && lines.length * lineHeight <= height) || fontSize <= minFontSize) break;
        fontSize = Math.max(minFontSize, fontSize * 0.92);
        lineHeight = fontSize * 1.02;
      }
      const fits = lines.length > 0
        && lines.at(-1)?.endsWith("…") !== true
        && lines.length * lineHeight <= height;
      if (!fits) return "";
      const blockHeight = lines.length * lineHeight;
      const firstBaseline = y + Math.max(fontSize, (height - blockHeight) / 2 + fontSize);
      const textX = region.align === "center" ? x + width / 2 : region.align === "right" ? x + width : x;
      const textAnchor = region.align === "center" ? "middle" : region.align === "right" ? "end" : "start";
      const font = region.fontRole === "display" ? brand.displayFont : brand.font;
      const regionId = `Korean-Translation-Region-${index + 1}`;
      const clipId = `${regionId}-Clip`;
      const horizontalTransform = `translate(${textX.toFixed(2)} 0) scale(${region.scaleX.toFixed(2)} 1) translate(${(-textX).toFixed(2)} 0)`;
      return `<defs><clipPath id="${clipId}"><rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${width.toFixed(2)}" height="${height.toFixed(2)}"/></clipPath></defs><g id="${regionId}" clip-path="url(#${clipId})">${textLayers(
        `${regionId}-Text`,
        lines,
        textX,
        firstBaseline,
        lineHeight,
        `transform="${horizontalTransform}" text-anchor="${textAnchor}" fill="#FFFFFF" stroke="#100D16" stroke-opacity="0.78" stroke-width="2" paint-order="stroke fill" stroke-linejoin="round" font-family="${escapeXml(font)}, ${escapeXml(brand.font)}, Pretendard, sans-serif" font-size="${fontSize.toFixed(2)}" font-weight="800" letter-spacing="-${(fontSize * 0.035).toFixed(2)}"`,
      )}</g>`;
    })
    : [];
  const translations = translationLayers.length > 0 && translationLayers.every(Boolean)
    ? translationLayers.join("\n")
    : "";
  return `<rect id="Canvas-Background" width="1080" height="1080" fill="${brand.dark}"/>
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
  const content = templateStyle === "editorial"
    ? editorialSvg(brand, spec, assets)
    : templateStyle === "signal"
      ? signalSvg(brand, spec, assets)
      : templateStyle === "remix"
        ? clientId === "squid"
          ? squidTranslationSvg(brand, spec, assets)
          : remixSvg(brand, spec, assets)
        : classicSvg(brand, spec, assets);
  return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1080" viewBox="0 0 1080 1080" role="img" aria-labelledby="Title Description">
  <title id="Title">${escapeXml(brand.name)} editable Korean news card</title>
  <desc id="Description">Figma-editable localized news card. Text, shapes, official logo, and source image are separate named layers.</desc>
  <metadata>Localized News Card · Figma Editable · ${templateStyle}</metadata>
  ${content}
</svg>`;
}
