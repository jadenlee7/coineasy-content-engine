import type { Config, Context } from "@netlify/functions";

import {
  batchReviewConfig,
  BatchReviewError,
  getBatchReviewItem,
} from "./_shared/batch-review.mts";
import {
  buzzResultPreviewStartAt,
  buzzShadowAccessConfigured,
  hasValidBuzzShadowAccess,
} from "./_shared/buzz-shadow.mts";
import { isCatalogUuid } from "./_shared/content-catalog.mts";
import { getOriginTrailArchivedReview } from "./_shared/origintrail-archived-review.mts";
import {
  originTrailBatchBannerResponse,
  OriginTrailBatchBannerError,
  renderOriginTrailBatchBanner,
} from "./_shared/origintrail-batch-banner.mts";

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

export default async (request: Request, context: Context): Promise<Response> => {
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

  const jobId = context.params.jobId;
  if (!isCatalogUuid(jobId)) {
    return json({ error: "invalid_buzz_shadow_banner_id" }, 400);
  }
  const previewStartAt = buzzResultPreviewStartAt(getEnv);
  if (previewStartAt === null) {
    return json({ error: "buzz_shadow_preview_not_configured" }, 503);
  }

  try {
    const archived = getOriginTrailArchivedReview(jobId);
    const config = archived ? null : batchReviewConfig(getEnv);
    if (!archived && !config) {
      return json({ error: "buzz_shadow_storage_not_configured" }, 503);
    }
    const item = archived || await getBatchReviewItem(config!, jobId);
    if (!item || (!archived && Date.parse(item.finished_at) < previewStartAt)) {
      return json({ error: "buzz_shadow_banner_not_found" }, 404);
    }
    const banner = await renderOriginTrailBatchBanner(item, context.site.url);
    const response = originTrailBatchBannerResponse(banner, item.job_id);
    response.headers.set("Vary", "x-coineasy-buzz-key");
    return response;
  } catch (error) {
    const code = error instanceof BatchReviewError || error instanceof OriginTrailBatchBannerError
      ? error.code
      : "buzz_shadow_banner_unavailable";
    const status = code === "origintrail_batch_banner_evidence_required" ? 409 : 502;
    return json({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/buzz-shadow/origintrail/batch/:jobId/banner.png",
};
