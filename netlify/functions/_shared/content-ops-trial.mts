import { createHash } from "node:crypto";
import { isCatalogUuid, type contentCatalogConfig } from "./content-catalog.mts";
import { READY_ANCHORED_TRIAL_ID, configuredTrialWindow, trialWindowUnchanged, withinTrialWindow,
  type TrialWindow } from "./content-ops-trial-window.mts";

type Json = Record<string, any>;
type Env = (key: string) => string | undefined;
type Config = NonNullable<ReturnType<typeof contentCatalogConfig>>;
export type TrustedTrialResolver = (identity: Readonly<{ trial_id: string; plan_sha256: string }>) => Promise<unknown>;
export type TrialScope = { mode: "trial"; content_version_id: string;
  packet_mode: "trial_delivery_only_v1"; trial_id: string; plan_sha256: string; manifest_sha256: string };
export type TrialAdmission = { scope: TrialScope; entry: Json; window: TrialWindow; assertActive: () => Promise<void> };
const CLIENTS = ["babylon", "origintrail"];
const HEX = /^[a-f0-9]{64}$/;
const record = (v: unknown): v is Json => !!v && typeof v === "object" && !Array.isArray(v);
const exact = (v: Json, keys: string[]) => Object.keys(v).length === keys.length && keys.every(k => Object.hasOwn(v, k));
const uuid = (v: unknown) => isCatalogUuid(v) && v === v.toLowerCase();
const hash = (v: unknown) => typeof v === "string" && HEX.test(v);
const sha = (v: string) => createHash("sha256").update(v, "utf8").digest("hex");
const stamp = (v: unknown): number => typeof v === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(v) ? Date.parse(v) : NaN;
function requireTrue(v: unknown): asserts v { if (!v) throw Error("content_ops_trial_blocked"); }
function canonical(v: any): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (record(v)) return `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`;
  return JSON.stringify(v);
}
export const trialManifestHash = (v: Json) => sha(canonical(v));

export function trialFlagsOff(env: Env): boolean {
  return ["CONTENT_OPS_GATEWAY_ENABLED", "CONTENT_OPS_REVIEW_ENABLED",
    "CONTENT_OPS_BUTTON_CARD_GATEWAY_ENABLED", "CONTENT_OPS_DAILY_BUTTON_CARD_GATEWAY_ENABLED"]
    .every(key => env(key) === "false");
}

/** No resolver is installed by default. A header/list/date alone is never admission.
 * The resolver must read the same durable trial owner's CURRENT active
 * admission and immutable generated manifest, never caller-provided fields. */
export async function admitTrial(req: Request, env: Env, release: string, clock: () => number,
  resolver?: TrustedTrialResolver): Promise<TrialAdmission> {
  requireTrue(env("CONTENT_OPS_TRIAL_GATEWAY_ENABLED") === "true" && trialFlagsOff(env));
  const trialId = env("CONTENT_OPS_TRIAL_ID"), plan = env("CONTENT_OPS_TRIAL_PLAN_SHA256");
  const policy = env("CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256");
  const bounds = configuredTrialWindow(env, "CONTENT_OPS_TRIAL");
  requireTrue(trialId === READY_ANCHORED_TRIAL_ID && hash(plan) && hash(policy) && bounds && typeof resolver === "function"
    && /^[a-f0-9]{40}$/.test(release) && env("CONTENT_OPS_REVIEW_RELEASE_SHA") === release);
  const version = req.headers.get("x-content-ops-version-id"), manifestHash = req.headers.get("x-content-ops-trial-manifest-sha256");
  requireTrue(uuid(version) && hash(manifestHash)
    && req.headers.get("x-content-ops-mode") === "trial"
    && req.headers.get("x-content-ops-packet-mode") === "trial_delivery_only_v1"
    && req.headers.get("x-content-ops-daily-scope") === null
    && req.headers.get("x-content-ops-trial-id") === trialId
    && req.headers.get("x-content-ops-trial-plan-sha256") === plan);
  function window() {
    const now = clock();
    requireTrue(withinTrialWindow(bounds!, now) && trialWindowUnchanged(env, "CONTENT_OPS_TRIAL", bounds!) && trialFlagsOff(env)
      && env("CONTENT_OPS_TRIAL_GATEWAY_ENABLED") === "true"
      && env("CONTENT_OPS_TRIAL_ID") === trialId && env("CONTENT_OPS_TRIAL_PLAN_SHA256") === plan
      && env("CONTENT_OPS_TRIAL_DESTINATION_POLICY_SHA256") === policy && env("CONTENT_OPS_REVIEW_RELEASE_SHA") === release);
    return now;
  }
  let pinned: string | undefined;
  async function resolve() {
    window(); // before resolver I/O, not only before downstream HTTP
    const value = await resolver!({ trial_id: trialId!, plan_sha256: plan! });
    const now = window();
    requireTrue(record(value) && exact(value, ["active", "observed_at", "manifest", "manifest_sha256"])
      && value.active === true && now - stamp(value.observed_at) >= 0 && now - stamp(value.observed_at) <= 5000
      && value.manifest_sha256 === manifestHash && record(value.manifest));
    const m = value.manifest;
    requireTrue(exact(m, ["contract", "trial_id", "plan_sha256", "release_sha", "not_before", "deadline",
      "destination_policy_sha256", "entries"])
      && m.contract === "ybo-trial-courier-manifest@1" && m.trial_id === trialId && m.plan_sha256 === plan
      && m.release_sha === release && m.destination_policy_sha256 === policy
      && m.not_before === bounds!.notBefore && m.deadline === bounds!.deadline
      && Array.isArray(m.entries) && m.entries.length >= 1 && m.entries.length <= 2);
    const clients = new Set(), ids = new Set();
    for (const e of m.entries) {
      requireTrue(record(e) && exact(e, ["client_id", "job_id", "request_id", "source_item_id", "source_sha256",
        "content_item_id", "content_version_id", "banner_sha256", "telegram_copy_sha256", "x_copy_sha256"])
        && CLIENTS.includes(e.client_id) && !clients.has(e.client_id)
        && [e.job_id, e.request_id, e.source_item_id, e.content_item_id, e.content_version_id].every(uuid)
        && e.request_id === e.content_item_id
        && [e.source_sha256, e.banner_sha256, e.telegram_copy_sha256, e.x_copy_sha256].every(hash));
      const unique = [e.job_id, e.request_id, e.source_item_id, e.content_version_id];
      requireTrue(new Set(unique).size === 4 && unique.every(id => !ids.has(id)));
      unique.forEach(id => ids.add(id)); clients.add(e.client_id);
    }
    const c = canonical(m);
    requireTrue(sha(c) === manifestHash && (pinned === undefined || c === pinned));
    pinned = c;
    return m;
  }
  const m = await resolve(), entry = m.entries.find((e: Json) => e.content_version_id === version);
  requireTrue(entry);
  return { scope: { mode: "trial", content_version_id: version!, packet_mode: "trial_delivery_only_v1",
      trial_id: trialId!, plan_sha256: plan!, manifest_sha256: manifestHash! }, entry: Object.freeze({ ...entry }), window: bounds!,
    assertActive: async () => { await resolve(); } };
}

/** Cross-check durable generated data. This is NOT trial authority; resolver is.
 * Existing exact owner RPCs repeat current daily-slot/source/approval checks. */
export async function verifyTrialProjection(admission: TrialAdmission, cfg: Config,
  fetcher: typeof fetch, clock: () => number): Promise<void> {
  const e = admission.entry;
  const { start, end, kstDate } = admission.window;
  async function one(table: string, select: string, filters: Record<string, string>) {
    await admission.assertActive();
    const query = new URLSearchParams({ select, workspace_id: `eq.${cfg.workspaceId}`, ...filters, limit: "2" });
    const r = await fetcher(`${cfg.supabaseUrl}/rest/v1/${table}?${query}`, {
      method: "GET", redirect: "error", signal: AbortSignal.timeout(10_000),
      headers: { apikey: cfg.serviceRoleKey, Authorization: `Bearer ${cfg.serviceRoleKey}` } });
    requireTrue(r.ok && !r.redirected && r.headers.get("content-type")?.split(";",1)[0] === "application/json");
    const reader = r.body?.getReader(); requireTrue(reader);
    const bytes: Uint8Array[] = []; let size = 0;
    try {
      while (true) {
        const next = await reader.read(); if (next.done) break;
        size += next.value.length; if (size > 65536) { await reader.cancel(); requireTrue(false); }
        bytes.push(next.value);
      }
    } finally { reader.releaseLock(); }
    const rows = JSON.parse(Buffer.concat(bytes, size).toString("utf8"));
    requireTrue(Array.isArray(rows) && rows.length === 1 && record(rows[0]));
    return rows[0];
  }
  const job = await one("jobs", "id,client_id,job_kind,status,attempts,max_attempts,created_at,started_at,finished_at,idempotency_key,input,output,content_item_id",
    { id: `eq.${e.job_id}`, client_id: `eq.${e.client_id}` });
  requireTrue(job.id === e.job_id && job.client_id === e.client_id && job.job_kind === "generate"
    && job.status === "succeeded" && job.attempts === 1 && job.max_attempts === 3
    // complete_review_draft_job preserves the natural job's null item column;
    // immutable generated identities are persisted in output, checked below.
    && job.content_item_id === null
    && job.idempotency_key === `official-x-review:v1:${kstDate}:${e.client_id}`
    && [job.created_at, job.started_at, job.finished_at].every(t => start <= stamp(t) && stamp(t) <= clock() && stamp(t) < end)
    && stamp(job.created_at) <= stamp(job.started_at) && stamp(job.started_at) <= stamp(job.finished_at)
    && record(job.input) && record(job.output)
    && job.input.workflow === "official_x_review_draft_v1" && job.input.content_kind === "daily_news"
    && job.input.manual_only === false && job.input.kst_date === kstDate && job.input.request_id === e.request_id
    && canonical(job.input.source_item_ids) === canonical([e.source_item_id])
    && job.output.content_item_id === e.content_item_id && job.output.content_version_id === e.content_version_id
    && canonical(job.output.source_item_ids) === canonical([e.source_item_id]));
  const item = await one("content_items", "id,client_id,status,content_kind,current_version_id",
    { id: `eq.${e.content_item_id}` });
  requireTrue(item.id === e.content_item_id && item.client_id === e.client_id && item.status === "needs_review"
    && item.content_kind === "daily_news" && item.current_version_id === e.content_version_id);
  const version = await one("content_versions", "id,content_item_id,channel_copy,generation_meta,deliverables,created_at",
    { id: `eq.${e.content_version_id}`, content_item_id: `eq.${e.content_item_id}` });
  requireTrue(version.id === e.content_version_id && version.content_item_id === e.content_item_id
    && record(version.channel_copy) && typeof version.channel_copy.telegram === "string" && typeof version.channel_copy.x === "string"
    && sha(version.channel_copy.telegram) === e.telegram_copy_sha256 && sha(version.channel_copy.x) === e.x_copy_sha256
    && record(version.generation_meta) && version.generation_meta.mock_mode === false
    && version.generation_meta.request_id === e.request_id && record(version.deliverables)
    && uuid(version.deliverables.primary_asset_id) && start <= stamp(version.created_at)
    && stamp(version.created_at) <= clock() && stamp(version.created_at) < end);
  const asset = await one("assets", "id,content_item_id,content_version_id,asset_kind,mime_type,sha256,storage_bucket,storage_path",
    { id: `eq.${version.deliverables.primary_asset_id}`, content_version_id: `eq.${e.content_version_id}` });
  requireTrue(asset.id === version.deliverables.primary_asset_id && asset.content_item_id === e.content_item_id
    && asset.content_version_id === e.content_version_id && asset.asset_kind === "png" && asset.mime_type === "image/png"
    && asset.sha256 === e.banner_sha256 && asset.storage_bucket === "content-studio"
    && asset.storage_path === `${cfg.workspaceId}/${e.client_id}/${asset.id}/news-card.png`);
  const source = await one("source_items", "id,client_id,body", { id: `eq.${e.source_item_id}` });
  requireTrue(source.id === e.source_item_id && source.client_id === e.client_id && typeof source.body === "string"
    && sha(source.body) === e.source_sha256 && job.input.source_content === source.body);
  await admission.assertActive();
}
