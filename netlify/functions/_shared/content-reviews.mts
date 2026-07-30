import { createHash } from "node:crypto";
import {
  ContentCatalogError,
  type ContentCatalogConfig,
  type ContentCatalogKind,
} from "./content-catalog.mts";

export const REVIEW_REASON_CODES = [
  "off_brand_tone",
  "unsupported_claim",
  "awkward_korean",
  "visual_brand_mismatch",
  "duplicate_logo",
  "source_fidelity",
  "channel_fit",
  "other",
] as const;

export type ReviewReasonCode = typeof REVIEW_REASON_CODES[number];
export type StudioReviewDecision = "approved" | "rejected";

export type StudioReviewInput = {
  contentVersionId: string;
  decision: StudioReviewDecision;
  reasonCodes: ReviewReasonCode[];
  comment: string | null;
  idempotencyKey: string;
};

export type ContentReviewSummary = {
  approval_id: string;
  content_version_id: string;
  decision: StudioReviewDecision | "commented";
  reason_codes: ReviewReasonCode[];
  comment: string | null;
  reviewer_source: "studio_session" | "supabase_auth";
  created_at: string;
};

export type BrandReviewGuidance = {
  policy_version: "brand-review-learning@1";
  approved_examples: Array<{
    content_item_id: string;
    content_version_id: string;
    content_kind: ContentCatalogKind;
    text: string;
    approved_at: string;
  }>;
  avoid_reason_codes: Array<{
    code: ReviewReasonCode;
    count: number;
  }>;
};

export class ContentReviewError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "ContentReviewError";
    this.code = code;
  }
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const REASON_CODES = new Set<string>(REVIEW_REASON_CODES);
const CONTENT_KINDS = new Set<string>(["daily_news", "article", "tutorial"]);

function headers(config: ContentCatalogConfig): Record<string, string> {
  return {
    apikey: config.serviceRoleKey,
    Authorization: `Bearer ${config.serviceRoleKey}`,
    "Content-Type": "application/json",
  };
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function validDate(value: unknown): value is string {
  return typeof value === "string"
    && value.length >= 20
    && value.length <= 40
    && Number.isFinite(Date.parse(value));
}

function validReasonCodes(value: unknown, maximum = 5): value is ReviewReasonCode[] {
  return Array.isArray(value)
    && value.length <= maximum
    && new Set(value).size === value.length
    && value.every((code) => typeof code === "string" && REASON_CODES.has(code));
}

function validateReviewSummary(value: unknown): ContentReviewSummary | null {
  if (value === null) return null;
  const row = objectValue(value);
  if (
    !row
    || !UUID_PATTERN.test(String(row.approval_id || ""))
    || !UUID_PATTERN.test(String(row.content_version_id || ""))
    || !["approved", "rejected", "commented"].includes(String(row.decision || ""))
    || !validReasonCodes(row.reason_codes)
    || !(row.comment === null || (
      typeof row.comment === "string" && row.comment.length <= 4000
    ))
    || !["studio_session", "supabase_auth"].includes(String(row.reviewer_source || ""))
    || !validDate(row.created_at)
  ) throw new ContentReviewError("invalid_content_review_response");
  return {
    approval_id: String(row.approval_id).toLowerCase(),
    content_version_id: String(row.content_version_id).toLowerCase(),
    decision: row.decision as ContentReviewSummary["decision"],
    reason_codes: row.reason_codes as ReviewReasonCode[],
    comment: row.comment as string | null,
    reviewer_source: row.reviewer_source as ContentReviewSummary["reviewer_source"],
    created_at: row.created_at,
  };
}

function validateGuidance(
  value: unknown,
  expectedKind: ContentCatalogKind,
): BrandReviewGuidance {
  const guidance = objectValue(value);
  if (
    !guidance
    || guidance.policy_version !== "brand-review-learning@1"
    || !Array.isArray(guidance.approved_examples)
    || guidance.approved_examples.length > 3
    || !Array.isArray(guidance.avoid_reason_codes)
    || guidance.avoid_reason_codes.length > 5
  ) throw new ContentReviewError("invalid_brand_review_guidance");

  const approvedExamples: BrandReviewGuidance["approved_examples"] = [];
  const itemIds = new Set<string>();
  for (const raw of guidance.approved_examples) {
    const example = objectValue(raw);
    const itemId = String(example?.content_item_id || "").toLowerCase();
    const versionId = String(example?.content_version_id || "").toLowerCase();
    const text = typeof example?.text === "string" ? example.text.trim() : "";
    if (
      !example
      || !UUID_PATTERN.test(itemId)
      || !UUID_PATTERN.test(versionId)
      || example.content_kind !== expectedKind
      || !CONTENT_KINDS.has(String(example.content_kind))
      || !text
      || text.length > 1200
      || !validDate(example.approved_at)
      || itemIds.has(itemId)
    ) throw new ContentReviewError("invalid_brand_review_guidance");
    itemIds.add(itemId);
    approvedExamples.push({
      content_item_id: itemId,
      content_version_id: versionId,
      content_kind: example.content_kind as ContentCatalogKind,
      text,
      approved_at: example.approved_at,
    });
  }

  const avoidReasonCodes: BrandReviewGuidance["avoid_reason_codes"] = [];
  const codes = new Set<string>();
  for (const raw of guidance.avoid_reason_codes) {
    const reason = objectValue(raw);
    if (
      !reason
      || typeof reason.code !== "string"
      || !REASON_CODES.has(reason.code)
      || !Number.isSafeInteger(reason.count)
      || Number(reason.count) < 1
      || Number(reason.count) > 1_000_000
      || codes.has(reason.code)
    ) throw new ContentReviewError("invalid_brand_review_guidance");
    codes.add(reason.code);
    avoidReasonCodes.push({
      code: reason.code as ReviewReasonCode,
      count: Number(reason.count),
    });
  }

  return {
    policy_version: "brand-review-learning@1",
    approved_examples: approvedExamples,
    avoid_reason_codes: avoidReasonCodes,
  };
}

async function rpc(
  config: ContentCatalogConfig,
  functionName: string,
  body: Record<string, unknown>,
  fetcher: typeof fetch,
  signal: AbortSignal,
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/${functionName}`, {
      method: "POST",
      headers: headers(config),
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    throw new ContentReviewError("content_review_storage_unavailable");
  }
  if (!response.ok) {
    const failure = objectValue(await response.json().catch(() => null));
    const message = typeof failure?.message === "string" ? failure.message : "";
    if (message.includes("idempotency conflict")) {
      throw new ContentReviewError("content_review_idempotency_conflict");
    }
    if (message.includes("mock/test content")) {
      throw new ContentReviewError("mock_content_cannot_be_approved");
    }
    if (message.includes("not awaiting")) {
      throw new ContentReviewError("content_not_awaiting_review");
    }
    if (message.includes("not found")) {
      throw new ContentReviewError("library_item_not_found");
    }
    if (
      message.includes("invalid")
      || message.includes("requires a reason")
      || message.includes("too long")
      || message.includes("not current")
    ) {
      throw new ContentReviewError("invalid_content_review");
    }
    throw new ContentReviewError("content_review_storage_unavailable");
  }
  try {
    return await response.json();
  } catch {
    throw new ContentReviewError("invalid_content_review_response");
  }
}

export async function recordStudioContentReview(
  config: ContentCatalogConfig,
  contentItemId: string,
  input: StudioReviewInput,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<{
  approval_id: string;
  content_item_id: string;
  content_version_id: string;
  decision: StudioReviewDecision;
  status: StudioReviewDecision;
  reason_codes: ReviewReasonCode[];
  created_at: string;
  reused: boolean;
}> {
  const raw = await rpc(config, "record_studio_content_review", {
    target_workspace_id: config.workspaceId,
    target_content_item_id: contentItemId,
    target_content_version_id: input.contentVersionId,
    review_decision: input.decision,
    review_reason_codes: input.reasonCodes,
    review_comment: input.comment,
    review_idempotency_key: input.idempotencyKey,
  }, fetcher, signal);
  const result = objectValue(raw);
  if (
    !result
    || !UUID_PATTERN.test(String(result.approval_id || ""))
    || String(result.content_item_id).toLowerCase() !== contentItemId.toLowerCase()
    || String(result.content_version_id).toLowerCase() !== input.contentVersionId.toLowerCase()
    || result.decision !== input.decision
    || result.status !== input.decision
    || !validReasonCodes(result.reason_codes)
    || !validDate(result.created_at)
    || typeof result.reused !== "boolean"
  ) throw new ContentReviewError("invalid_content_review_response");
  return {
    approval_id: String(result.approval_id).toLowerCase(),
    content_item_id: contentItemId.toLowerCase(),
    content_version_id: input.contentVersionId.toLowerCase(),
    decision: input.decision,
    status: input.decision,
    reason_codes: result.reason_codes as ReviewReasonCode[],
    created_at: result.created_at,
    reused: result.reused,
  };
}

export async function getContentReviewSummary(
  config: ContentCatalogConfig,
  contentItemId: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<ContentReviewSummary | null> {
  const raw = await rpc(config, "get_content_review_summary", {
    target_workspace_id: config.workspaceId,
    target_content_item_id: contentItemId,
  }, fetcher, signal);
  return validateReviewSummary(raw);
}

export async function getBrandReviewGuidance(
  config: ContentCatalogConfig,
  clientId: string,
  contentKind: ContentCatalogKind,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(6_000),
): Promise<BrandReviewGuidance> {
  const raw = await rpc(config, "get_brand_review_guidance", {
    target_workspace_id: config.workspaceId,
    target_client_id: clientId,
    target_content_kind: contentKind,
    target_example_limit: 3,
  }, fetcher, signal);
  return validateGuidance(raw, contentKind);
}

export function brandReviewGuidanceAudit(guidance: BrandReviewGuidance): {
  brand_review_policy_version: string;
  brand_review_example_ids: string[];
  brand_review_avoid_reason_codes: ReviewReasonCode[];
  brand_review_guidance_hash: string;
} {
  return {
    brand_review_policy_version: guidance.policy_version,
    brand_review_example_ids: guidance.approved_examples.map((item) => item.content_item_id),
    brand_review_avoid_reason_codes: guidance.avoid_reason_codes.map((item) => item.code),
    brand_review_guidance_hash: createHash("sha256")
      .update(JSON.stringify(guidance), "utf8")
      .digest("hex"),
  };
}

export function emptyBrandReviewGuidance(): BrandReviewGuidance {
  return {
    policy_version: "brand-review-learning@1",
    approved_examples: [],
    avoid_reason_codes: [],
  };
}

export function contentReviewStatus(error: unknown): number {
  const code = error instanceof ContentReviewError
    ? error.code
    : error instanceof ContentCatalogError
      ? error.code
      : "";
  if (code === "library_item_not_found") return 404;
  if (
    code === "content_review_idempotency_conflict"
    || code === "mock_content_cannot_be_approved"
    || code === "content_not_awaiting_review"
  ) return 409;
  if (code === "invalid_content_review" || code === "invalid_library_item_id") return 400;
  return 502;
}
