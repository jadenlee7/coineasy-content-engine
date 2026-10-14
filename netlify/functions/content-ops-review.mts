import { createContentOpsReviewHandler, type ContentOpsReviewRuntime } from "./_shared/content-ops-review.mts";
import { createTrialManifestResolver, createTrialDeliveryConfirmation } from "./_shared/content-ops-trial-resolver.mts";
import { currentStudioReleaseSha } from "./_shared/studio-release.mts";

// Dedicated courier credential, not a Studio session or shared admin API key.
export default (req: Request, runtime?: ContentOpsReviewRuntime) => {
  const getEnv = (name: string) => Netlify.env.get(name);
  const releaseSha = () => currentStudioReleaseSha();
  // No configuration/secret read on import, and no resolver on ordinary routes.
  // Creation itself performs no HTTP and reads only the explicit trial flag.
  const trialResolver = req.headers.get("x-content-ops-mode") === "trial"
    ? createTrialManifestResolver({ getEnv, releaseSha }) : undefined;
  const trialDeliveryConfirm = trialResolver ? createTrialDeliveryConfirmation({ getEnv, releaseSha }) : undefined;
  return createContentOpsReviewHandler({ getEnv, releaseSha, trialResolver, trialDeliveryConfirm })(req, runtime);
};
