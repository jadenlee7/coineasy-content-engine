import type { Config } from "@netlify/functions";
import {
  batchReviewConfig,
  BatchReviewError,
  listBatchReviewInbox,
  MAX_BATCH_REVIEW_LIMIT,
} from "./_shared/batch-review.mts";
import { requireStudioSession, studioSessionJson } from "./_shared/studio-session.mts";

export default async (req: Request): Promise<Response> => {
  if (req.method !== "GET") {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "GET" });
  }

  const accessError = requireStudioSession(req);
  if (accessError) return accessError;

  const config = batchReviewConfig((name) => Netlify.env.get(name));
  if (!config) return studioSessionJson({ error: "batch_review_not_configured" }, 503);

  const url = new URL(req.url);
  const limitRaw = url.searchParams.get("limit");
  const limit = limitRaw === null || limitRaw === "" ? 24 : Number(limitRaw);
  const beforeFinishedAt = url.searchParams.get("before_finished_at");
  const beforeJobId = url.searchParams.get("before_job_id");
  if (
    !Number.isSafeInteger(limit)
    || limit < 1
    || limit > MAX_BATCH_REVIEW_LIMIT
  ) return studioSessionJson({ error: "invalid_batch_review_filters" }, 400);

  try {
    const page = await listBatchReviewInbox(config, {
      limit,
      beforeFinishedAt,
      beforeJobId,
    });
    return studioSessionJson(page, 200);
  } catch (error) {
    const code = error instanceof BatchReviewError
      ? error.code
      : "batch_review_unavailable";
    const status = code === "invalid_batch_review_filters" ? 400 : 502;
    return studioSessionJson({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/batch-review",
};
