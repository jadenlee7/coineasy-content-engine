/** Exact, default-OFF generation seam. A caller payload is never admission:
 * the existing API must verify its canonical digest against the active durable
 * owner before EVERY downstream HTTP call. No reserve/claim/enable occurs here. */
import { currentStudioReleaseSha } from "./studio-release.mts";

type Json = Record<string, any>;
type Env = (name: string) => string | undefined;
const START = Date.parse("2026-10-14T15:10:51Z"), END = Date.parse("2026-10-14T17:10:51Z");
const API = "https://coineasy-content-engine-production.up.railway.app";
const ORIGIN = "https://coineasy-newscard.netlify.app";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
const SHA = /^[a-f0-9]{64}$/;
const CLIENTS = ["yellow", "babylon", "origintrail"];
const PAYLOAD_KEYS = ["source_content", "source_type", "source_url", "source_image_url", "mock_mode",
  "template_style", "style_references", "style_reference_pack_hash", "brand_review_guidance", "quality_context"];
const record = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: Json, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
export class TrialGenerationError extends Error { constructor() { super("trial_generation_blocked"); } }
function requireTrue(v: unknown): asserts v { if (!v) throw new TrialGenerationError(); }
export function canonicalTrialPayload(v: any): string {
  if (Array.isArray(v)) return `[${v.map(canonicalTrialPayload).join(",")}]`;
  if (record(v)) return `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonicalTrialPayload(v[k])}`).join(",")}}`;
  return JSON.stringify(v);
}
export type TrialGenerationAdmission = {
  trialId: string; collectOnly: boolean; payload: Json; apiUrl: string; assertActive: () => Promise<void>; fetcher: typeof fetch;
};

export async function admitTrialGeneration(options: {
  req: Request; runtime: { deploy?: { context?: string; published?: boolean; id?: string } };
  body: Json; clientId: string; requestId: string; automation: boolean; env: Env;
  fetcher?: typeof fetch; clock?: () => number; release?: string | null;
}): Promise<TrialGenerationAdmission> {
  const { req, runtime, body, clientId, requestId, automation, env } = options;
  const fetcher = options.fetcher ?? fetch, clock = options.clock ?? Date.now;
  const release = options.release === undefined ? currentStudioReleaseSha() : options.release;
  const trialId = req.headers.get("x-ybo-trial-id"), plan = req.headers.get("x-ybo-trial-plan-sha256");
  const collectOnly = req.headers.get("x-ybo-trial-collect-only");
  const secret = env("API_SECRET"), configuredApi = (env("RAILWAY_API_URL") || API).replace(/\/+$/, "");
  const url = new URL(req.url), deploy = runtime?.deploy;
  requireTrue(automation && CLIENTS.includes(clientId) && UUID.test(requestId)
    && (collectOnly === null || collectOnly === "true")
    && trialId !== null && UUID.test(trialId) && plan !== null && SHA.test(plan)
    && typeof release === "string" && /^[a-f0-9]{40}$/.test(release)
    && req.headers.get("x-studio-expected-release-sha") === release
    && env("YBO_TRIAL_PLAN_SHA256") === plan && env("YBO_TRIAL_ID") === trialId
    && env("YBO_TRIAL_RELEASE_SHA") === release && configuredApi === API
    && typeof secret === "string" && secret.length > 0
    && url.origin === ORIGIN && url.pathname === `/api/news-card/${clientId}`
    && !url.search && !url.hash && deploy?.context === "production" && deploy.published === true
    && typeof deploy.id === "string" && deploy.id.trim().length > 0 && deploy.id === deploy.id.trim());
  const submitted = body.trial_payload;
  requireTrue(record(submitted) && exact(submitted, PAYLOAD_KEYS)
    && typeof submitted.source_content === "string" && submitted.source_content.length >= 10
    && submitted.source_content.length <= 20_000 && submitted.source_content === submitted.source_content.trim()
    && submitted.source_type === "tweet" && submitted.template_style === "remix" && submitted.mock_mode === false
    && submitted.brand_review_guidance === null && record(submitted.quality_context)
    && submitted.quality_context.client_id === clientId && submitted.quality_context.request_id === requestId
    && Array.isArray(submitted.style_references) && submitted.style_references.length <= 3
    && typeof submitted.style_reference_pack_hash === "string"
    && /^(?:|[a-f0-9]{32})$/.test(submitted.style_reference_pack_hash)
    && ["source_content", "source_type", "source_url", "source_image_url", "mock_mode", "template_style"]
      .every(k => body[k] === submitted[k])
    && canonicalTrialPayload(body.style_references ?? []) === canonicalTrialPayload(submitted.style_references)
    && (body.style_reference_pack_hash ?? "") === submitted.style_reference_pack_hash);
  // Preserve the exact authority-bound payload for the whole request. The
  // ordinary dynamic brand-guidance read must never alter its digest.
  const payload = JSON.parse(JSON.stringify(submitted));
  const authorizationBody = JSON.stringify({ trial_id: trialId, plan_sha256: plan, release_sha: release,
    client_id: clientId, request_id: requestId, payload });
  requireTrue(Buffer.byteLength(authorizationBody, "utf8") <= 128_000);
  function window() {
    const now = clock();
    requireTrue(Number.isFinite(now) && START <= now && now < END
      && env("YBO_TRIAL_GENERATION_GATEWAY_ENABLED") === "true"
      && env("QUALITY_BANNER_ENABLED") === "false" && env("NEWS_CARD_IDEMPOTENCY_ENABLED") === "false"
      && env("YBO_TRIAL_ID") === trialId && env("YBO_TRIAL_PLAN_SHA256") === plan
      && env("YBO_TRIAL_RELEASE_SHA") === release);
    return now;
  }
  async function assertActive() {
    window();
    try {
      const response = await fetcher(`${API}/internal/trial/generation-authorize`, {
        method: "POST", redirect: "error", signal: AbortSignal.timeout(5_000),
        headers: { "Content-Type": "application/json", "X-API-Key": secret! }, body: authorizationBody });
      requireTrue(response.status === 200 && !response.redirected
        && response.headers.get("content-type")?.split(";", 1)[0] === "application/json");
      const reader = response.body?.getReader(); requireTrue(reader);
      let size = 0; const chunks: Uint8Array[] = [];
      try {
        while (true) {
          const next = await reader.read(); if (next.done) break;
          size += next.value.length; if (size > 4096) { await reader.cancel(); requireTrue(false); }
          chunks.push(next.value);
        }
      } finally { reader.releaseLock(); }
      const value = JSON.parse(Buffer.concat(chunks, size).toString("utf8")), now = window();
      requireTrue(record(value) && exact(value, ["authorized", "trial_id", "plan_sha256", "release_sha",
        "client_id", "request_id", "observed_at"]) && value.authorized === true
        && value.trial_id === trialId && value.plan_sha256 === plan && value.release_sha === release
        && value.client_id === clientId && value.request_id === requestId
        && typeof value.observed_at === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value.observed_at)
        && now - Date.parse(value.observed_at) >= 0 && now - Date.parse(value.observed_at) <= 5_000);
    } catch { throw new TrialGenerationError(); }
  }
  await assertActive();
  const guarded: typeof fetch = async (input, init) => {
    await assertActive();
    return fetcher(input, { ...init, redirect: "error" });
  };
  return { trialId, collectOnly: collectOnly === "true", payload, apiUrl: API, assertActive, fetcher: guarded };
}
