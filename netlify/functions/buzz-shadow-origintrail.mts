import type { Config } from "@netlify/functions";

import {
  batchReviewConfig,
  BatchReviewError,
  getBatchReviewItem,
  listBatchReviewInbox,
  MAX_BATCH_REVIEW_LIMIT,
} from "./_shared/batch-review.mts";
import {
  buzzShadowAccessConfigured,
  buzzResultPreviewStartAt,
  type BuzzShadowPreview,
  BuzzShadowError,
  hasValidBuzzShadowAccess,
  projectBuzzShadowPage,
} from "./_shared/buzz-shadow.mts";

function json(body: unknown, status = 200, extraHeaders: HeadersInit = {}): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
      "Vary": "x-coineasy-buzz-key",
      "X-Content-Type-Options": "nosniff",
      ...extraHeaders,
    },
  });
}

export default async (request: Request): Promise<Response> => {
  if (request.method !== "GET") {
    return json({ error: "method_not_allowed" }, 405, { Allow: "GET" });
  }

  const getEnv = (name: string) => Netlify.env.get(name);
  if (!buzzShadowAccessConfigured(getEnv)) {
    return json({ error: "buzz_shadow_not_configured" }, 503);
  }
  if (!hasValidBuzzShadowAccess(request, getEnv)) {
    return json({ error: "buzz_shadow_auth_required" }, 401);
  }

  const config = batchReviewConfig(getEnv);
  if (!config) return json({ error: "buzz_shadow_storage_not_configured" }, 503);
  // Adoption path for the read-only `coineasy_batch_reviewer` role (ADR-007):
  // the scoped JWT becomes the RPC bearer without replacing the project API
  // key used by PostgREST. Unset keeps the legacy service-role bearer.
  const scopedKey = (getEnv("SUPABASE_BUZZ_SHADOW_KEY") || "").trim();
  const effectiveConfig = scopedKey
    ? { ...config, authorizationKey: scopedKey }
    : config;
  const previewStartAt = buzzResultPreviewStartAt(getEnv);
  if (previewStartAt === null) {
    return json({ error: "buzz_shadow_preview_not_configured" }, 503);
  }

  const url = new URL(request.url);
  const limitRaw = url.searchParams.get("limit");
  const limit = limitRaw === null || limitRaw === "" ? 20 : Number(limitRaw);
  const beforeFinishedAt = url.searchParams.get("before_finished_at");
  const beforeJobId = url.searchParams.get("before_job_id");
  if (
    !Number.isSafeInteger(limit)
    || limit < 1
    || limit > MAX_BATCH_REVIEW_LIMIT
  ) return json({ error: "invalid_buzz_shadow_filters" }, 400);

  try {
    const page = await listBatchReviewInbox(effectiveConfig, {
      limit,
      beforeFinishedAt,
      beforeJobId,
    });
    const eligibleItems = page.items.filter(
      (item) => Date.parse(item.finished_at) >= previewStartAt,
    );
    const details = await Promise.all(
      eligibleItems.map((item) => getBatchReviewItem(effectiveConfig, item.job_id)),
    );
    const previews = new Map<string, BuzzShadowPreview>();
    for (let index = 0; index < eligibleItems.length; index += 1) {
      const detail = details[index];
      if (!detail || detail.job_id !== eligibleItems[index].job_id) {
        throw new BuzzShadowError("buzz_shadow_invalid_review_page");
      }
      previews.set(detail.job_id, {
        headline_ko: detail.result_payload.headline_ko.trim(),
        summary_ko: detail.result_payload.telegram_copy_ko.trim(),
      });
    }
    return json(projectBuzzShadowPage({
      items: eligibleItems,
      next_cursor: page.next_cursor,
    }, config.workspaceId, previews));
  } catch (error) {
    const code = error instanceof BatchReviewError || error instanceof BuzzShadowError
      ? error.code
      : "buzz_shadow_unavailable";
    const status = code === "invalid_batch_review_filters" ? 400 : 502;
    return json({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/buzz-shadow/origintrail/batch",
};
