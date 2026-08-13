import { createHash, timingSafeEqual } from "node:crypto";

import type { ContentCatalogConfig } from "./content-catalog.mts";
import { isCatalogUuid } from "./content-catalog.mts";

export const BUZZ_OPERATIONS_TOKEN_HEADER = "x-coineasy-buzz-operations-key";
export const BUZZ_OPERATIONS_PROTOCOL_VERSION = "origintrail-buzz-operations@1";

const TOKEN_INVALID = /[^\x21-\x7e]/;
const HASH = /^[a-f0-9]{64}$/;
const WORKER = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const RESPONSE_STATUSES = new Set([
  "pending", "claimed", "attempt_started", "delivered",
  "delivery_unknown", "failed",
]);
const COMMANDS = new Set(["status", "plan_today", "next_task", "hold"]);
const ERROR_CODES = new Set([
  "buzz_cli_config_invalid",
  "buzz_cli_preflight_failed",
  "buzz_delivery_request_invalid",
  "buzz_delivery_unknown",
]);
const RESERVED_SECRET_ENVS = [
  "BUZZ_REVIEW_WORKER_TOKEN",
  "BUZZ_SHADOW_ACCESS_TOKEN",
  "BUZZ_DELIVERY_WORKER_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY",
  "SUPABASE_BUZZ_REVIEW_KEY",
  "SUPABASE_BUZZ_OPERATIONS_KEY",
  "OPENAI_API_KEY",
  "PUBLICATION_WORKER_TOKEN",
  "STUDIO_ACCESS_TOKEN",
] as const;

type RecordAction = {
  action: "record";
  channel_id: string;
  command_event_id: string;
  reviewer_pubkey: string;
  protocol_version: typeof BUZZ_OPERATIONS_PROTOCOL_VERSION;
  command: "status" | "plan_today" | "next_task" | "hold";
  command_sha256: string;
  command_created_at_epoch: number;
  reply_to_event_id: string | null;
};
type ReconcileAction = { action: "response_reconcile"; limit: number };
type ClaimAction = {
  action: "response_claim";
  command_event_id: string | null;
  worker_id: string;
  lease_seconds: number;
};
type AttemptAction = {
  action: "response_attempt";
  command_event_id: string;
  worker_id: string;
  message_sha256: string;
  request_sha256: string;
};
type CompleteAction = {
  action: "response_complete";
  command_event_id: string;
  worker_id: string;
  request_sha256: string;
  relay_event_id: string;
  reconciled: boolean;
};
type FailAction = {
  action: "response_fail";
  command_event_id: string;
  worker_id: string;
  error_code: string;
  retryable_before_attempt: boolean;
};
type UnknownAction = { action: "response_unknown"; limit: number };
export type BuzzOperationsAction = RecordAction | ReconcileAction | ClaimAction
  | AttemptAction | CompleteAction | FailAction | UnknownAction;

export type BuzzOperationsConfig = ContentCatalogConfig & {
  authorizationKey?: string;
  protocolStartEpoch: number;
};

export class BuzzOperationsError extends Error {
  readonly code: string;
  constructor(code: string) {
    super(code);
    this.name = "BuzzOperationsError";
    this.code = code;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(value).sort();
  const sorted = [...expected].sort();
  return actual.length === sorted.length
    && actual.every((key, index) => key === sorted[index]);
}

function uuid(value: unknown): value is string {
  return typeof value === "string" && isCatalogUuid(value.toLowerCase());
}

function hash(value: unknown): value is string {
  return typeof value === "string" && HASH.test(value);
}

function worker(value: unknown): value is string {
  return typeof value === "string" && WORKER.test(value);
}

function epoch(value: unknown): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 1
    && Number(value) <= 4_294_967_295;
}

function limit(value: unknown, maximum: number): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 1
    && Number(value) <= maximum;
}

function configuredToken(
  getEnv: (name: string) => string | undefined,
): string | null {
  const value = getEnv("BUZZ_OPERATIONS_WORKER_TOKEN") || "";
  const reused = RESERVED_SECRET_ENVS.some((name) => {
    const candidate = getEnv(name) || "";
    return candidate.length > 0 && candidate === value;
  });
  return value.length >= 32 && value.length <= 512
    && !TOKEN_INVALID.test(value) && !reused ? value : null;
}

function configuredReviewers(
  getEnv: (name: string) => string | undefined,
): ReadonlySet<string> | null {
  const values = (getEnv("BUZZ_OPERATIONS_REVIEWER_PUBKEYS") || "")
    .split(",").map((value) => value.trim());
  return values.length >= 1 && values.length <= 5
    && values.every((value) => HASH.test(value))
    && new Set(values).size === values.length ? new Set(values) : null;
}

function configuredProtocolStart(
  getEnv: (name: string) => string | undefined,
): number | null {
  const raw = (getEnv("BUZZ_OPERATIONS_PROTOCOL_START_EPOCH") || "").trim();
  if (!/^[1-9][0-9]{8,9}$/.test(raw)) return null;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value >= 1_700_000_000
    && value <= 4_294_967_295 ? value : null;
}

export function buzzOperationsConfigured(
  getEnv: (name: string) => string | undefined,
): boolean {
  return configuredToken(getEnv) !== null
    && configuredReviewers(getEnv) !== null
    && configuredProtocolStart(getEnv) !== null;
}

export function buzzOperationsOutboxEnabled(
  getEnv: (name: string) => string | undefined,
): boolean {
  return getEnv("BUZZ_OPERATIONS_OUTBOX_ENABLED") === "true";
}

export function hasValidBuzzOperationsAccess(
  request: Request,
  getEnv: (name: string) => string | undefined,
): boolean {
  const expected = configuredToken(getEnv);
  const candidate = request.headers.get(BUZZ_OPERATIONS_TOKEN_HEADER) || "";
  if (!expected || candidate.length < 32 || candidate.length > 512) return false;
  const digest = (value: string) => createHash("sha256").update(value).digest();
  return timingSafeEqual(digest(candidate), digest(expected));
}

export function buzzOperationsSupabaseConfig(
  config: ContentCatalogConfig,
  getEnv: (name: string) => string | undefined,
): BuzzOperationsConfig | null {
  const protocolStartEpoch = configuredProtocolStart(getEnv);
  if (protocolStartEpoch === null) return null;
  const scoped = (getEnv("SUPABASE_BUZZ_OPERATIONS_KEY") || "").trim();
  return scoped
    ? { ...config, protocolStartEpoch, authorizationKey: scoped }
    : { ...config, protocolStartEpoch };
}

export function parseBuzzOperationsAction(
  value: unknown,
  getEnv: (name: string) => string | undefined,
): BuzzOperationsAction {
  if (!record(value) || typeof value.action !== "string") {
    throw new BuzzOperationsError("invalid_buzz_operations_request");
  }
  const reviewers = configuredReviewers(getEnv);
  if (!reviewers) throw new BuzzOperationsError("invalid_buzz_operations_request");
  if (value.action === "record") {
    if (!exactKeys(value, [
      "action", "channel_id", "command_event_id", "reviewer_pubkey",
      "protocol_version", "command", "command_sha256",
      "command_created_at_epoch", "reply_to_event_id",
    ]) || !uuid(value.channel_id) || !hash(value.command_event_id)
      || !hash(value.reviewer_pubkey) || !reviewers.has(value.reviewer_pubkey)
      || value.protocol_version !== BUZZ_OPERATIONS_PROTOCOL_VERSION
      || typeof value.command !== "string" || !COMMANDS.has(value.command)
      || !hash(value.command_sha256) || !epoch(value.command_created_at_epoch)
      || !(value.reply_to_event_id === null || hash(value.reply_to_event_id))
      || ((value.command === "hold") !== (value.reply_to_event_id !== null))) {
      throw new BuzzOperationsError("invalid_buzz_operations_request");
    }
    return {
      action: "record",
      channel_id: value.channel_id.toLowerCase(),
      command_event_id: value.command_event_id,
      reviewer_pubkey: value.reviewer_pubkey,
      protocol_version: BUZZ_OPERATIONS_PROTOCOL_VERSION,
      command: value.command as RecordAction["command"],
      command_sha256: value.command_sha256,
      command_created_at_epoch: Number(value.command_created_at_epoch),
      reply_to_event_id: value.reply_to_event_id as string | null,
    };
  }
  if (value.action === "response_reconcile" || value.action === "response_unknown") {
    const maximum = value.action === "response_reconcile" ? 100 : 10;
    if (!exactKeys(value, ["action", "limit"]) || !limit(value.limit, maximum)) {
      throw new BuzzOperationsError("invalid_buzz_operations_request");
    }
    return { action: value.action, limit: Number(value.limit) };
  }
  if (value.action === "response_claim") {
    if (!exactKeys(value, [
      "action", "command_event_id", "worker_id", "lease_seconds",
    ]) || !(value.command_event_id === null || hash(value.command_event_id))
      || !worker(value.worker_id) || !Number.isSafeInteger(value.lease_seconds)
      || Number(value.lease_seconds) < 180 || Number(value.lease_seconds) > 600) {
      throw new BuzzOperationsError("invalid_buzz_operations_request");
    }
    return {
      action: "response_claim",
      command_event_id: value.command_event_id as string | null,
      worker_id: value.worker_id,
      lease_seconds: Number(value.lease_seconds),
    };
  }
  if (value.action === "response_attempt") {
    if (!exactKeys(value, [
      "action", "command_event_id", "worker_id", "message_sha256",
      "request_sha256",
    ]) || !hash(value.command_event_id) || !worker(value.worker_id)
      || !hash(value.message_sha256) || !hash(value.request_sha256)) {
      throw new BuzzOperationsError("invalid_buzz_operations_request");
    }
    return {
      action: "response_attempt", command_event_id: value.command_event_id,
      worker_id: value.worker_id, message_sha256: value.message_sha256,
      request_sha256: value.request_sha256,
    };
  }
  if (value.action === "response_complete") {
    if (!exactKeys(value, [
      "action", "command_event_id", "worker_id", "request_sha256",
      "relay_event_id", "reconciled",
    ]) || !hash(value.command_event_id) || !worker(value.worker_id)
      || !hash(value.request_sha256) || !hash(value.relay_event_id)
      || typeof value.reconciled !== "boolean") {
      throw new BuzzOperationsError("invalid_buzz_operations_request");
    }
    return {
      action: "response_complete", command_event_id: value.command_event_id,
      worker_id: value.worker_id, request_sha256: value.request_sha256,
      relay_event_id: value.relay_event_id, reconciled: value.reconciled,
    };
  }
  if (value.action === "response_fail") {
    if (!exactKeys(value, [
      "action", "command_event_id", "worker_id", "error_code",
      "retryable_before_attempt",
    ]) || !hash(value.command_event_id) || !worker(value.worker_id)
      || typeof value.error_code !== "string" || !ERROR_CODES.has(value.error_code)
      || typeof value.retryable_before_attempt !== "boolean") {
      throw new BuzzOperationsError("invalid_buzz_operations_request");
    }
    return {
      action: "response_fail", command_event_id: value.command_event_id,
      worker_id: value.worker_id, error_code: value.error_code,
      retryable_before_attempt: value.retryable_before_attempt,
    };
  }
  throw new BuzzOperationsError("invalid_buzz_operations_request");
}

function commandSha(action: RecordAction): string {
  return createHash("sha256").update([
    "coineasy-buzz-operations-command", action.protocol_version,
    action.channel_id, action.command_event_id, action.reviewer_pubkey,
    action.command, String(action.command_created_at_epoch),
    action.reply_to_event_id ?? "",
  ].join("\0"), "utf8").digest("hex");
}

function rpc(action: BuzzOperationsAction, config: BuzzOperationsConfig) {
  if (action.action === "record") {
    if (commandSha(action) !== action.command_sha256) {
      throw new BuzzOperationsError("invalid_buzz_operations_request");
    }
    return { name: "record_origintrail_buzz_operations_command", body: {
      target_workspace_id: config.workspaceId,
      target_channel_id: action.channel_id,
      target_command_event_id: action.command_event_id,
      target_reviewer_pubkey: action.reviewer_pubkey,
      target_protocol_version: action.protocol_version,
      target_protocol_start_epoch: config.protocolStartEpoch,
      target_command: action.command,
      target_command_sha256: action.command_sha256,
      target_command_created_at_epoch: action.command_created_at_epoch,
      target_reply_to_event_id: action.reply_to_event_id,
    }};
  }
  if (action.action === "response_reconcile") return {
    name: "reconcile_origintrail_buzz_operations_leases",
    body: { target_workspace_id: config.workspaceId, target_limit: action.limit },
  };
  if (action.action === "response_claim") return {
    name: "claim_origintrail_buzz_operations_response",
    body: {
      target_workspace_id: config.workspaceId,
      target_command_event_id: action.command_event_id,
      target_worker_id: action.worker_id,
      target_lease_seconds: action.lease_seconds,
    },
  };
  if (action.action === "response_attempt") return {
    name: "mark_origintrail_buzz_operations_response_attempt",
    body: {
      target_workspace_id: config.workspaceId,
      target_command_event_id: action.command_event_id,
      target_worker_id: action.worker_id,
      target_message_sha256: action.message_sha256,
      target_request_sha256: action.request_sha256,
    },
  };
  if (action.action === "response_complete") return {
    name: "complete_origintrail_buzz_operations_response",
    body: {
      target_workspace_id: config.workspaceId,
      target_command_event_id: action.command_event_id,
      target_worker_id: action.worker_id,
      target_request_sha256: action.request_sha256,
      target_relay_event_id: action.relay_event_id,
      target_reconciled: action.reconciled,
    },
  };
  if (action.action === "response_fail") return {
    name: "fail_origintrail_buzz_operations_response",
    body: {
      target_workspace_id: config.workspaceId,
      target_command_event_id: action.command_event_id,
      target_worker_id: action.worker_id,
      target_error_code: action.error_code,
      target_retryable_before_attempt: action.retryable_before_attempt,
    },
  };
  return {
    name: "list_origintrail_buzz_operations_unknown",
    body: { target_workspace_id: config.workspaceId, target_limit: action.limit },
  };
}

function validResponseObject(raw: unknown, workspaceId: string): boolean {
  if (!record(raw) || !exactKeys(raw, [
    "workspace_id", "command_event_id", "channel_id", "reply_to_event_id",
    "thread_root_event_id", "command", "task_id", "message", "message_sha256", "status",
    "claim_granted", "reused", "authorized_once", "request_sha256",
    "delivery_started_at_epoch", "relay_event_id",
  ])) return false;
  const message = raw.message;
  const attemptStarted = ["attempt_started", "delivered", "delivery_unknown"]
    .includes(String(raw.status));
  return raw.workspace_id === workspaceId && hash(raw.command_event_id)
    && uuid(raw.channel_id) && raw.reply_to_event_id === raw.command_event_id
    && hash(raw.thread_root_event_id)
    && typeof raw.command === "string" && COMMANDS.has(raw.command)
    && (raw.task_id === null || uuid(raw.task_id))
    && typeof message === "string" && Buffer.byteLength(message, "utf8") >= 1
    && Buffer.byteLength(message, "utf8") <= 1_024 && !message.includes("@")
    && !message.toLowerCase().includes("nostr:npub1")
    && hash(raw.message_sha256)
    && createHash("sha256").update(message, "utf8").digest("hex") === raw.message_sha256
    && RESPONSE_STATUSES.has(String(raw.status))
    && typeof raw.claim_granted === "boolean" && typeof raw.reused === "boolean"
    && typeof raw.authorized_once === "boolean"
    && (raw.request_sha256 === null || hash(raw.request_sha256))
    && (raw.delivery_started_at_epoch === null || epoch(raw.delivery_started_at_epoch))
    && (raw.relay_event_id === null || hash(raw.relay_event_id))
    && (!attemptStarted || (hash(raw.request_sha256) && epoch(raw.delivery_started_at_epoch)))
    && ((raw.status === "delivered") === hash(raw.relay_event_id));
}

function validResponse(
  action: BuzzOperationsAction, raw: unknown, workspaceId: string,
): boolean {
  if (action.action === "response_reconcile") return record(raw)
    && exactKeys(raw, [
      "ok", "workspace_id", "requeued_count", "failed_count", "unknown_count",
    ]) && raw.ok === true && raw.workspace_id === workspaceId
    && [raw.requeued_count, raw.failed_count, raw.unknown_count]
      .every((value) => Number.isSafeInteger(value) && Number(value) >= 0);
  if (action.action === "response_unknown") return record(raw)
    && exactKeys(raw, ["workspace_id", "items"])
    && raw.workspace_id === workspaceId && Array.isArray(raw.items)
    && raw.items.length <= action.limit
    && raw.items.every((item) => validResponseObject(item, workspaceId)
      && (item as Record<string, unknown>).status === "delivery_unknown");
  if (action.action === "response_claim" && raw === null) return true;
  if (!validResponseObject(raw, workspaceId)) return false;
  const response = raw as Record<string, unknown>;
  if ("command_event_id" in action && action.command_event_id !== null
    && response.command_event_id !== action.command_event_id) return false;
  if (action.action === "response_attempt") return response.message_sha256 === action.message_sha256
    && response.request_sha256 === action.request_sha256;
  if (action.action === "response_complete") return response.status === "delivered"
    && response.request_sha256 === action.request_sha256
    && response.relay_event_id === action.relay_event_id;
  return true;
}

export async function executeBuzzOperationsAction(
  config: BuzzOperationsConfig,
  action: BuzzOperationsAction,
  fetcher: typeof fetch = fetch,
): Promise<unknown> {
  const request = rpc(action, config);
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/rest/v1/rpc/${request.name}`,
      {
        method: "POST",
        headers: {
          apikey: config.serviceRoleKey,
          Authorization: `Bearer ${config.authorizationKey ?? config.serviceRoleKey}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(request.body),
        signal: AbortSignal.timeout(10_000),
      },
    );
  } catch {
    throw new BuzzOperationsError("buzz_operations_storage_unavailable");
  }
  if (!response.ok) {
    throw new BuzzOperationsError(response.status === 409
      ? "buzz_operations_conflict" : "buzz_operations_storage_unavailable");
  }
  let raw: unknown;
  try { raw = await response.json(); } catch {
    throw new BuzzOperationsError("buzz_operations_invalid_response");
  }
  if (!validResponse(action, raw, config.workspaceId)) {
    throw new BuzzOperationsError("buzz_operations_invalid_response");
  }
  return raw;
}
