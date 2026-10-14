/** Config and existing upstream credential proof before daily-slot admission.
 * One bounded API configuration GET only; no owner, database, provider or send. */
import type { Context } from "@netlify/functions";
import { constantTimeAccessCodeMatch } from "./studio-session.mts";
import { READY_ANCHORED_TRIAL_ID, configuredTrialWindow, trialWindowUnchanged, withinTrialWindow } from "./content-ops-trial-window.mts";

export const YBO_TRIAL_PREFLIGHT_PATH = "/api/ybo-trial-preflight";
export const YBO_TRIAL_REQUIRED_OFF_FLAGS = [
  "QUALITY_BANNER_ENABLED", "NEWS_CARD_IDEMPOTENCY_ENABLED", "CONTENT_OPS_GATEWAY_ENABLED",
  "CONTENT_OPS_REVIEW_ENABLED", "CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED",
  "CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED", "STUDIO_TELEGRAM_PUBLISH_ENABLED",
] as const;
const ORIGIN = "https://coineasy-newscard.netlify.app";
const API = "https://coineasy-content-engine-production.up.railway.app";
type Runtime = Readonly<Pick<Context, "deploy">>;
type Dependencies = { getEnv: (name: string) => string | undefined; releaseSha: () => string | null;
  clock?: () => number; fetcher?: typeof fetch };
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
      const bounds = configuredTrialWindow(deps.getEnv, "YBO_TRIAL");
      if (!release || !/^[a-f0-9]{40}$/.test(release)
        || req.headers.get("x-studio-expected-release-sha") !== release
        || deps.getEnv("YBO_TRIAL_RELEASE_SHA") !== release
        || trialId !== READY_ANCHORED_TRIAL_ID || deps.getEnv("YBO_TRIAL_ID") !== trialId
        || !plan || !/^[a-f0-9]{64}$/.test(plan) || deps.getEnv("YBO_TRIAL_PLAN_SHA256") !== plan
        || !bounds || !withinTrialWindow(bounds, now)
        || deps.getEnv("YBO_TRIAL_GENERATION_GATEWAY_ENABLED") !== "true"
        || !YBO_TRIAL_REQUIRED_OFF_FLAGS.every(key => deps.getEnv(key) === "false")) {
        return json({ error: "trial_preflight_blocked" }, 503);
      }
      const apiSecret = deps.getEnv("API_SECRET"), apiOrigin = deps.getEnv("RAILWAY_API_URL");
      if (typeof apiSecret !== "string" || apiSecret !== apiSecret.trim()
        || apiSecret.length < 16 || apiSecret.length > 4096 || /[\r\n]/.test(apiSecret)
        || typeof apiOrigin !== "string" || apiOrigin.replace(/\/+$/, "") !== API) {
        return json({ error: "trial_preflight_blocked" }, 503);
      }
      // Exact ON runtime proof prevents an old OFF container from satisfying
      // startup readiness. This endpoint never opens an owner or reserves work.
      const response = await (deps.fetcher ?? fetch)(API + "/internal/trial/readiness", {
        method: "GET", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(4000),
        headers: { "X-API-Key": apiSecret, "Accept": "application/json", "Accept-Encoding": "identity" },
      });
      if (response.status !== 200 || response.redirected
        || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json"
        || (response.headers.get("content-encoding") ?? "identity") !== "identity"
        || response.headers.has("content-range")) throw Error();
      const length = response.headers.get("content-length"), limit = 16_384;
      if (length !== null && (!/^[0-9]+$/.test(length) || Number(length) > limit)) throw Error();
      const reader = response.body?.getReader(); if (!reader) throw Error();
      const chunks: Uint8Array[] = []; let size = 0;
      try {
        for (;;) {
          const next = await reader.read(); if (next.done) break;
          size += next.value.length;
          if (size > limit) { await reader.cancel(); throw Error(); }
          chunks.push(next.value);
        }
      } finally { reader.releaseLock(); }
      if (!size || (length !== null && Number(length) !== size)) throw Error();
      const proof = JSON.parse(Buffer.concat(chunks, size).toString("utf8"));
      const observed = (deps.clock ?? Date.now)();
      if (!proof || typeof proof !== "object" || Array.isArray(proof)
        || Object.keys(proof).sort().join(",") !== "broad_public_flags_off,client_ids,controller_enabled,deadline,kst_date,not_before,observed_at,plan_sha256,ready,release_sha,schema_version,trial_id"
        || proof.schema_version !== "ybo-api-readiness@1" || proof.ready !== true
        || proof.trial_id !== trialId || proof.plan_sha256 !== plan || proof.release_sha !== release
        || proof.not_before !== bounds.notBefore || proof.deadline !== bounds.deadline || proof.kst_date !== bounds.kstDate
        || proof.controller_enabled !== true || proof.broad_public_flags_off !== true
        || JSON.stringify(proof.client_ids) !== '["yellow","babylon","origintrail"]'
        || typeof proof.observed_at !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(proof.observed_at)
        || !Number.isFinite(Date.parse(proof.observed_at))
        || observed - Date.parse(proof.observed_at) < 0 || observed - Date.parse(proof.observed_at) > 5000) {
        throw Error();
      }
      if (observed < now || !withinTrialWindow(bounds, observed) || !trialWindowUnchanged(deps.getEnv, "YBO_TRIAL", bounds)
        || deps.releaseSha() !== release || deps.getEnv("YBO_TRIAL_RELEASE_SHA") !== release
        || deps.getEnv("YBO_TRIAL_ID") !== trialId || deps.getEnv("YBO_TRIAL_PLAN_SHA256") !== plan
        || deps.getEnv("YBO_TRIAL_GENERATION_GATEWAY_ENABLED") !== "true"
        || !YBO_TRIAL_REQUIRED_OFF_FLAGS.every(key => deps.getEnv(key) === "false")
        || deps.getEnv("API_SECRET") !== apiSecret || deps.getEnv("RAILWAY_API_URL") !== apiOrigin) throw Error();
      return json({ schema_version: "ybo-trial-preflight@1", ready: true,
        netlify_release_sha: release, trial_id: trialId, plan_sha256: plan,
        not_before: bounds.notBefore, deadline: bounds.deadline, kst_date: bounds.kstDate,
        generation_gateway_enabled: true, broad_public_flags_off: true,
        upstream_api_auth_verified: true, observed_at: new Date(observed).toISOString() });
    } catch { return json({ error: "trial_preflight_unavailable" }, 503); }
  };
}
