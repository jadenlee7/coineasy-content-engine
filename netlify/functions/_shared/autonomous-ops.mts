import { createHash, timingSafeEqual } from "node:crypto";

import type { ContentCatalogConfig } from "./content-catalog.mts";

export const AUTONOMOUS_OPS_PROTOCOL = "origintrail-autonomous-ops@1";
export const AUTONOMOUS_OPS_TOKEN_HEADER = "x-coineasy-autonomous-ops-key";

const HASH = /^[a-f0-9]{64}$/;
const TOKEN_INVALID = /[^\x21-\x7e]/;
const CATEGORIES = new Set([
  "unexpected_publication", "batch_cost_overage", "batch_failed",
  "batch_stale", "buzz_delivery_unknown", "buzz_delivery_failed",
  "review_ack_unknown", "operations_response_unknown",
]);
const SEVERITIES = new Set(["medium", "high", "critical"]);
const CATEGORY_SEVERITY: Readonly<Record<string, string>> = {
  unexpected_publication: "critical",
  batch_cost_overage: "critical",
  batch_failed: "high",
  batch_stale: "high",
  buzz_delivery_unknown: "high",
  buzz_delivery_failed: "medium",
  review_ack_unknown: "medium",
  operations_response_unknown: "medium",
};
const RESERVED = [
  "OPENAI_API_KEY", "PUBLICATION_WORKER_TOKEN", "BATCH_DISPATCHER_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY", "BUZZ_PRIVATE_KEY",
  "BUZZ_OPERATIONS_WORKER_TOKEN", "BUZZ_REVIEW_WORKER_TOKEN",
  "BUZZ_DELIVERY_WORKER_TOKEN",
] as const;

type Observe = {
  action: "observe";
  protocol_version: typeof AUTONOMOUS_OPS_PROTOCOL;
};
type RecordPlan = {
  action: "record_plan";
  protocol_version: typeof AUTONOMOUS_OPS_PROTOCOL;
  snapshot_sha256: string;
  incident_key: string;
  category: string;
  severity: string;
  title_ko: string;
  summary_ko: string;
  steps_ko: string[];
  execution_mode: "propose_only";
  automatic_publication: false;
  external_writes: false;
};
export type AutonomousOpsAction = Observe | RecordPlan;
export type AutonomousOpsConfig = ContentCatalogConfig & {
  authorizationKey?: string;
};

export class AutonomousOpsError extends Error {
  readonly code: string;
  constructor(code: string) {
    super(code);
    this.name = "AutonomousOpsError";
    this.code = code;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exact(value: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  return actual.length === sorted.length
    && actual.every((key, index) => key === sorted[index]);
}

function configuredToken(getEnv: (name: string) => string | undefined): string | null {
  const token = getEnv("AUTONOMOUS_OPS_WORKER_TOKEN") || "";
  const reused = RESERVED.some((name) => {
    const candidate = getEnv(name) || "";
    return candidate.length > 0 && candidate === token;
  });
  return token.length >= 32 && token.length <= 512
    && !TOKEN_INVALID.test(token) && !reused ? token : null;
}

export function autonomousOpsConfigured(
  getEnv: (name: string) => string | undefined,
): boolean {
  return configuredToken(getEnv) !== null;
}

export function autonomousOpsLedgerEnabled(
  getEnv: (name: string) => string | undefined,
): boolean {
  return getEnv("AUTONOMOUS_OPS_LEDGER_ENABLED") === "true";
}

export function hasAutonomousOpsAccess(
  request: Request,
  getEnv: (name: string) => string | undefined,
): boolean {
  const expected = configuredToken(getEnv);
  const candidate = request.headers.get(AUTONOMOUS_OPS_TOKEN_HEADER) || "";
  if (!expected || candidate.length < 32 || candidate.length > 512) return false;
  const digest = (value: string) => createHash("sha256").update(value).digest();
  return timingSafeEqual(digest(candidate), digest(expected));
}

export function autonomousOpsSupabaseConfig(
  catalog: ContentCatalogConfig,
  getEnv: (name: string) => string | undefined,
): AutonomousOpsConfig {
  const scoped = (getEnv("SUPABASE_AUTONOMOUS_OPS_KEY") || "").trim();
  return scoped ? { ...catalog, authorizationKey: scoped } : { ...catalog };
}

function boundedText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.trim() === value
    && Buffer.byteLength(value, "utf8") >= 1
    && Buffer.byteLength(value, "utf8") <= maximum
    && !/[\u0000-\u001f\u007f]/u.test(value);
}

export function parseAutonomousOpsAction(value: unknown): AutonomousOpsAction {
  if (!record(value) || typeof value.action !== "string") {
    throw new AutonomousOpsError("invalid_autonomous_ops_request");
  }
  if (value.action === "observe") {
    if (!exact(value, ["action", "protocol_version"])
      || value.protocol_version !== AUTONOMOUS_OPS_PROTOCOL) {
      throw new AutonomousOpsError("invalid_autonomous_ops_request");
    }
    return { action: "observe", protocol_version: AUTONOMOUS_OPS_PROTOCOL };
  }
  if (value.action !== "record_plan" || !exact(value, [
    "action", "protocol_version", "snapshot_sha256", "incident_key",
    "category", "severity", "title_ko", "summary_ko", "steps_ko",
    "execution_mode", "automatic_publication", "external_writes",
  ]) || value.protocol_version !== AUTONOMOUS_OPS_PROTOCOL
    || typeof value.snapshot_sha256 !== "string" || !HASH.test(value.snapshot_sha256)
    || typeof value.incident_key !== "string" || !HASH.test(value.incident_key)
    || typeof value.category !== "string" || !CATEGORIES.has(value.category)
    || typeof value.severity !== "string" || !SEVERITIES.has(value.severity)
    || CATEGORY_SEVERITY[String(value.category)] !== value.severity
    || !boundedText(value.title_ko, 240) || !boundedText(value.summary_ko, 1200)
    || !Array.isArray(value.steps_ko) || value.steps_ko.length < 1
    || value.steps_ko.length > 5
    || !value.steps_ko.every((item) => boundedText(item, 600))
    || value.execution_mode !== "propose_only"
    || value.automatic_publication !== false || value.external_writes !== false) {
    throw new AutonomousOpsError("invalid_autonomous_ops_request");
  }
  return value as RecordPlan;
}

function rpc(action: AutonomousOpsAction, config: AutonomousOpsConfig) {
  if (action.action === "observe") return {
    name: "observe_origintrail_autonomous_ops",
    body: {
      target_workspace_id: config.workspaceId,
      target_protocol_version: action.protocol_version,
    },
  };
  return {
    name: "record_origintrail_autonomous_ops_plan",
    body: {
      target_workspace_id: config.workspaceId,
      target_protocol_version: action.protocol_version,
      target_snapshot_sha256: action.snapshot_sha256,
      target_incident_key: action.incident_key,
      target_category: action.category,
      target_severity: action.severity,
      target_title_ko: action.title_ko,
      target_summary_ko: action.summary_ko,
      target_steps_ko: action.steps_ko,
      target_execution_mode: action.execution_mode,
      target_automatic_publication: false,
      target_external_writes: false,
    },
  };
}

function validObserve(raw: unknown, workspaceId: string): boolean {
  if (!record(raw) || !exact(raw, [
    "workspace_id", "protocol_version", "observed_at_epoch",
    "observation_date_kst", "snapshot_sha256", "batch_failed_count",
    "batch_stale_count", "cost_overage_count", "buzz_delivery_failed_count",
    "buzz_delivery_unknown_count", "review_ack_unknown_count",
    "operations_response_unknown_count", "unexpected_publication_count",
    "nonterminal_batch_count", "actual_cost_microusd",
  ])) return false;
  const countKeys = [
    "batch_failed_count", "batch_stale_count", "cost_overage_count",
    "buzz_delivery_failed_count", "buzz_delivery_unknown_count",
    "review_ack_unknown_count", "operations_response_unknown_count",
    "unexpected_publication_count", "nonterminal_batch_count",
    "actual_cost_microusd",
  ];
  return raw.workspace_id === workspaceId
    && raw.protocol_version === AUTONOMOUS_OPS_PROTOCOL
    && Number.isSafeInteger(raw.observed_at_epoch)
    && Number(raw.observed_at_epoch) >= 1_700_000_000
    && typeof raw.observation_date_kst === "string"
    && /^20[0-9]{2}-[0-9]{2}-[0-9]{2}$/.test(raw.observation_date_kst)
    && typeof raw.snapshot_sha256 === "string" && HASH.test(raw.snapshot_sha256)
    && countKeys.every((key) => Number.isSafeInteger(raw[key]) && Number(raw[key]) >= 0);
}

function validTask(raw: unknown, action: RecordPlan, workspaceId: string): boolean {
  return record(raw) && exact(raw, [
    "workspace_id", "task_id", "incident_key", "category", "severity",
    "title_ko", "summary_ko", "steps_ko", "status", "reused",
    "automatic_execution",
  ]) && raw.workspace_id === workspaceId
    && typeof raw.task_id === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(raw.task_id)
    && raw.incident_key === action.incident_key
    && raw.category === action.category && raw.severity === action.severity
    && raw.title_ko === action.title_ko && raw.summary_ko === action.summary_ko
    && JSON.stringify(raw.steps_ko) === JSON.stringify(action.steps_ko)
    && raw.status === "proposed" && typeof raw.reused === "boolean"
    && raw.automatic_execution === false;
}

export async function executeAutonomousOpsAction(
  config: AutonomousOpsConfig,
  action: AutonomousOpsAction,
  fetcher: typeof fetch = fetch,
): Promise<unknown> {
  const request = rpc(action, config);
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/${request.name}`, {
      method: "POST",
      headers: {
        apikey: config.serviceRoleKey,
        Authorization: `Bearer ${config.authorizationKey ?? config.serviceRoleKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(request.body),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new AutonomousOpsError("autonomous_ops_storage_unavailable");
  }
  if (!response.ok) {
    throw new AutonomousOpsError(response.status === 409
      ? "autonomous_ops_conflict" : "autonomous_ops_storage_unavailable");
  }
  let raw: unknown;
  try { raw = await response.json(); } catch {
    throw new AutonomousOpsError("autonomous_ops_invalid_response");
  }
  const valid = action.action === "observe"
    ? validObserve(raw, config.workspaceId)
    : validTask(raw, action, config.workspaceId);
  if (!valid) throw new AutonomousOpsError("autonomous_ops_invalid_response");
  return raw;
}
