import type { ContentOpsReviewRuntime } from "./_shared/content-ops-review.mts";
import { createTrialStateHandler } from "./_shared/content-ops-trial-state.mts";
import { createTrialManifestResolver } from "./_shared/content-ops-trial-resolver.mts";
import { currentStudioReleaseSha } from "./_shared/studio-release.mts";
export default (req: Request, runtime?: ContentOpsReviewRuntime) => {
  const getEnv = (name: string) => Netlify.env.get(name), releaseSha = () => currentStudioReleaseSha();
  return createTrialStateHandler({ getEnv, releaseSha,
    resolver: createTrialManifestResolver({ getEnv, releaseSha }) })(req, runtime);
};
