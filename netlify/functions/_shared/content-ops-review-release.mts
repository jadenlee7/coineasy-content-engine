import type { Context } from "@netlify/functions";
import { configuredContentOpsGatewayToken, hasContentOpsGatewayAccess } from "./content-ops-gateway-auth.mts";

export const CONTENT_OPS_RELEASE_PATH = "/.netlify/functions/content-ops-review-release";
const ORIGIN = "https://coineasy-newscard.netlify.app";
const SHA = /^[a-f0-9]{40}$/;
export type ContentOpsReleaseRuntime = Readonly<Pick<Context, "deploy">>;
type Dependencies = {
  getEnv: (name: string) => string | undefined;
  releaseSha: () => string | null;
};

function json(body: Record<string, unknown>, status = 200): Response {
  return Response.json(body, { status, headers: {
    "Cache-Control": "no-store",
    "CDN-Cache-Control": "no-store",
    "Netlify-CDN-Cache-Control": "no-store",
    "Vary": "Authorization",
    "X-Content-Type-Options": "nosniff",
  } });
}

// This route has no catalog, RPC, fetcher, provider or sender dependency.
// It attests this Netlify function's build stamp and OFF flags, not the
// Railway runtime, callback owner, database health or canary readiness.
export function createContentOpsReviewReleaseHandler(deps: Dependencies) {
  return async (req: Request, runtime?: ContentOpsReleaseRuntime): Promise<Response> => {
    if (req.method !== "GET") return json({ error: "method_not_allowed" }, 405);
    try {
      const url = new URL(req.url);
      // Only Netlify's native second handler argument supplies deployment context.
      // CONTEXT is a build variable, not guaranteed in Functions at runtime.
      const deploy = runtime?.deploy;
      if (url.origin !== ORIGIN || url.pathname !== CONTENT_OPS_RELEASE_PATH
        || deploy?.context !== "production" || deploy.published !== true
        || typeof deploy.id !== "string" || !deploy.id.trim()
        || deploy.id !== deploy.id.trim()) {
        return json({ error: "content_ops_production_host_required" }, 421);
      }
      const token = configuredContentOpsGatewayToken(deps.getEnv);
      if (!token) return json({ error: "content_ops_token_not_configured" }, 503);
      if (!hasContentOpsGatewayAccess(req, token)) return json({ error: "invalid_token" }, 401);
      // No actions, IDs, URLs, request body or caller-selected scope are accepted.
      if (url.search || req.body !== null
        || ![null, "0"].includes(req.headers.get("content-length"))) {
        return json({ error: "invalid_request" }, 400);
      }
      const release = deps.releaseSha();
      if (!release || !SHA.test(release)
        || deps.getEnv("CONTENT_OPS_REVIEW_RELEASE_SHA") !== release
        || req.headers.get("x-content-ops-expected-release-sha") !== release) {
        return json({ error: "content_ops_release_mismatch" }, 503);
      }
      // Explicit false is required: missing/invalid flags are not proof of OFF.
      if (deps.getEnv("CONTENT_OPS_GATEWAY_ENABLED") !== "false"
        || deps.getEnv("CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED") !== "false"
        || deps.getEnv("STUDIO_TELEGRAM_PUBLISH_ENABLED") !== "false") {
        return json({ error: "content_ops_off_state_required" }, 503);
      }
      return json({
        schema_version: "content-ops-release-readback@1",
        ok: true,
        netlify_release_sha: release,
        gateway_enabled: false,
        button_gateway_enabled: false,
        public_telegram_publish_enabled: false,
      });
    } catch {
      // Never reflect config values, tokens, errors or provider responses.
      return json({ error: "content_ops_release_unavailable" }, 503);
    }
  };
}
