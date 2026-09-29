import { createContentOpsReviewReleaseHandler } from "./_shared/content-ops-review-release.mts";
import { currentStudioReleaseSha } from "./_shared/studio-release.mts";

export default createContentOpsReviewReleaseHandler({
  getEnv: (name) => Netlify.env.get(name),
  releaseSha: () => currentStudioReleaseSha(),
});
