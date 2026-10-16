import { createHash } from "node:crypto";
import { isCatalogUuid, type contentCatalogConfig } from "./content-catalog.mts";

type Json = Record<string, any>;
type Env = (key: string) => string | undefined;
type Config = NonNullable<ReturnType<typeof contentCatalogConfig>>;
export type RecoveryScope = {mode: "recovery"; packet_mode: "recovery_delivery_only_v1";
  content_version_id: string; recovery_id: string; plan_sha256: string};
export type RecoveryContext = {plan: Json; sha: string; scope: RecoveryScope;
  check: (terminal?: boolean) => void};
const KEYS = ["schema_version", "recovery_id", "workspace_id", "client_id", "outbox_id",
  "content_item_id", "content_version_id", "source_item_id", "generate_job_id", "banner_asset_id",
  "banner_sha256", "telegram_copy_sha256", "x_copy_sha256", "original_outbox_sha256",
  "original_trial_id", "original_plan_sha256", "destination_policy_sha256", "release_sha",
  "not_before", "deadline", "estimated_daily_limit_usd_micros"];
const HASH = /^[a-f0-9]{64}$/;
const record = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: Json, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
function requireTrue(v: unknown): asserts v {if (!v) throw Error("content_ops_recovery_blocked");}
function canonical(v: any): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (record(v)) return `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`;
  return JSON.stringify(v);
}
export const recoveryPlanHash = (value: Json) => createHash("sha256").update(canonical(value), "utf8").digest("hex");
const timestamp = (value: unknown) => typeof value === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(value)
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value.replace("Z", ".000Z");

export function recoveryFlagsOff(env: Env): boolean {
  return ["CONTENT_OPS_GATEWAY_ENABLED", "CONTENT_OPS_REVIEW_ENABLED", "CONTENT_OPS_TRIAL_GATEWAY_ENABLED",
    "CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED", "CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED",
    "YBO_TRIAL_GENERATION_GATEWAY_ENABLED", "TELEGRAM_PUBLICATION_ENABLED", "STUDIO_TELEGRAM_PUBLISH_ENABLED"]
    .every(key => (env(key) ?? "false") === "false");
}

export function configuredRecovery(req: Request, env: Env, release: string, clock: () => number): RecoveryContext {
  const raw = env("CONTENT_OPS_RECOVERY_PLAN_JSON"), sha = env("CONTENT_OPS_RECOVERY_PLAN_SHA256");
  requireTrue(typeof raw === "string" && raw.length <= 8192 && typeof sha === "string" && HASH.test(sha));
  const plan = JSON.parse(raw);
  requireTrue(record(plan) && exact(plan, KEYS) && plan.schema_version === "private-review-recovery@1"
    && plan.client_id === "babylon" && plan.estimated_daily_limit_usd_micros === 10_000_000
    && /^[a-f0-9]{40}$/.test(release) && plan.release_sha === release
    && KEYS.filter(k => k.endsWith("_id") && k !== "client_id").every(k => isCatalogUuid(plan[k])
      && plan[k] === plan[k].toLowerCase() && plan[k] !== "00000000-0000-0000-0000-000000000000")
    && KEYS.filter(k => k.endsWith("_sha256")).every(k => typeof plan[k] === "string" && HASH.test(plan[k]))
    && timestamp(plan.not_before) && timestamp(plan.deadline)
    && Date.parse(plan.deadline) > Date.parse(plan.not_before)
    && Date.parse(plan.deadline) - Date.parse(plan.not_before) <= 7_200_000
    && recoveryPlanHash(plan) === sha);
  const scope: RecoveryScope = {mode: "recovery", packet_mode: "recovery_delivery_only_v1",
    content_version_id: plan.content_version_id, recovery_id: plan.recovery_id, plan_sha256: sha};
  requireTrue(req.headers.get("x-content-ops-recovery-id") === scope.recovery_id
    && req.headers.get("x-content-ops-recovery-plan-sha256") === sha
    && !req.headers.has("x-content-ops-trial-id") && !req.headers.has("x-content-ops-trial-plan-sha256")
    && !req.headers.has("x-content-ops-trial-manifest-sha256"));
  const check = (terminal = false) => {
    requireTrue(env("CONTENT_OPS_RECOVERY_GATEWAY_ENABLED") === "true" && recoveryFlagsOff(env)
      && env("CONTENT_OPS_RECOVERY_PLAN_JSON") === raw && env("CONTENT_OPS_RECOVERY_PLAN_SHA256") === sha
      && env("CONTENT_OPS_REVIEW_RELEASE_SHA") === release);
    if (!terminal) {const now = clock(); requireTrue(Number.isFinite(now) && Date.parse(plan.not_before) <= now && now < Date.parse(plan.deadline));}
  };
  // Terminal readback may reconcile a historical committed result after expiry.
  // It supplies no claim or provider reservation authority.
  check(true);
  return {plan, sha, scope, check};
}

export async function boundedRecoveryJson(response: Response): Promise<unknown> {
  // PostgREST scalar JSON RPCs may carry one-item range metadata. This is
  // not a partial byte response; Storage PNG responses remain range-free.
  const rpcRange = response.headers.get("content-range"), rpcUnit = response.headers.get("range-unit");
  requireTrue(response.status === 200 && !response.redirected && response.body
    && (response.headers.get("content-type") || "").split(";", 1)[0] === "application/json"
    && (response.headers.get("content-encoding") || "identity") === "identity"
    && (rpcRange === null || rpcRange === "0-0/*" || rpcRange === "0-0/1")
    && (rpcUnit === null || rpcUnit === "items"));
  const declared = response.headers.get("content-length");
  requireTrue(declared === null || (/^\d+$/.test(declared) && Number(declared) <= 16384));
  const reader = response.body.getReader(), chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {const part = await reader.read(); if (part.done) break;
      size += part.value.byteLength;
      if (size > 16384) {await reader.cancel(); throw Error("content_ops_recovery_blocked");}
      chunks.push(part.value);
    }
    requireTrue(declared === null || Number(declared) === size);
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } finally {reader.releaseLock();}
}

export function recoveryRpc(context: RecoveryContext, cfg: Config, action: string, args: Json) {
  requireTrue(context.plan.workspace_id === cfg.workspaceId);
  return {target_workspace_id: cfg.workspaceId, target_content_version_id: context.scope.content_version_id,
    target_recovery_id: context.scope.recovery_id, target_plan_sha256: context.sha,
    target_release_sha: context.plan.release_sha, target_action: action, target_args: args};
}

export async function recoveryStatus(context: RecoveryContext, cfg: Config, fetcher: typeof fetch, clock: () => number) {
  context.check();
  const response = await fetcher(`${cfg.supabaseUrl}/rest/v1/rpc/content_ops_recovery_step`, {
    method: "POST", redirect: "error", signal: AbortSignal.timeout(10_000),
    headers: {apikey: cfg.serviceRoleKey, Authorization: `Bearer ${cfg.serviceRoleKey}`,
      "Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"},
    body: JSON.stringify(recoveryRpc(context, cfg, "status", {})),
  });
  const value = await boundedRecoveryJson(response);
  context.check();
  requireTrue(record(value) && exact(value, ["active", "observed_at", "plan", "plan_sha256"])
    && value.active === true && typeof value.observed_at === "string"
    && /(Z|[+-]\d\d:\d\d)$/.test(value.observed_at)
    && record(value.plan) && canonical(value.plan) === canonical(context.plan)
    && value.plan_sha256 === context.sha);
  const age = clock() - Date.parse(value.observed_at);
  requireTrue(Number.isFinite(age) && age >= 0 && age <= 5000);
  return value;
}

/** Translate only the known legacy internal calls, not arbitrary caller RPCs.
 * Every business request is attempted once. Status reads cannot mutate owners. */
export function recoveryFetcher(context: RecoveryContext, cfg: Config, backend: typeof fetch, clock: () => number): typeof fetch {
  return async (url, init) => {
    const input = String(url), base = `${cfg.supabaseUrl}/rest/v1/rpc/`;
    const storage = `${cfg.supabaseUrl}/storage/v1/object/content-studio/${cfg.workspaceId}/babylon/${context.plan.banner_asset_id}/news-card.png`;
    if (input === storage && init?.method === "GET") {
      await recoveryStatus(context, cfg, backend, clock); context.check();
      return backend(url, init);
    }
    requireTrue(init?.method === "POST" && typeof init.body === "string");
    const body = JSON.parse(init.body); requireTrue(record(body)
      && body.target_workspace_id === cfg.workspaceId && body.target_content_version_id === context.scope.content_version_id);
    let action: string, args: Json;
    if (input === base + "content_ops_button_card_owner_step") {
      requireTrue(exact(body, ["target_workspace_id", "target_content_version_id", "target_action", "target_args"]));
      action = body.target_action; args = body.target_args;
      requireTrue(["prepare", "bind", "reserve", "confirm", "register", "terminal"].includes(action) && record(args));
    } else {
      const map: Record<string, [string, string[]]> = {
        content_ops_reconcile_daily: ["reconcile", []],
        content_ops_claim_review: ["claim", ["claim_token"]],
        content_ops_begin_review_send: ["begin", ["outbox_id", "claim_token", "packet_sha256"]],
        content_ops_button_card_image_locator: ["image", ["outbox_id", "claim_token"]],
      };
      const entry = Object.entries(map).find(([name]) => input === base + name)?.[1];
      requireTrue(entry); [action] = entry;
      requireTrue(exact(body, ["target_workspace_id", "target_content_version_id", ...entry[1].map(k => "target_" + k)]));
      args = Object.fromEntries(entry[1].map(k => [k, body["target_" + k]]));
    }
    if (Object.hasOwn(args, "outbox_id")) requireTrue(args.outbox_id === context.plan.outbox_id);
    const terminal = action === "terminal";
    context.check(terminal);
    if (!terminal) await recoveryStatus(context, cfg, backend, clock);
    context.check(terminal);
    const result = await backend(base + "content_ops_recovery_step", {
      ...init, headers: {apikey: cfg.serviceRoleKey, Authorization: `Bearer ${cfg.serviceRoleKey}`,
        "Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity"},
      body: JSON.stringify(recoveryRpc(context, cfg, action, args)),
    });
    const value = await boundedRecoveryJson(result);
    context.check(terminal);
    return Response.json(value);
  };
}

export function recoveryTerminal(value: unknown, args: Json, context: RecoveryContext): value is Json {
  return record(value) && exact(value, ["status", "recovery_id", "card_id", "outbox_id", "original_outbox_status", "execution_authorized"])
    && value.recovery_id === context.scope.recovery_id && value.original_outbox_status === "claimed"
    && value.execution_authorized === false && args.outbox_id === context.plan.outbox_id
    && ((value.status === "recovery_sent" && value.card_id === args.card_id && value.outbox_id === args.outbox_id)
      || (value.status === "not_confirmed" && value.card_id === null && value.outbox_id === null));
}
