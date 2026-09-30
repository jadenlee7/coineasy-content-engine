import type { Context } from "@netlify/functions";
import { createContentOpsReviewReleaseHandler } from "./_shared/content-ops-review-release.mts";
import { currentStudioReleaseSha } from "./_shared/studio-release.mts";

const handler = createContentOpsReviewReleaseHandler({
  getEnv: (name) => Netlify.env.get(name),
  releaseSha: () => currentStudioReleaseSha(),
});

export default (req: Request, context?: Context): Promise<Response> => handler(req, context);
