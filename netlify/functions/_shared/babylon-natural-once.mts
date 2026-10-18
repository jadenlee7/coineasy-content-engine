/** A separately named trusted plan contract, NOT an arbitrary trial-ID fallback.
 * Only generation/preflight may consume this configuration. The existing API
 * still proves the exact durable owner reservation before every external call;
 * a header, plan JSON, or valid UUID alone never authorizes generation. */
import { createHash } from "node:crypto";
import { trialIdentityPolicy, trialWindow, type TrialWindow } from "./content-ops-trial-window.mts";

export const BABYLON_NATURAL_ONCE_CONTRACT = "babylon-natural-once@1";
export const BABYLON_NATURAL_ONCE_ESTIMATE_POLICY = "2f71160c21ebb2b40d000ccf6cf6d36553c0039766416d52e89d3ab0af653982";
type Env = (name: string) => string | undefined;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/;
// Retired readiness anchor is deliberately NOT an active trial policy. Its
// historic owner/DB identity must never be recycled under the new contract.
const RETIRED_READINESS_ANCHOR = "1a2bef47-4827-4bc3-9b4d-5baf2714015d";
const KEYS = ["contract", "trial_id", "not_before", "deadline", "kst_date", "client_ids",
  "max_per_client", "max_total", "estimated_daily_limit_micros", "estimate_policy_sha256", "release_sha"].sort();
const PINS = ["YBO_TRIAL_PLAN_JSON", "YBO_TRIAL_PLAN_SHA256", "YBO_TRIAL_ID", "YBO_TRIAL_RELEASE_SHA",
  "YBO_TRIAL_NOT_BEFORE", "YBO_TRIAL_DEADLINE"] as const;
const record = (value: unknown): value is Record<string, any> => !!value && typeof value === "object" && !Array.isArray(value);
export class NaturalOnceConfigurationError extends Error {
  constructor() { super("natural_once_configuration_invalid"); }
}
export type TrustedNaturalOncePolicy = Readonly<{
  trialId: string; planSha256: string; releaseSha: string; bounds: TrialWindow;
  policy: Readonly<{ clientIds: readonly string[]; maxPerClient: number; maxTotal: number; earliest: number }>;
  pins: Readonly<Record<typeof PINS[number], string>>;
}>;

/** Legacy plans retain their existing identity checks. New plans must be a
 * canonical full deployment-owned plan with fixed scope and a matching hash.
 * Requiring canonical raw JSON also rejects duplicate keys and hidden replans. */
export function trustedNaturalOncePolicy(env: Env): TrustedNaturalOncePolicy | null {
  const supplied = Object.fromEntries(PINS.map(key => [key, env(key)]));
  const raw = supplied.YBO_TRIAL_PLAN_JSON;
  if (raw === undefined) return null;
  try {
    if (raw.length > 4096) throw new NaturalOnceConfigurationError();
    const value = JSON.parse(raw);
    if (!record(value)) throw new NaturalOnceConfigurationError();
    if (value.contract === "ybo-server-trial@1") return null;
    if (value.contract !== BABYLON_NATURAL_ONCE_CONTRACT
      || Object.keys(value).sort().join() !== KEYS.join()) throw new NaturalOnceConfigurationError();
    const canonical = JSON.stringify(Object.fromEntries(KEYS.map(key => [key, value[key]])));
    const bounds = trialWindow(value.not_before, value.deadline);
    const hash = createHash("sha256").update(canonical, "utf8").digest("hex");
    if (raw !== canonical || !bounds || value.kst_date !== bounds.kstDate
      || typeof value.trial_id !== "string" || !UUID.test(value.trial_id)
      || trialIdentityPolicy(value.trial_id) !== null || value.trial_id === RETIRED_READINESS_ANCHOR
      || JSON.stringify(value.client_ids) !== '["babylon"]'
      || value.max_per_client !== 1 || value.max_total !== 1
      || value.estimated_daily_limit_micros !== 10_000_000
      || value.estimate_policy_sha256 !== BABYLON_NATURAL_ONCE_ESTIMATE_POLICY
      || typeof value.release_sha !== "string" || !/^[a-f0-9]{40}$/.test(value.release_sha)
      || supplied.YBO_TRIAL_PLAN_SHA256 !== hash || supplied.YBO_TRIAL_ID !== value.trial_id
      || supplied.YBO_TRIAL_RELEASE_SHA !== value.release_sha
      || supplied.YBO_TRIAL_NOT_BEFORE !== value.not_before || supplied.YBO_TRIAL_DEADLINE !== value.deadline) {
      throw new NaturalOnceConfigurationError();
    }
    return Object.freeze({ trialId: value.trial_id, planSha256: hash, releaseSha: value.release_sha, bounds,
      policy: Object.freeze({ clientIds: Object.freeze(["babylon"]), maxPerClient: 1, maxTotal: 1, earliest: bounds.start }),
      pins: Object.freeze(supplied as Record<typeof PINS[number], string>) });
  } catch { throw new NaturalOnceConfigurationError(); }
}

export function naturalOncePolicyUnchanged(env: Env, policy: TrustedNaturalOncePolicy): boolean {
  return PINS.every(key => env(key) === policy.pins[key]);
}
