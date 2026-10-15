/** Operator-installed readiness anchor only. No runtime clock can create or
 * extend these bounds; durable owner/manifest checks remain separate authority. */
export const READY_ANCHORED_TRIAL_ID = "d346ceea-4ea9-479f-a5cf-05097b50ce48";
export const FRESH_BABYLON_TRIAL_ID = "9d7865c1-c13b-4b1b-aba1-83124aa8d2cd";
export const FRESH_BABYLON_EARLIEST = "2026-10-15T15:00:00Z";
export const FRESH_BABYLON_KST_DATE = "2026-10-16";
export const NEW_BABYLON_TRIAL_ID = "c34dbb8a-ff7c-484e-8e43-757f692ec743";
export const NEW_BABYLON_EARLIEST = "2026-10-15T15:47:35Z";
type TrialIdentityPolicy = Readonly<{ clientIds: readonly string[]; maxPerClient: number; maxTotal: number; earliest: number; kstDate?: string }>;
const LEGACY_POLICY: TrialIdentityPolicy = Object.freeze({ clientIds: Object.freeze(["babylon", "origintrail"]), maxPerClient: 1, maxTotal: 2, earliest: 0 });
const FRESH_POLICY: TrialIdentityPolicy = Object.freeze({ clientIds: Object.freeze(["babylon"]), maxPerClient: 1, maxTotal: 1,
  earliest: Date.parse(FRESH_BABYLON_EARLIEST), kstDate: FRESH_BABYLON_KST_DATE });
const NEW_BABYLON_POLICY: TrialIdentityPolicy = Object.freeze({ clientIds: Object.freeze(["babylon"]), maxPerClient: 1, maxTotal: 1,
  earliest: Date.parse(NEW_BABYLON_EARLIEST), kstDate: FRESH_BABYLON_KST_DATE });
/** Code-owned identities, never a caller-selected client list or mode. */
export function trialIdentityPolicy(trialId: unknown): TrialIdentityPolicy | null {
  return trialId === READY_ANCHORED_TRIAL_ID ? LEGACY_POLICY
    : trialId === FRESH_BABYLON_TRIAL_ID ? FRESH_POLICY
    : trialId === NEW_BABYLON_TRIAL_ID ? NEW_BABYLON_POLICY : null;
}
export type TrialWindow = Readonly<{ notBefore: string; deadline: string; start: number; end: number; kstDate: string }>;
type Env = (name: string) => string | undefined;
export function trialWindow(notBefore: unknown, deadline: unknown): TrialWindow | null {
  const canonical = (value: unknown): value is string => typeof value === "string"
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)
    && Number.isFinite(Date.parse(value)) && new Date(Date.parse(value)).toISOString() === value.replace("Z", ".000Z");
  if (!canonical(notBefore) || !canonical(deadline)) return null;
  const start = Date.parse(notBefore), end = Date.parse(deadline);
  const kstDate = new Date(start + 9 * 3600_000).toISOString().slice(0, 10);
  if (end - start !== 7200_000 || new Date(end + 9 * 3600_000).toISOString().slice(0, 10) !== kstDate) return null;
  return Object.freeze({ notBefore, deadline, start, end, kstDate });
}
export function configuredTrialWindow(env: Env, prefix: "YBO_TRIAL" | "CONTENT_OPS_TRIAL"): TrialWindow | null {
  const policy = trialIdentityPolicy(env(`${prefix}_ID`));
  const bounds = trialWindow(env(`${prefix}_NOT_BEFORE`), env(`${prefix}_DEADLINE`));
  return policy && bounds && bounds.start >= policy.earliest
    && (!policy.kstDate || bounds.kstDate === policy.kstDate) ? bounds : null;
}
export function trialWindowUnchanged(env: Env, prefix: "YBO_TRIAL" | "CONTENT_OPS_TRIAL", window: TrialWindow): boolean {
  return env(`${prefix}_NOT_BEFORE`) === window.notBefore && env(`${prefix}_DEADLINE`) === window.deadline;
}
export function withinTrialWindow(window: TrialWindow, now: number): boolean {
  return Number.isFinite(now) && window.start <= now && now < window.end;
}
