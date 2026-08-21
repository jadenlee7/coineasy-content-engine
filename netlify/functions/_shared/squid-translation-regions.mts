type PercentBox = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export type ValidatedSquidTranslationRegion = {
  input: Record<string, unknown>;
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
};

function cleanText(value: unknown, maxLength: number): string {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : "";
}

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

export function validateSquidTranslationRegions(
  value: unknown,
): ValidatedSquidTranslationRegion[] | null {
  if (!Array.isArray(value) || value.length < 1 || value.length > 4) return null;

  const validated: ValidatedSquidTranslationRegion[] = [];
  for (const rawRegion of value) {
    if (!rawRegion || typeof rawRegion !== "object" || Array.isArray(rawRegion)) return null;
    const region = rawRegion as Record<string, unknown>;
    const sourceText = cleanText(region.source_text, 240);
    const text = cleanText(region.text, 240);
    const target = strictPercentBox(region, ["x", "y", "width", "height"]);
    const source = strictPercentBox(
      region,
      ["source_x", "source_y", "source_width", "source_height"],
    );
    if (!sourceText || !text || !target || !source) return null;

    const sameTarget = ["x", "y", "width", "height"].every((key) => (
      Math.abs(target[key as keyof PercentBox] - source[key as keyof PercentBox]) <= 0.01
    ));
    if (!sameTarget) return null;

    const explicitLines = text.split(/\n+/).map((line) => line.trim()).filter(Boolean);
    if (explicitLines.length > 2) return null;

    const overlapsExisting = validated.some((existing) => (
      target.x < existing.x + existing.width
      && target.x + target.width > existing.x
      && target.y < existing.y + existing.height
      && target.y + target.height > existing.y
    ));
    if (overlapsExisting) return null;

    validated.push({
      input: region,
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
    });
  }
  return validated;
}
