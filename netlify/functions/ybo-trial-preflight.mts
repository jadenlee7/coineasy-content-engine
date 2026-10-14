import type { Config, Context } from "@netlify/functions";
import { currentStudioReleaseSha } from "./_shared/studio-release.mts";
import { createYboTrialPreflightHandler } from "./_shared/ybo-trial-preflight.mts";

const handler = createYboTrialPreflightHandler({
  getEnv: name => Netlify.env.get(name), releaseSha: () => currentStudioReleaseSha(),
});
export default (req: Request, context?: Context): Promise<Response> => handler(req, context);
export const config: Config = { path: "/api/ybo-trial-preflight" };
