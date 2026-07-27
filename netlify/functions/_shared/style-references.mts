import { createHash } from "node:crypto";

export type StyleReference = {
  source_item_id: string;
  source_url: string;
  text: string;
  published_at: string;
};

export type StyleReferencePack = {
  references: StyleReference[];
  packHash: string;
};

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const X_STATUS_PATTERN = /^https:\/\/x\.com\/[A-Za-z0-9_]{1,15}\/status\/[0-9]{1,19}$/;
const PACK_HASH_PATTERN = /^[a-f0-9]{32}$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/;

export class StyleReferenceInputError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.code = code;
  }
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export function parseStyleReferencePack(
  rawReferences: unknown,
  rawPackHash: unknown,
): StyleReferencePack {
  if (rawReferences === undefined && rawPackHash === undefined) {
    return { references: [], packHash: "" };
  }
  if (!Array.isArray(rawReferences) || rawReferences.length > 3) {
    throw new StyleReferenceInputError("invalid_style_references");
  }
  if (typeof rawPackHash !== "string" || !PACK_HASH_PATTERN.test(rawPackHash)) {
    throw new StyleReferenceInputError("invalid_style_reference_pack_hash");
  }

  const references: StyleReference[] = [];
  const sourceIds = new Set<string>();
  const sourceUrls = new Set<string>();
  for (const rawReference of rawReferences) {
    const reference = objectValue(rawReference);
    if (!reference) {
      throw new StyleReferenceInputError("invalid_style_references");
    }
    const allowedKeys = new Set([
      "source_item_id",
      "source_url",
      "text",
      "published_at",
    ]);
    if (Object.keys(reference).some((key) => !allowedKeys.has(key))) {
      throw new StyleReferenceInputError("invalid_style_references");
    }
    const sourceItemId = typeof reference.source_item_id === "string"
      ? reference.source_item_id.toLowerCase()
      : "";
    const sourceUrl = typeof reference.source_url === "string"
      ? reference.source_url
      : "";
    const text = typeof reference.text === "string"
      ? reference.text.trim()
      : "";
    const publishedAt = typeof reference.published_at === "string"
      ? reference.published_at
      : "";
    if (
      !UUID_PATTERN.test(sourceItemId)
      || !X_STATUS_PATTERN.test(sourceUrl)
      || !text
      || text.length > 600
      || !DATE_PATTERN.test(publishedAt)
      || !Number.isFinite(Date.parse(publishedAt))
      || sourceIds.has(sourceItemId)
      || sourceUrls.has(sourceUrl)
    ) {
      throw new StyleReferenceInputError("invalid_style_references");
    }
    sourceIds.add(sourceItemId);
    sourceUrls.add(sourceUrl);
    references.push({
      source_item_id: sourceItemId,
      source_url: sourceUrl,
      text,
      published_at: publishedAt,
    });
  }
  return { references, packHash: rawPackHash };
}

export function styleReferenceAudit(pack: StyleReferencePack): {
  style_reference_count: number;
  style_reference_pack_hash: string | null;
  style_reference_urls: string[];
  style_reference_payload_hash: string | null;
} {
  return {
    style_reference_count: pack.references.length,
    style_reference_pack_hash: pack.packHash || null,
    style_reference_urls: pack.references.map((item) => item.source_url),
    style_reference_payload_hash: pack.packHash
      ? createHash("sha256").update(
        JSON.stringify(pack.references),
        "utf8",
      ).digest("hex")
      : null,
  };
}
