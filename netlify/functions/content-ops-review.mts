import { createContentOpsReviewHandler } from "./_shared/content-ops-review.mts";
import { currentStudioReleaseSha } from "./_shared/studio-release.mts";

// Dedicated courier credential, not a Studio session or shared admin API key.
export default createContentOpsReviewHandler({
  getEnv: (name) => Netlify.env.get(name),
  releaseSha: () => currentStudioReleaseSha(),
});
