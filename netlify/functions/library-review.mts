import type { Config, Context } from "@netlify/functions";
import {
  contentCatalogConfig,
  getContentLibraryItem,
  isCatalogUuid,
} from "./_shared/content-catalog.mts";
import {
  brandReviewGuidanceAudit,
  contentReviewStatus,
  ContentReviewError,
  FACT_CHECK_POLICY_VERSION,
  getBrandReviewGuidance,
  getContentReviewSummary,
  recordStudioContentReview,
  REVIEW_REASON_CODES,
  type ContentReviewSummary,
  type ReviewReasonCode,
  type StudioFactCheckAttestation,
  type StudioReviewDecision,
} from "./_shared/content-reviews.mts";
import { requireStudioSession, studioSessionJson } from "./_shared/studio-session.mts";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const REASONS = new Set<string>(REVIEW_REASON_CODES);

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "POST" });
  }
  const accessError = requireStudioSession(req);
  if (accessError) return accessError;

  const contentItemId = context.params.contentId;
  if (!isCatalogUuid(contentItemId)) {
    return studioSessionJson({ error: "invalid_library_item_id" }, 400);
  }
  const idempotencyKey = (req.headers.get("idempotency-key") || "").trim().toLowerCase();
  if (!UUID_PATTERN.test(idempotencyKey)) {
    return studioSessionJson({ error: "invalid_content_review_idempotency_key" }, 400);
  }
  const config = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!config) {
    return studioSessionJson({ error: "content_catalog_not_configured" }, 503);
  }

  let body: Record<string, unknown> | null;
  try {
    body = objectValue(await req.json());
  } catch {
    return studioSessionJson({ error: "invalid_json" }, 400);
  }
  if (
    !body
    || Object.keys(body).some((key) => ![
      "content_version_id",
      "decision",
      "reason_codes",
      "comment",
      "fact_check",
    ].includes(key))
  ) return studioSessionJson({ error: "invalid_content_review" }, 400);

  const contentVersionId = typeof body.content_version_id === "string"
    ? body.content_version_id.toLowerCase()
    : "";
  const decision = body.decision;
  const rawReasonCodes = body.reason_codes ?? [];
  const comment = typeof body.comment === "string" ? body.comment.trim() : "";
  const rawFactCheck = body.fact_check;
  const factCheckValue = rawFactCheck === undefined || rawFactCheck === null
    ? null
    : objectValue(rawFactCheck);
  const factCheck = factCheckValue && (
    Object.keys(factCheckValue).length === 3
    && factCheckValue.policy_version === FACT_CHECK_POLICY_VERSION
    && typeof factCheckValue.source_facts_verified === "boolean"
    && typeof factCheckValue.output_claims_verified === "boolean"
  )
    ? {
      policyVersion: FACT_CHECK_POLICY_VERSION,
      sourceFactsVerified: factCheckValue.source_facts_verified,
      outputClaimsVerified: factCheckValue.output_claims_verified,
    } satisfies StudioFactCheckAttestation
    : null;
  const invalidFactCheck = (rawFactCheck !== undefined && rawFactCheck !== null && !factCheck)
    || (decision === "approved" && (!factCheck
      || !factCheck.sourceFactsVerified
      || !factCheck.outputClaimsVerified));
  if (invalidFactCheck) {
    return studioSessionJson({ error: "invalid_fact_check_attestation" }, 400);
  }
  if (
    !UUID_PATTERN.test(contentVersionId)
    || !["approved", "rejected"].includes(String(decision))
    || !Array.isArray(rawReasonCodes)
    || rawReasonCodes.length > 5
    || new Set(rawReasonCodes).size !== rawReasonCodes.length
    || rawReasonCodes.some((code) => typeof code !== "string" || !REASONS.has(code))
    || comment.length > 1000
    || (decision === "approved" && rawReasonCodes.length > 0)
    || (decision === "rejected" && rawReasonCodes.length === 0 && !comment)
  ) return studioSessionJson({ error: "invalid_content_review" }, 400);

  try {
    const before = await getContentLibraryItem(config, contentItemId);
    if (!before) return studioSessionJson({ error: "library_item_not_found" }, 404);
    if (before.current_version_id !== contentVersionId) {
      return studioSessionJson({ error: "content_review_version_conflict" }, 409);
    }
    const result = await recordStudioContentReview(config, contentItemId, {
      contentVersionId,
      decision: decision as StudioReviewDecision,
      reasonCodes: rawReasonCodes as ReviewReasonCode[],
      comment: comment || null,
      factCheck,
      idempotencyKey,
    });
    let latestReview: ContentReviewSummary = {
      approval_id: result.approval_id,
      content_version_id: result.content_version_id,
      decision: result.decision,
      reason_codes: result.reason_codes,
      comment: comment || null,
      reviewer_source: "studio_session",
      created_at: result.created_at,
      fact_check_policy_version: result.fact_check_policy_version,
      source_facts_verified: result.source_facts_verified,
      output_claims_verified: result.output_claims_verified,
    };
    try {
      latestReview = await getContentReviewSummary(config, contentItemId) || latestReview;
    } catch {
      // The decision is already committed. A summary projection failure must not
      // turn an idempotent success into a misleading retryable error.
    }
    let brandLearning: Record<string, unknown> = { available: false };
    try {
      const guidance = await getBrandReviewGuidance(
        config,
        before.client_id,
        before.content_kind,
      );
      brandLearning = {
        ...brandReviewGuidanceAudit(guidance),
        available: true,
      };
    } catch {
      // The next generation can fetch guidance independently.
    }
    return studioSessionJson({
      result,
      latest_review: latestReview,
      brand_learning: brandLearning,
    }, result.reused ? 200 : 201);
  } catch (error) {
    const code = error instanceof ContentReviewError
      ? error.code
      : "content_review_storage_unavailable";
    return studioSessionJson({ error: code }, contentReviewStatus(error));
  }
};

export const config: Config = {
  path: "/api/library/:contentId/review",
};
