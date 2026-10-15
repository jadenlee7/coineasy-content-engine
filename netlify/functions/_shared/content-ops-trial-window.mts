/** Operator-installed readiness anchor only. No runtime clock can create or
 * extend these bounds; durable owner/manifest checks remain separate authority. */
export const READY_ANCHORED_TRIAL_ID = "d346ceea-4ea9-479f-a5cf-05097b50ce48";
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
  return trialWindow(env(`${prefix}_NOT_BEFORE`), env(`${prefix}_DEADLINE`));
}
export function trialWindowUnchanged(env: Env, prefix: "YBO_TRIAL" | "CONTENT_OPS_TRIAL", window: TrialWindow): boolean {
  return env(`${prefix}_NOT_BEFORE`) === window.notBefore && env(`${prefix}_DEADLINE`) === window.deadline;
}
export function withinTrialWindow(window: TrialWindow, now: number): boolean {
  return Number.isFinite(now) && window.start <= now && now < window.end;
}
