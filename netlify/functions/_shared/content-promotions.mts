import {
  type ContentCatalogConfig,
  isCatalogUuid,
} from "./content-catalog.mts";

const POLICY_VERSION = "content-performance-v1";
const CHANNELS = new Set(["x", "telegram"]);
const TARGET_KINDS = new Set(["article", "tutorial"]);
const REASON_CODES = new Set([
  "high_reach",
  "high_interaction",
  "community_alignment",
  "tutorial_learning_signal",
]);
const X_PATH = /^\/([A-Za-z0-9_]{1,15})\/status\/([1-9][0-9]{0,18})\/?$/;
const TELEGRAM_PATH = /^\/([A-Za-z][A-Za-z0-9_]{4,31})\/([1-9][0-9]{0,18})\/?$/;
const MAX_RECOMMENDATIONS = 8;
const MAX_MANUAL_PUBLICATIONS = 2;
const MAX_EVIDENCE_AGE_MS = 24 * 60 * 60 * 1_000;
const MAX_EVIDENCE_FUTURE_MS = 5 * 60 * 1_000;

export type PromotionChannel = "x" | "telegram";
export type PromotionTargetKind = "article" | "tutorial";

export type PromotionRecommendation = {
  recommendation_id: string;
  content_version_id: string;
  target_kind: PromotionTargetKind;
  score: number;
  reason_codes: string[];
  policy_version: string;
  source_url: string;
  channel: PromotionChannel;
  observed_at: string;
  source_ready: boolean;
};

export type ManualPublication = {
  publication_id: string;
  content_version_id: string;
  channel: PromotionChannel;
  external_url: string;
};

export type ContentPromotionSummary = {
  items: PromotionRecommendation[];
  publications: ManualPublication[];
};

export type ManualPublicationReceipt = {
  publication_id: string;
  content_version_id: string;
  channel: PromotionChannel;
  external_url: string;
  reused: boolean;
};

export class ContentPromotionError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "ContentPromotionError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validDate(value: unknown): value is string {
  return typeof value === "string"
    && value.length >= 20
    && value.length <= 40
    && Number.isFinite(Date.parse(value));
}

function validObservedAt(value: unknown, now = Date.now()): value is string {
  if (!validDate(value)) return false;
  const timestamp = Date.parse(value);
  return timestamp >= now - MAX_EVIDENCE_AGE_MS
    && timestamp <= now + MAX_EVIDENCE_FUTURE_MS;
}

function headers(config: ContentCatalogConfig): Record<string, string> {
  return {
    apikey: config.serviceRoleKey,
    Authorization: `Bearer ${config.serviceRoleKey}`,
    "Content-Type": "application/json",
  };
}

export function canonicalPublicationUrl(
  channel: PromotionChannel,
  value: unknown,
): string | null {
  if (!CHANNELS.has(channel) || typeof value !== "string" || value.length > 500) {
    return null;
  }
  let parsed: URL;
  try {
    parsed = new URL(value.trim());
  } catch {
    return null;
  }
  if (
    parsed.protocol !== "https:"
    || parsed.port
    || parsed.username
    || parsed.password
  ) return null;

  const rawHostname = parsed.hostname.toLowerCase();
  const hostname = rawHostname.startsWith("www.")
    ? rawHostname.slice(4)
    : rawHostname;
  if (channel === "x") {
    if (hostname !== "x.com" && hostname !== "twitter.com") return null;
    const match = parsed.pathname.match(X_PATH);
    if (!match) return null;
    return `https://x.com/${match[1].toLowerCase()}/status/${match[2]}`;
  }

  if (hostname !== "t.me" && hostname !== "telegram.me") return null;
  const publicPath = parsed.pathname.replace(/^\/s\//i, "/");
  const match = publicPath.match(TELEGRAM_PATH);
  if (!match || match[1].toLowerCase() === "c") return null;
  return `https://t.me/${match[1].toLowerCase()}/${match[2]}`;
}

function recommendation(
  value: unknown,
  expectedVersionId: string,
): PromotionRecommendation | null {
  if (!isRecord(value)) return null;
  const channel = value.channel;
  const targetKind = value.target_kind;
  const score = value.score;
  const reasonCodes = value.reason_codes;
  const sourceUrl = typeof channel === "string"
    ? canonicalPublicationUrl(channel as PromotionChannel, value.source_url)
    : null;
  if (
    !isCatalogUuid(value.recommendation_id)
    || !isCatalogUuid(value.content_version_id)
    || value.content_version_id.toLowerCase() !== expectedVersionId
    || typeof targetKind !== "string"
    || !TARGET_KINDS.has(targetKind)
    || typeof channel !== "string"
    || !CHANNELS.has(channel)
    || typeof score !== "number"
    || !Number.isFinite(score)
    || score < 0
    || score > 1
    || !Array.isArray(reasonCodes)
    || reasonCodes.length < 1
    || reasonCodes.length > 5
    || reasonCodes.some((code) => typeof code !== "string" || !REASON_CODES.has(code))
    || new Set(reasonCodes).size !== reasonCodes.length
    || value.policy_version !== POLICY_VERSION
    || !sourceUrl
    || !validObservedAt(value.observed_at)
    || typeof value.source_ready !== "boolean"
  ) return null;
  return {
    recommendation_id: value.recommendation_id.toLowerCase(),
    content_version_id: value.content_version_id.toLowerCase(),
    target_kind: targetKind as PromotionTargetKind,
    score,
    reason_codes: [...reasonCodes].sort(),
    policy_version: POLICY_VERSION,
    source_url: sourceUrl,
    channel: channel as PromotionChannel,
    observed_at: value.observed_at,
    source_ready: value.source_ready,
  };
}

function manualPublication(
  value: unknown,
  expectedVersionId: string,
): ManualPublication | null {
  if (!isRecord(value)) return null;
  const channel = value.channel;
  const externalUrl = typeof channel === "string"
    ? canonicalPublicationUrl(channel as PromotionChannel, value.external_url)
    : null;
  if (
    !isCatalogUuid(value.publication_id)
    || !isCatalogUuid(value.content_version_id)
    || value.content_version_id.toLowerCase() !== expectedVersionId
    || typeof channel !== "string"
    || !CHANNELS.has(channel)
    || !externalUrl
  ) return null;
  return {
    publication_id: value.publication_id.toLowerCase(),
    content_version_id: value.content_version_id.toLowerCase(),
    channel: channel as PromotionChannel,
    external_url: externalUrl,
  };
}

export async function listContentPromotionRecommendations(
  config: ContentCatalogConfig,
  contentItemId: string,
  contentVersionId: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<ContentPromotionSummary> {
  if (!isCatalogUuid(contentItemId) || !isCatalogUuid(contentVersionId)) {
    throw new ContentPromotionError("invalid_library_item_id");
  }
  const expectedVersionId = contentVersionId.toLowerCase();
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/rest/v1/rpc/list_content_promotion_recommendations`,
      {
        method: "POST",
        headers: headers(config),
        body: JSON.stringify({
          target_workspace_id: config.workspaceId,
          target_content_item_id: contentItemId.toLowerCase(),
          target_content_version_id: expectedVersionId,
        }),
        signal,
      },
    );
  } catch {
    throw new ContentPromotionError("content_promotions_unavailable");
  }
  if (!response.ok) throw new ContentPromotionError("content_promotions_unavailable");
  let raw: unknown;
  try {
    raw = await response.json();
  } catch {
    throw new ContentPromotionError("content_promotions_invalid_response");
  }
  const items = isRecord(raw) ? raw.items : null;
  const publications = isRecord(raw) ? raw.publications : null;
  if (
    !Array.isArray(items)
    || items.length > MAX_RECOMMENDATIONS
    || !Array.isArray(publications)
    || publications.length > MAX_MANUAL_PUBLICATIONS
  ) {
    throw new ContentPromotionError("content_promotions_invalid_response");
  }
  const parsed = items.map((item) => recommendation(item, expectedVersionId));
  if (parsed.some((item) => item === null)) {
    throw new ContentPromotionError("content_promotions_invalid_response");
  }
  const unique = new Set(parsed.map((item) => item!.recommendation_id));
  if (unique.size !== parsed.length) {
    throw new ContentPromotionError("content_promotions_invalid_response");
  }
  const parsedPublications = publications.map((item) =>
    manualPublication(item, expectedVersionId)
  );
  if (parsedPublications.some((item) => item === null)) {
    throw new ContentPromotionError("content_promotions_invalid_response");
  }
  const publicationIds = new Set(
    parsedPublications.map((item) => item!.publication_id),
  );
  const publicationChannels = new Set(
    parsedPublications.map((item) => item!.channel),
  );
  if (
    publicationIds.size !== parsedPublications.length
    || publicationChannels.size !== parsedPublications.length
  ) {
    throw new ContentPromotionError("content_promotions_invalid_response");
  }
  return {
    items: parsed as PromotionRecommendation[],
    publications: parsedPublications as ManualPublication[],
  };
}

export async function recordManualPublicationObservation(
  config: ContentCatalogConfig,
  contentItemId: string,
  contentVersionId: string,
  channel: PromotionChannel,
  externalUrl: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<ManualPublicationReceipt> {
  const canonicalUrl = canonicalPublicationUrl(channel, externalUrl);
  if (
    !isCatalogUuid(contentItemId)
    || !isCatalogUuid(contentVersionId)
    || !canonicalUrl
  ) {
    throw new ContentPromotionError("invalid_publication_observation");
  }
  const expectedVersionId = contentVersionId.toLowerCase();
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/rest/v1/rpc/record_manual_publication_observation`,
      {
        method: "POST",
        headers: headers(config),
        body: JSON.stringify({
          target_workspace_id: config.workspaceId,
          target_content_item_id: contentItemId.toLowerCase(),
          target_content_version_id: expectedVersionId,
          target_channel: channel,
          target_external_url: canonicalUrl,
        }),
        signal,
      },
    );
  } catch {
    throw new ContentPromotionError("publication_observation_unavailable");
  }
  if (!response.ok) {
    const code = response.status === 409
      ? "publication_observation_conflict"
      : response.status === 400
        ? "invalid_publication_observation"
        : "publication_observation_unavailable";
    throw new ContentPromotionError(code);
  }
  let raw: unknown;
  try {
    raw = await response.json();
  } catch {
    throw new ContentPromotionError("publication_observation_invalid_response");
  }
  if (
    !isRecord(raw)
    || !isCatalogUuid(raw.publication_id)
    || !isCatalogUuid(raw.content_version_id)
    || raw.content_version_id.toLowerCase() !== expectedVersionId
    || raw.channel !== channel
    || raw.external_url !== canonicalUrl
    || typeof raw.reused !== "boolean"
  ) throw new ContentPromotionError("publication_observation_invalid_response");
  return {
    publication_id: raw.publication_id.toLowerCase(),
    content_version_id: raw.content_version_id.toLowerCase(),
    channel,
    external_url: canonicalUrl,
    reused: raw.reused,
  };
}

export { POLICY_VERSION };
