import {
  ContentCatalogError,
  type ContentCatalogConfig,
  isCatalogUuid,
} from "./content-catalog.mts";

const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const GROK_STATUSES = new Set([
  "pending",
  "claimed",
  "staged",
  "sent",
  "obsolete",
  "failed",
  "provider_unknown",
  "delivery_unknown",
]);
const GROK_DECISIONS = new Set(["PASS", "WARN", "BLOCK"]);
const GROK_NEXT_ACTIONS = new Set([
  "ready_for_human_approval",
  "human_review",
  "verify_source",
  "revise_copy",
  "revise_banner",
]);
const READINESS_KEYS = [
  "content_item_id",
  "content_version_id",
  "generate_job_id",
  "source_item_id",
  "source_published_at",
  "source_is_latest",
  "source_within_24h",
  "feed_active",
  "feed_poll_interval_minutes",
  "feed_last_polled_at",
  "feed_poll_recent",
  "banner_sha256",
  "grok_outbox_count",
  "grok_status",
  "grok_decision",
  "grok_next_action",
  "grok_verdict_sha256",
  "grok_banner_sha256",
  "approval_count",
  "publication_count",
] as const;

export type ContentReviewReadiness = {
  content_item_id: string;
  content_version_id: string;
  generate_job_id: string | null;
  source_item_id: string | null;
  source_published_at: string | null;
  source_is_latest: boolean;
  source_within_24h: boolean;
  feed_active: boolean;
  feed_poll_interval_minutes: number | null;
  feed_last_polled_at: string | null;
  feed_poll_recent: boolean;
  banner_sha256: string | null;
  grok_outbox_count: number;
  grok_status: string | null;
  grok_decision: "PASS" | "WARN" | "BLOCK" | null;
  grok_next_action: string | null;
  grok_verdict_sha256: string | null;
  grok_banner_sha256: string | null;
  approval_count: number;
  publication_count: number;
};

export class ContentReviewReadinessError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "ContentReviewReadinessError";
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

function nullableDate(value: unknown): value is string | null {
  return value === null || validDate(value);
}

function nullableUuid(value: unknown): value is string | null {
  return value === null || isCatalogUuid(value);
}

function nullableSha256(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && SHA256_PATTERN.test(value));
}

function boundedCount(value: unknown, maximum = 1_000_000): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= maximum;
}

function exactKeys(value: Record<string, unknown>): boolean {
  const keys = Object.keys(value).sort();
  return keys.length === READINESS_KEYS.length
    && keys.every((key, index) => key === [...READINESS_KEYS].sort()[index]);
}

function validateReadiness(
  value: unknown,
  contentItemId: string,
  contentVersionId: string,
): ContentReviewReadiness | null {
  if (value === null) return null;
  if (!isRecord(value) || !exactKeys(value)) {
    throw new ContentReviewReadinessError("invalid_content_review_readiness_response");
  }
  if (
    value.content_item_id !== contentItemId
    || value.content_version_id !== contentVersionId
    || !nullableUuid(value.generate_job_id)
    || !nullableUuid(value.source_item_id)
    || !nullableDate(value.source_published_at)
    || typeof value.source_is_latest !== "boolean"
    || typeof value.source_within_24h !== "boolean"
    || typeof value.feed_active !== "boolean"
    || !(value.feed_poll_interval_minutes === null || (
      Number.isSafeInteger(value.feed_poll_interval_minutes)
      && Number(value.feed_poll_interval_minutes) >= 5
      && Number(value.feed_poll_interval_minutes) <= 10_080
    ))
    || !nullableDate(value.feed_last_polled_at)
    || typeof value.feed_poll_recent !== "boolean"
    || !nullableSha256(value.banner_sha256)
    || !boundedCount(value.grok_outbox_count, 1)
    || !(value.grok_status === null || (
      typeof value.grok_status === "string" && GROK_STATUSES.has(value.grok_status)
    ))
    || !(value.grok_decision === null || (
      typeof value.grok_decision === "string" && GROK_DECISIONS.has(value.grok_decision)
    ))
    || !(value.grok_next_action === null || (
      typeof value.grok_next_action === "string" && GROK_NEXT_ACTIONS.has(value.grok_next_action)
    ))
    || !nullableSha256(value.grok_verdict_sha256)
    || !nullableSha256(value.grok_banner_sha256)
    || !boundedCount(value.approval_count)
    || !boundedCount(value.publication_count)
  ) throw new ContentReviewReadinessError("invalid_content_review_readiness_response");

  if (
    (value.grok_outbox_count === 0 && (
      value.grok_status !== null
      || value.grok_decision !== null
      || value.grok_next_action !== null
      || value.grok_verdict_sha256 !== null
      || value.grok_banner_sha256 !== null
    ))
    || (value.source_within_24h && (
      value.source_item_id === null || value.source_published_at === null
    ))
    || (value.source_is_latest && value.source_item_id === null)
    || (value.feed_poll_recent && (
      value.feed_last_polled_at === null
      || value.feed_poll_interval_minutes === null
    ))
    || (value.grok_outbox_count === 1 && (
      value.grok_status === null || value.source_item_id === null
    ))
    || ((value.grok_decision === null) !== (value.grok_next_action === null))
    || ((value.grok_decision === null) !== (value.grok_verdict_sha256 === null))
    || (
      value.banner_sha256 !== null
      && value.grok_banner_sha256 !== null
      && value.banner_sha256 !== value.grok_banner_sha256
    )
  ) throw new ContentReviewReadinessError("invalid_content_review_readiness_response");

  return {
    content_item_id: contentItemId,
    content_version_id: contentVersionId,
    generate_job_id: value.generate_job_id === null
      ? null
      : String(value.generate_job_id).toLowerCase(),
    source_item_id: value.source_item_id === null
      ? null
      : String(value.source_item_id).toLowerCase(),
    source_published_at: value.source_published_at as string | null,
    source_is_latest: value.source_is_latest,
    source_within_24h: value.source_within_24h,
    feed_active: value.feed_active,
    feed_poll_interval_minutes: value.feed_poll_interval_minutes as number | null,
    feed_last_polled_at: value.feed_last_polled_at as string | null,
    feed_poll_recent: value.feed_poll_recent,
    banner_sha256: value.banner_sha256 as string | null,
    grok_outbox_count: Number(value.grok_outbox_count),
    grok_status: value.grok_status as string | null,
    grok_decision: value.grok_decision as ContentReviewReadiness["grok_decision"],
    grok_next_action: value.grok_next_action as string | null,
    grok_verdict_sha256: value.grok_verdict_sha256 as string | null,
    grok_banner_sha256: value.grok_banner_sha256 as string | null,
    approval_count: Number(value.approval_count),
    publication_count: Number(value.publication_count),
  };
}

export async function getContentReviewReadiness(
  config: ContentCatalogConfig,
  contentItemId: string,
  contentVersionId: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<ContentReviewReadiness | null> {
  const normalizedItemId = contentItemId.toLowerCase();
  const normalizedVersionId = contentVersionId.toLowerCase();
  if (!isCatalogUuid(normalizedItemId) || !isCatalogUuid(normalizedVersionId)) {
    throw new ContentCatalogError("invalid_library_item_id");
  }

  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/rest/v1/rpc/get_content_review_readiness`,
      {
        method: "POST",
        headers: {
          apikey: config.serviceRoleKey,
          Authorization: `Bearer ${config.serviceRoleKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          target_workspace_id: config.workspaceId,
          target_content_item_id: normalizedItemId,
          target_content_version_id: normalizedVersionId,
        }),
        signal,
      },
    );
  } catch {
    throw new ContentReviewReadinessError("content_review_readiness_unavailable");
  }
  if (!response.ok) {
    throw new ContentReviewReadinessError("content_review_readiness_unavailable");
  }
  let result: unknown;
  try {
    result = await response.json();
  } catch {
    throw new ContentReviewReadinessError("invalid_content_review_readiness_response");
  }
  return validateReadiness(result, normalizedItemId, normalizedVersionId);
}
