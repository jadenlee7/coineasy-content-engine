import type { Config } from "@netlify/functions";

import {
  batchReviewConfig,
  BatchReviewError,
  listBatchReviewInbox,
  MAX_BATCH_REVIEW_LIMIT,
} from "./_shared/batch-review.mts";
import {
  buzzShadowAccessConfigured,
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
    const page = await listBatchReviewInbox(config, {
      limit,
      beforeFinishedAt,
      beforeJobId,
    });
    return json(projectBuzzShadowPage(page, config.workspaceId));
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
