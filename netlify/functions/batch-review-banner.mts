import type { Config, Context } from "@netlify/functions";

import {
  batchReviewConfig,
  BatchReviewError,
  getBatchReviewItem,
} from "./_shared/batch-review.mts";
import { isCatalogUuid } from "./_shared/content-catalog.mts";
import { getOriginTrailArchivedReview } from "./_shared/origintrail-archived-review.mts";
import {
  originTrailBatchBannerResponse,
  OriginTrailBatchBannerError,
  renderOriginTrailBatchBanner,
} from "./_shared/origintrail-batch-banner.mts";
import { requireStudioSession, studioSessionJson } from "./_shared/studio-session.mts";

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "GET") {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "GET" });
  }

  const accessError = requireStudioSession(req);
  if (accessError) return accessError;

  const jobId = context.params.jobId;
  if (!isCatalogUuid(jobId)) {
    return studioSessionJson({ error: "invalid_batch_review_item_id" }, 400);
  }

  try {
    const archived = getOriginTrailArchivedReview(jobId);
    const config = archived ? null : batchReviewConfig((name) => Netlify.env.get(name));
    if (!archived && !config) {
      return studioSessionJson({ error: "batch_review_not_configured" }, 503);
    }
    const item = archived || await getBatchReviewItem(config!, jobId);
    if (!item) return studioSessionJson({ error: "batch_review_item_not_found" }, 404);
    const banner = await renderOriginTrailBatchBanner(item, context.site.url);
    return originTrailBatchBannerResponse(banner, item.job_id);
  } catch (error) {
    const code = error instanceof BatchReviewError || error instanceof OriginTrailBatchBannerError
      ? error.code
      : "origintrail_batch_banner_unavailable";
    const status = code === "origintrail_batch_banner_evidence_required" ? 409 : 502;
    return studioSessionJson({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/batch-review/:jobId/banner.png",
};
