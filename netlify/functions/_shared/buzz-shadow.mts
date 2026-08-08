import { createHash, timingSafeEqual } from "node:crypto";

import {
  batchReviewConfig,
  type BatchReviewConfig,
  type BatchReviewCursor,
  type BatchReviewListItem,
  type BatchReviewPage,
} from "./batch-review.mts";
import { isCatalogUuid } from "./content-catalog.mts";

export const BUZZ_SHADOW_TOKEN_HEADER = "x-coineasy-buzz-key";

const BUZZ_SHADOW_SCHEMA_VERSION = "1.0";
const BUZZ_SHADOW_EVENT_TYPE = "origintrail.batch_review_ready.v1";
const MINIMUM_TOKEN_LENGTH = 32;
const MAXIMUM_TOKEN_LENGTH = 512;
const MAXIMUM_EVENTS = 50;
const RESERVED_SECRET_ENVS = [
  "BUZZ_DELIVERY_WORKER_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY",
  "SUPABASE_BUZZ_DELIVERY_KEY",
  "SUPABASE_BUZZ_SHADOW_KEY",
  "STUDIO_ACCESS_TOKEN",
  "API_SECRET",
  "PUBLICATION_WORKER_TOKEN",
] as const;
const OFFICIAL_SOURCE_PATTERN = /^https:\/\/x\.com\/origin_trail\/status\/[0-9]{1,19}$/;

export type BuzzShadowEvent = {
  event_id: string;
  event_type: "origintrail.batch_review_ready.v1";
  job_id: string;
  review_ref: string;
  client_id: "origintrail";
  agent_id: "origintrail_client_agent";
  workflow_kind: "official_source_nonurgent_pack";
  result_code: "needs_review";
  model_tier: "S" | "M";
  actual_cost_microusd: number;
  finished_at: string;
  source_url: string;
  studio_review_path: string;
  headline_ko: string;
  summary_ko: string;
};

export type BuzzShadowPreview = {
  headline_ko: string;
  summary_ko: string;
};

export type BuzzShadowPage = {
  schema_version: "1.0";
  mode: "shadow_read_only";
  events: BuzzShadowEvent[];
  next_cursor: BatchReviewCursor | null;
};

export class BuzzShadowError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "BuzzShadowError";
    this.code = code;
  }
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

function configuredToken(
  getEnv: (name: string) => string | undefined,
): string | null {
  const value = (getEnv("BUZZ_SHADOW_ACCESS_TOKEN") || "").trim();
  // Same distinctness rule the delivery token already enforces: a shadow
  // token equal to another secret means a mis-provisioned environment, so
  // the endpoint fails closed as not-configured instead of accepting it.
  const reused = RESERVED_SECRET_ENVS.some((name) => {
    const reserved = (getEnv(name) || "").trim();
    return reserved.length > 0 && reserved === value;
  });
  return value.length >= MINIMUM_TOKEN_LENGTH
    && value.length <= MAXIMUM_TOKEN_LENGTH
    && !reused
    ? value
    : null;
}

export function buzzShadowAccessConfigured(
  getEnv: (name: string) => string | undefined,
): boolean {
  return configuredToken(getEnv) !== null;
}

export function buzzShadowBatchReviewConfig(
  getEnv: (name: string) => string | undefined,
): BatchReviewConfig | null {
  const config = batchReviewConfig(getEnv);
  if (!config) return null;
  // PostgREST still requires the project API key in `apikey`. The scoped
  // custom-role JWT replaces only the bearer used for database authorization.
  const scopedKey = (getEnv("SUPABASE_BUZZ_SHADOW_KEY") || "").trim();
  return scopedKey ? { ...config, authorizationKey: scopedKey } : config;
}

export function hasValidBuzzShadowAccess(
  request: Request,
  getEnv: (name: string) => string | undefined = (name) => Netlify.env.get(name),
): boolean {
  const expected = configuredToken(getEnv);
  const candidate = (request.headers.get(BUZZ_SHADOW_TOKEN_HEADER) || "").trim();
  if (
    !expected
    || candidate.length < MINIMUM_TOKEN_LENGTH
    || candidate.length > MAXIMUM_TOKEN_LENGTH
  ) return false;
  return timingSafeEqual(digest(candidate), digest(expected));
}

function validTimestamp(value: string): boolean {
  return value.length >= 20
    && value.length <= 40
    && Number.isFinite(Date.parse(value));
}

function validPreviewText(value: unknown, maximum: number): value is string {
  return typeof value === "string"
    && value.trim() === value
    && value.length >= 1
    && value.length <= maximum
    && !value.includes("@")
    && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value);
}

export function buzzResultPreviewStartAt(
  getEnv: (name: string) => string | undefined,
): number | null {
  const value = (getEnv("BUZZ_RESULT_PREVIEW_START_AT") || "").trim();
  if (!validTimestamp(value)) return null;
  return Date.parse(value);
}

function validReviewItem(item: BatchReviewListItem): boolean {
  return isCatalogUuid(item.job_id)
    && item.job_id === item.job_id.toLowerCase()
    && item.ref === `batch:${item.job_id}`
    && item.client_id === "origintrail"
    && item.agent_id === "origintrail_client_agent"
    && item.workflow_kind === "official_source_nonurgent_pack"
    && item.stage === "generate"
    && item.status === "completed"
    && item.result_code === "needs_review"
    && (item.model_tier === "S" || item.model_tier === "M")
    && item.model === (item.model_tier === "S" ? "gpt-5.6-luna" : "gpt-5.6-terra")
    && Number.isSafeInteger(item.actual_cost_microusd)
    && item.actual_cost_microusd >= 0
    && item.actual_cost_microusd <= 500_000
    && validTimestamp(item.finished_at)
    && typeof item.source_url === "string"
    && OFFICIAL_SOURCE_PATTERN.test(item.source_url);
}

function eventId(workspaceId: string, jobId: string): string {
  return createHash("sha256")
    .update(
      `coineasy-buzz-shadow\u0000${BUZZ_SHADOW_SCHEMA_VERSION}\u0000${workspaceId}\u0000origintrail\u0000${jobId}`,
      "utf8",
    )
    .digest("hex");
}

function projectEvent(
  item: BatchReviewListItem,
  workspaceId: string,
  preview: BuzzShadowPreview | undefined,
): BuzzShadowEvent {
  if (!validReviewItem(item)) {
    throw new BuzzShadowError("buzz_shadow_invalid_review_page");
  }
  if (
    !validTimestamp(item.finished_at)
    || item.source_url === null
    || !preview
    || !validPreviewText(preview.headline_ko, 120)
    || !validPreviewText(preview.summary_ko, 1_800)
  ) {
    throw new BuzzShadowError("buzz_shadow_invalid_review_page");
  }
  return {
    event_id: eventId(workspaceId, item.job_id),
    event_type: BUZZ_SHADOW_EVENT_TYPE,
    job_id: item.job_id,
    review_ref: item.ref,
    client_id: "origintrail",
    agent_id: "origintrail_client_agent",
    workflow_kind: "official_source_nonurgent_pack",
    result_code: "needs_review",
    model_tier: item.model_tier,
    actual_cost_microusd: item.actual_cost_microusd,
    finished_at: item.finished_at,
    source_url: item.source_url,
    studio_review_path: `/?batch=${encodeURIComponent(item.job_id)}`,
    headline_ko: preview.headline_ko,
    summary_ko: preview.summary_ko,
  };
}

function projectCursor(cursor: BatchReviewCursor | null): BatchReviewCursor | null {
  if (cursor === null) return null;
  if (
    !validTimestamp(cursor.finished_at)
    || !isCatalogUuid(cursor.job_id)
    || cursor.job_id !== cursor.job_id.toLowerCase()
  ) throw new BuzzShadowError("buzz_shadow_invalid_review_page");
  return { finished_at: cursor.finished_at, job_id: cursor.job_id };
}

export function projectBuzzShadowPage(
  page: BatchReviewPage,
  workspaceId: string,
  previews: ReadonlyMap<string, BuzzShadowPreview>,
): BuzzShadowPage {
  if (
    !isCatalogUuid(workspaceId)
    || workspaceId !== workspaceId.toLowerCase()
    || !Array.isArray(page.items)
    || page.items.length > MAXIMUM_EVENTS
  ) {
    throw new BuzzShadowError("buzz_shadow_invalid_review_page");
  }
  const seen = new Set<string>();
  const events = page.items.map((item) => {
    if (seen.has(item.job_id)) {
      throw new BuzzShadowError("buzz_shadow_invalid_review_page");
    }
    seen.add(item.job_id);
    return projectEvent(item, workspaceId, previews.get(item.job_id));
  });
  return {
    schema_version: BUZZ_SHADOW_SCHEMA_VERSION,
    mode: "shadow_read_only",
    events,
    next_cursor: projectCursor(page.next_cursor),
  };
}
