import type { ContentOpsReviewRuntime } from "./content-ops-review.mts";
import { configuredContentOpsGatewayToken, hasContentOpsGatewayAccess } from "./content-ops-gateway-auth.mts";
import { admitTrial, trialFlagsOff, type TrustedTrialResolver } from "./content-ops-trial.mts";
import { TrialManifestNotReady } from "./content-ops-trial-resolver.mts";
type Env = (key: string) => string | undefined;
const PATH = "/.netlify/functions/content-ops-trial-state", ORIGIN = "https://coineasy-newscard.netlify.app";
const json = (body: unknown, status: number, release?: string) => Response.json(body, { status,
  headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
    ...(release ? { "X-Content-Ops-Release-Sha": release } : {}) } });
export function createTrialStateHandler(deps: { getEnv: Env; releaseSha: () => string | null;
  resolver?: TrustedTrialResolver; clock?: () => number }) {
  return async (req: Request, runtime?: ContentOpsReviewRuntime) => {
    if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);
    if (deps.getEnv("CONTENT_OPS_TRIAL_GATEWAY_ENABLED") !== "true" || !trialFlagsOff(deps.getEnv)) {
      return json({ error: "content_ops_disabled" }, 503);
    }
    try {
      const url = new URL(req.url), deploy = runtime?.deploy;
      if (url.origin !== ORIGIN || url.pathname !== PATH || deploy?.context !== "production"
        || deploy.published !== true || typeof deploy.id !== "string" || !deploy.id.trim()
        || deploy.id !== deploy.id.trim()) return json({ error: "content_ops_production_host_required" }, 421);
    } catch { return json({ error: "content_ops_production_host_required" }, 421); }
    const release = deps.releaseSha();
    if (!release || !/^[a-f0-9]{40}$/.test(release) || deps.getEnv("CONTENT_OPS_REVIEW_RELEASE_SHA") !== release
      || req.headers.get("x-content-ops-expected-release-sha") !== release) {
      return json({ error: "content_ops_release_mismatch" }, 503);
    }
    const token = configuredContentOpsGatewayToken(deps.getEnv);
    if (!token || !hasContentOpsGatewayAccess(req, token)) return json({ error: "invalid_token" }, 401);
    try {
      const now = (deps.clock ?? Date.now)();
      if (!(Date.parse("2026-10-14T12:44:51Z") <= now && now < Date.parse("2026-10-14T14:44:51Z"))) throw Error();
      if (!/^application\/json(?:\s*;|$)/i.test(req.headers.get("content-type") ?? "") || !req.body) throw Error();
      const reader = req.body.getReader(); let bytes = 0; const chunks: Uint8Array[] = [];
      try { for (;;) { const r = await reader.read(); if (r.done) break;
        bytes += r.value.length; if (bytes > 1024) { await reader.cancel(); throw Error(); } chunks.push(r.value);
      } } finally { reader.releaseLock(); }
      const body = JSON.parse(Buffer.concat(chunks, bytes).toString("utf8"));
      if (!body || typeof body !== "object" || Array.isArray(body) || Object.keys(body).length !== 2
        || body.trial_id !== deps.getEnv("CONTENT_OPS_TRIAL_ID")
        || body.plan_sha256 !== deps.getEnv("CONTENT_OPS_TRIAL_PLAN_SHA256") || !deps.resolver) throw Error();
      let value: any;
      try { value = await deps.resolver(body); }
      catch (error) {
        if (error instanceof TrialManifestNotReady) return json({ error: "trial_manifest_not_ready" }, 409, release);
        throw error;
      }
      // Reuse the exact full manifest/freshness validator; no database or send.
      const probe = new Request(ORIGIN + PATH, { headers: {
        "x-content-ops-mode": "trial", "x-content-ops-packet-mode": "trial_delivery_only_v1",
        "x-content-ops-version-id": value?.manifest?.entries?.[0]?.content_version_id ?? "",
        "x-content-ops-trial-id": body.trial_id, "x-content-ops-trial-plan-sha256": body.plan_sha256,
        "x-content-ops-trial-manifest-sha256": value?.manifest_sha256 ?? "" } });
      await admitTrial(probe, deps.getEnv, release, deps.clock ?? Date.now, async () => value);
      return json(value, 200, release);
    } catch { return json({ error: "content_ops_trial_blocked" }, 503); }
  };
}
