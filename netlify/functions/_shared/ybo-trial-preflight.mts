/** Pure runtime-config proof before a hosted trial can reserve any daily slot.
 * No owner, catalog, database, provider, fetcher or sender dependency exists. */
import type { Context } from "@netlify/functions";
import { constantTimeAccessCodeMatch } from "./studio-session.mts";

export const YBO_TRIAL_PREFLIGHT_PATH = "/api/ybo-trial-preflight";
export const YBO_TRIAL_REQUIRED_OFF_FLAGS = [
  "QUALITY_BANNER_ENABLED", "NEWS_CARD_IDEMPOTENCY_ENABLED", "CONTENT_OPS_GATEWAY_ENABLED",
  "CONTENT_OPS_REVIEW_ENABLED", "CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED",
  "CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED", "STUDIO_TELEGRAM_PUBLISH_ENABLED",
] as const;
const ORIGIN = "https://coineasy-newscard.netlify.app";
const API = "https://coineasy-content-engine-production.up.railway.app";
const START = Date.parse("2026-10-14T12:44:51Z"), END = Date.parse("2026-10-14T14:44:51Z");
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
type Runtime = Readonly<Pick<Context, "deploy">>;
type Dependencies = { getEnv: (name: string) => string | undefined; releaseSha: () => string | null;
  clock?: () => number };
const json = (body: unknown, status = 200) => Response.json(body, { status, headers: {
  "Cache-Control": "no-store", "CDN-Cache-Control": "no-store", "Netlify-CDN-Cache-Control": "no-store",
  "Vary": "X-Studio-Automation-Key", "X-Content-Type-Options": "nosniff",
} });

export function createYboTrialPreflightHandler(deps: Dependencies) {
  return async (req: Request, runtime?: Runtime): Promise<Response> => {
    if (req.method !== "GET") return json({ error: "method_not_allowed" }, 405);
    try {
      const url = new URL(req.url), deploy = runtime?.deploy;
      if (url.origin !== ORIGIN || url.pathname !== YBO_TRIAL_PREFLIGHT_PATH
        || deploy?.context !== "production" || deploy.published !== true
        || typeof deploy.id !== "string" || !deploy.id.trim() || deploy.id !== deploy.id.trim()) {
        return json({ error: "trial_production_host_required" }, 421);
      }
      const token = deps.getEnv("STUDIO_AUTOMATION_TOKEN")?.trim() ?? "";
      const candidate = (req.headers.get("x-studio-automation-key") ?? "").trim();
      if (token.length < 32 || token.length > 512) return json({ error: "trial_preflight_unavailable" }, 503);
      if (candidate.length > 512 || !constantTimeAccessCodeMatch(candidate, token)) {
        return json({ error: "studio_automation_auth_required" }, 401);
      }
      if (url.search || url.hash || req.body !== null
        || ![null, "0"].includes(req.headers.get("content-length"))) return json({ error: "invalid_request" }, 400);
      const release = deps.releaseSha(), trialId = req.headers.get("x-ybo-trial-id");
      const plan = req.headers.get("x-ybo-trial-plan-sha256");
      const now = (deps.clock ?? Date.now)();
      if (!release || !/^[a-f0-9]{40}$/.test(release)
        || req.headers.get("x-studio-expected-release-sha") !== release
        || deps.getEnv("YBO_TRIAL_RELEASE_SHA") !== release
        || !trialId || !UUID.test(trialId) || deps.getEnv("YBO_TRIAL_ID") !== trialId
        || !plan || !/^[a-f0-9]{64}$/.test(plan) || deps.getEnv("YBO_TRIAL_PLAN_SHA256") !== plan
        || !Number.isFinite(now) || !(START <= now && now < END)
        || deps.getEnv("YBO_TRIAL_GENERATION_GATEWAY_ENABLED") !== "true"
        || !YBO_TRIAL_REQUIRED_OFF_FLAGS.every(key => deps.getEnv(key) === "false")
        || !(deps.getEnv("API_SECRET")?.trim())
        || (deps.getEnv("RAILWAY_API_URL") || API).replace(/\/+$/, "") !== API) {
        return json({ error: "trial_preflight_blocked" }, 503);
      }
      return json({ schema_version: "ybo-trial-preflight@1", ready: true,
        netlify_release_sha: release, trial_id: trialId, plan_sha256: plan,
        generation_gateway_enabled: true, broad_public_flags_off: true,
        observed_at: new Date(now).toISOString() });
    } catch { return json({ error: "trial_preflight_unavailable" }, 503); }
  };
}
