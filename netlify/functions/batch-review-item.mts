import type { Config, Context } from "@netlify/functions";
import {
  batchReviewConfig,
  BatchReviewError,
  getBatchReviewItem,
} from "./_shared/batch-review.mts";
import { isCatalogUuid } from "./_shared/content-catalog.mts";
import { getOriginTrailArchivedReview } from "./_shared/origintrail-archived-review.mts";
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

  const archived = getOriginTrailArchivedReview(jobId);
  if (archived) return studioSessionJson(archived, 200);

  const config = batchReviewConfig((name) => Netlify.env.get(name));
  if (!config) return studioSessionJson({ error: "batch_review_not_configured" }, 503);

  try {
    const item = await getBatchReviewItem(config, jobId);
    if (!item) return studioSessionJson({ error: "batch_review_item_not_found" }, 404);
    return studioSessionJson(item, 200);
  } catch (error) {
    const code = error instanceof BatchReviewError
      ? error.code
      : "batch_review_unavailable";
    const status = code === "invalid_batch_review_item_id" ? 400 : 502;
    return studioSessionJson({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/batch-review/:jobId",
};
