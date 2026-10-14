import { isCatalogUuid } from "./content-catalog.mts";
import { trialFlagsOff, type TrustedTrialResolver } from "./content-ops-trial.mts";

type Env = (key: string) => string | undefined;
type Dependencies = { getEnv: Env; releaseSha: () => string | null;
  fetcher?: typeof fetch; clock?: () => number };
const START = Date.parse("2026-10-14T06:22:17Z"), END = Date.parse("2026-10-14T08:22:17Z");
const MAX_BYTES = 16_384;
const HASH = /^[a-f0-9]{64}$/, SHA = /^[a-f0-9]{40}$/;
const PATH = "/internal/trial/manifest";
export class TrialManifestNotReady extends Error {
  constructor() { super("trial_manifest_not_ready"); }
}
export type TrialDeliveryReceipt = Readonly<{ trial_id: string; plan_sha256: string;
  manifest_sha256: string; content_version_id: string; card_id: string; outbox_id: string;
  confirmed_parts: 4; status: "sent" }>;
export type TrialDeliveryConfirmation = (receipt: TrialDeliveryReceipt) => Promise<void>;
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
function requireTrue(v: unknown): asserts v { if (!v) throw Error("content_ops_trial_resolver_unavailable"); }

/** Existing API credential + server-owned origin only; never a request URL.
 * Construction is pure and OFF reads no credential/configuration. The API
 * returns its own current durable owner state, not a caller-supplied manifest.
 * This is one HTTP attempt per resolution; unknown responses are not retried. */
function configuredRequest(deps: Dependencies) {
  if (deps.getEnv("CONTENT_OPS_TRIAL_GATEWAY_ENABLED") !== "true") return undefined;
  const clock = deps.clock ?? Date.now;
  let pinned: string | undefined;
  function configuration() {
    const now = clock();
    requireTrue(Number.isFinite(now) && START <= now && now < END
      && deps.getEnv("CONTENT_OPS_TRIAL_GATEWAY_ENABLED") === "true" && trialFlagsOff(deps.getEnv));
    const release = deps.releaseSha(), trialId = deps.getEnv("CONTENT_OPS_TRIAL_ID");
    const plan = deps.getEnv("CONTENT_OPS_TRIAL_PLAN_SHA256"), policy = deps.getEnv("CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256");
    requireTrue(typeof release === "string" && SHA.test(release)
      && deps.getEnv("CONTENT_OPS_REVIEW_RELEASE_SHA") === release
      && isCatalogUuid(trialId) && trialId === trialId.toLowerCase()
      && typeof plan === "string" && HASH.test(plan) && typeof policy === "string" && HASH.test(policy));
    const raw = deps.getEnv("RAILWAY_API_URL"), secret = deps.getEnv("API_SECRET");
    requireTrue(typeof raw === "string" && raw === raw.trim()
      && typeof secret === "string" && secret === secret.trim() && secret.length >= 16
      && secret.length <= 4096 && !/[\r\n]/.test(secret));
    const url = new URL(raw);
    requireTrue(url.protocol === "https:" && !url.username && !url.password && !url.port
      && (url.pathname === "/" || url.pathname === "") && !url.search && !url.hash
      && /^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$/.test(url.hostname)
      && !/(?:^|\.)(?:localhost|local|internal|invalid)$/.test(url.hostname));
    const value = { origin: url.origin, secret, release, trialId, plan, policy };
    const fingerprint = JSON.stringify(value);
    requireTrue(pinned === undefined || fingerprint === pinned);
    pinned = fingerprint; // Memory only. Never logged, returned or persisted.
    return value;
  }
  return async (path: string, body: Record<string, unknown>, allowNotReady = false) => {
    try {
      const cfg = configuration();
      requireTrue(body.trial_id === cfg.trialId && body.plan_sha256 === cfg.plan);
      const response = await (deps.fetcher ?? fetch)(cfg.origin + path, {
        method: "POST", redirect: "error", cache: "no-store", signal: AbortSignal.timeout(5000),
        headers: { "Content-Type": "application/json", "Accept": "application/json",
          "Accept-Encoding": "identity", "X-API-Key": cfg.secret },
        body: JSON.stringify(body),
      });
      requireTrue((response.status === 200 || (allowNotReady && response.status === 409)) && !response.redirected
        && response.headers.get("content-type")?.split(";",1)[0] === "application/json"
        && (response.headers.get("content-encoding") ?? "identity") === "identity"
        && !response.headers.has("content-range"));
      const declared = response.headers.get("content-length");
      requireTrue(declared === null || (/^[0-9]+$/.test(declared) && Number(declared) <= MAX_BYTES));
      const reader = response.body?.getReader(); requireTrue(reader);
      const chunks: Uint8Array[] = []; let size = 0;
      try {
        for (;;) {
          const next = await reader.read(); if (next.done) break;
          size += next.value.length;
          if (size > MAX_BYTES) { await reader.cancel(); requireTrue(false); }
          chunks.push(next.value);
        }
      } finally { reader.releaseLock(); }
      requireTrue(size > 0 && (declared === null || Number(declared) === size));
      configuration(); // Closed/expired/config drift during HTTP never yields authority.
      const value = JSON.parse(Buffer.concat(chunks, size).toString("utf8"));
      if (response.status === 409) {
        requireTrue(record(value) && Object.keys(value).length === 1 && value.error === "trial_manifest_not_ready");
        throw new TrialManifestNotReady();
      }
      return value;
    } catch (error) {
      if (error instanceof TrialManifestNotReady) throw error;
      throw Error("content_ops_trial_resolver_unavailable");
    }
  };
}

export function createTrialManifestResolver(deps: Dependencies): TrustedTrialResolver | undefined {
  const request = configuredRequest(deps); if (!request) return undefined;
  return async identity => {
    requireTrue(record(identity) && Object.keys(identity).length === 2
      && Object.hasOwn(identity, "trial_id") && Object.hasOwn(identity, "plan_sha256"));
    const value = await request(PATH, identity, true);
    requireTrue(record(value) && Object.keys(value).length === 4
      && ["active", "observed_at", "manifest", "manifest_sha256"].every(k => Object.hasOwn(value,k))
      && typeof value.active === "boolean" && typeof value.observed_at === "string"
      && record(value.manifest) && typeof value.manifest_sha256 === "string" && HASH.test(value.manifest_sha256));
    return value; // admitTrial verifies all durable identity/freshness/hash fields.
  };
}

export function createTrialDeliveryConfirmation(deps: Dependencies): TrialDeliveryConfirmation | undefined {
  const request = configuredRequest(deps); if (!request) return undefined;
  return async receipt => {
    requireTrue(record(receipt) && Object.keys(receipt).length === 8
      && ["trial_id", "content_version_id", "card_id", "outbox_id"].every(k =>
        isCatalogUuid(receipt[k]) && receipt[k] === receipt[k].toLowerCase())
      && ["plan_sha256", "manifest_sha256"].every(k => typeof receipt[k] === "string" && HASH.test(receipt[k]))
      && receipt.confirmed_parts === 4 && receipt.status === "sent");
    const value = await request("/internal/trial/delivery-confirm", receipt);
    requireTrue(record(value) && Object.keys(value).length === 1 && value.recorded === true);
  };
}
