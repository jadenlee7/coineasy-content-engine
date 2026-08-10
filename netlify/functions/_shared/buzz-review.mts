import { createHash, timingSafeEqual } from "node:crypto";

import type { ContentCatalogConfig } from "./content-catalog.mts";
import { isCatalogUuid } from "./content-catalog.mts";

export const BUZZ_REVIEW_TOKEN_HEADER = "x-coineasy-buzz-review-key";

const TOKEN_MINIMUM = 32;
const TOKEN_MAXIMUM = 512;
const TOKEN_INVALID = /[^\x21-\x7e]/;
const HASH_PATTERN = /^[a-f0-9]{64}$/;
const CONTROL_PATTERN = /[\u0000-\u001f\u007f]/;
const PROTOCOL_VERSION = "origintrail-buzz-review@2";
const EPOCH_PATTERN = /^[1-9][0-9]{8,9}$/;
const RESERVED_SECRET_ENVS = [
  "BUZZ_SHADOW_ACCESS_TOKEN",
  "BUZZ_DELIVERY_WORKER_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY",
  "SUPABASE_BUZZ_DELIVERY_KEY",
  "SUPABASE_BUZZ_SHADOW_KEY",
  "SUPABASE_BUZZ_REVIEW_KEY",
  "STUDIO_ACCESS_TOKEN",
  "API_SECRET",
  "PUBLICATION_WORKER_TOKEN",
] as const;

type ListAction = { action: "list"; limit: number };
type RecordAction = {
  action: "record";
  job_id: string;
  delivery_event_id: string;
  channel_id: string;
  root_relay_event_id: string;
  message_sha256: string;
  protocol_version: typeof PROTOCOL_VERSION;
  decision_event_id: string;
  reviewer_pubkey: string;
  decision: "approved" | "changes_requested";
  reason: string | null;
  command_sha256: string;
  command_created_at_epoch: number;
};
export type BuzzReviewAction = ListAction | RecordAction;

export type BuzzReviewSupabaseConfig = ContentCatalogConfig & {
  authorizationKey?: string;
  protocolStartEpoch: number;
  protocolVersion: typeof PROTOCOL_VERSION;
};

export class BuzzReviewError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "BuzzReviewError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
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
  return typeof value === "string" && HASH_PATTERN.test(value);
}

function configuredToken(
  getEnv: (name: string) => string | undefined,
): string | null {
  const value = getEnv("BUZZ_REVIEW_WORKER_TOKEN") || "";
  const reused = RESERVED_SECRET_ENVS.some((name) => {
    const reserved = getEnv(name) || "";
    return reserved.length > 0 && reserved === value;
  });
  return value.length >= TOKEN_MINIMUM
    && value.length <= TOKEN_MAXIMUM
    && !TOKEN_INVALID.test(value)
    && !reused
    ? value
    : null;
}

export function configuredBuzzReviewers(
  getEnv: (name: string) => string | undefined,
): ReadonlySet<string> | null {
  const values = (getEnv("BUZZ_REVIEWER_PUBKEYS") || "")
    .split(",")
    .map((value) => value.trim());
  if (
    values.length < 1
    || values.length > 5
    || values.some((value) => !HASH_PATTERN.test(value))
    || new Set(values).size !== values.length
  ) return null;
  return new Set(values);
}

export function buzzReviewAccessConfigured(
  getEnv: (name: string) => string | undefined,
): boolean {
  return configuredToken(getEnv) !== null
    && configuredBuzzReviewers(getEnv) !== null
    && configuredBuzzReviewProtocol(getEnv) !== null;
}

export function configuredBuzzReviewProtocol(
  getEnv: (name: string) => string | undefined,
): { protocolStartEpoch: number; protocolVersion: typeof PROTOCOL_VERSION } | null {
  const raw = (getEnv("BUZZ_REVIEW_PROTOCOL_START_EPOCH") || "").trim();
  if (!EPOCH_PATTERN.test(raw)) return null;
  const protocolStartEpoch = Number(raw);
  if (
    !Number.isSafeInteger(protocolStartEpoch)
    || protocolStartEpoch < 1
    || protocolStartEpoch > 4_294_967_295
  ) return null;
  return { protocolStartEpoch, protocolVersion: PROTOCOL_VERSION };
}

export function hasValidBuzzReviewAccess(
  request: Request,
  getEnv: (name: string) => string | undefined,
): boolean {
  const expected = configuredToken(getEnv);
  const candidate = request.headers.get(BUZZ_REVIEW_TOKEN_HEADER) || "";
  if (
    !expected
    || candidate.length < TOKEN_MINIMUM
    || candidate.length > TOKEN_MAXIMUM
  ) return false;
  return timingSafeEqual(digest(candidate), digest(expected));
}

export function buzzReviewSupabaseConfig(
  config: ContentCatalogConfig,
  getEnv: (name: string) => string | undefined,
): BuzzReviewSupabaseConfig | null {
  const scoped = (getEnv("SUPABASE_BUZZ_REVIEW_KEY") || "").trim();
  const protocol = configuredBuzzReviewProtocol(getEnv);
  if (!protocol) return null;
  return scoped
    ? { ...config, ...protocol, authorizationKey: scoped }
    : { ...config, ...protocol };
}

export function parseBuzzReviewAction(
  value: unknown,
  reviewers: ReadonlySet<string>,
): BuzzReviewAction {
  if (!isRecord(value) || typeof value.action !== "string") {
    throw new BuzzReviewError("invalid_buzz_review_request");
  }
  if (value.action === "list") {
    if (
      !exactKeys(value, ["action", "limit"])
      || !Number.isSafeInteger(value.limit)
      || Number(value.limit) < 1
      || Number(value.limit) > 10
    ) throw new BuzzReviewError("invalid_buzz_review_request");
    return { action: "list", limit: Number(value.limit) };
  }
  if (value.action !== "record" || !exactKeys(value, [
    "action", "job_id", "delivery_event_id", "channel_id",
    "root_relay_event_id", "message_sha256", "protocol_version",
    "decision_event_id", "reviewer_pubkey",
    "decision", "reason", "command_sha256", "command_created_at_epoch",
  ])) throw new BuzzReviewError("invalid_buzz_review_request");

  const reasonValid = value.decision === "approved"
    ? value.reason === null
    : value.decision === "changes_requested"
      && typeof value.reason === "string"
      && value.reason.trim() === value.reason
      && value.reason.length >= 1
      && value.reason.length <= 500
      && Buffer.byteLength(value.reason, "utf8") <= 1_500
      && !CONTROL_PATTERN.test(value.reason);
  if (
    !uuid(value.job_id)
    || !hash(value.delivery_event_id)
    || !uuid(value.channel_id)
    || !hash(value.root_relay_event_id)
    || !hash(value.message_sha256)
    || value.protocol_version !== PROTOCOL_VERSION
    || !hash(value.decision_event_id)
    || !hash(value.reviewer_pubkey)
    || !reviewers.has(value.reviewer_pubkey)
    || !reasonValid
    || !hash(value.command_sha256)
    || !Number.isSafeInteger(value.command_created_at_epoch)
    || Number(value.command_created_at_epoch) < 1
    || Number(value.command_created_at_epoch) > 4_294_967_295
  ) throw new BuzzReviewError("invalid_buzz_review_request");
  return {
    action: "record",
    job_id: value.job_id.toLowerCase(),
    delivery_event_id: value.delivery_event_id,
    channel_id: value.channel_id.toLowerCase(),
    root_relay_event_id: value.root_relay_event_id,
    message_sha256: value.message_sha256,
    protocol_version: PROTOCOL_VERSION,
    decision_event_id: value.decision_event_id,
    reviewer_pubkey: value.reviewer_pubkey,
    decision: value.decision,
    reason: value.reason as string | null,
    command_sha256: value.command_sha256,
    command_created_at_epoch: Number(value.command_created_at_epoch),
  };
}

function commandSha256(action: RecordAction, workspaceId: string): string {
  return createHash("sha256").update([
    "coineasy-buzz-review-decision",
    "2.0",
    workspaceId,
    action.job_id,
    action.delivery_event_id,
    action.channel_id,
    action.root_relay_event_id,
    action.message_sha256,
    action.protocol_version,
    action.decision_event_id,
    action.reviewer_pubkey,
    action.decision,
    action.reason ?? "",
    String(action.command_created_at_epoch),
  ].join("\0"), "utf8").digest("hex");
}

function rpcRequest(
  action: BuzzReviewAction,
  workspaceId: string,
  config: Pick<BuzzReviewSupabaseConfig, "protocolStartEpoch" | "protocolVersion">,
): { name: string; body: Record<string, unknown> } {
  if (action.action === "list") return {
    name: "list_origintrail_buzz_review_targets",
    body: {
      target_workspace_id: workspaceId,
      target_limit: action.limit,
      target_protocol_start_epoch: config.protocolStartEpoch,
      target_protocol_version: config.protocolVersion,
    },
  };
  if (action.command_sha256 !== commandSha256(action, workspaceId)) {
    throw new BuzzReviewError("invalid_buzz_review_request");
  }
  return {
    name: "record_origintrail_buzz_review_decision",
    body: {
      target_workspace_id: workspaceId,
      target_job_id: action.job_id,
      target_delivery_event_id: action.delivery_event_id,
      target_channel_id: action.channel_id,
      target_root_relay_event_id: action.root_relay_event_id,
      target_message_sha256: action.message_sha256,
      target_protocol_version: action.protocol_version,
      target_protocol_start_epoch: config.protocolStartEpoch,
      target_decision_event_id: action.decision_event_id,
      target_reviewer_pubkey: action.reviewer_pubkey,
      target_decision: action.decision,
      target_reason: action.reason,
      target_command_sha256: action.command_sha256,
      target_command_created_at_epoch: action.command_created_at_epoch,
    },
  };
}

function validTarget(raw: unknown): boolean {
  if (!isRecord(raw) || !exactKeys(raw, [
    "job_id", "delivery_event_id", "channel_id", "root_relay_event_id",
    "message_sha256", "protocol_version", "delivered_at_epoch",
  ])) return false;
  return uuid(raw.job_id)
    && hash(raw.delivery_event_id)
    && uuid(raw.channel_id)
    && hash(raw.root_relay_event_id)
    && hash(raw.message_sha256)
    && raw.protocol_version === PROTOCOL_VERSION
    && Number.isSafeInteger(raw.delivered_at_epoch)
    && Number(raw.delivered_at_epoch) >= 1
    && Number(raw.delivered_at_epoch) <= 4_294_967_295;
}

function validResponse(
  action: BuzzReviewAction,
  raw: unknown,
  workspaceId: string,
): boolean {
  if (!isRecord(raw) || raw.schema_version !== "2.0"
    || raw.mode !== "publish_intent_review" || raw.workspace_id !== workspaceId) {
    return false;
  }
  if (action.action === "list") {
    return exactKeys(raw, ["schema_version", "mode", "workspace_id", "targets"])
      && Array.isArray(raw.targets)
      && raw.targets.length <= action.limit
      && raw.targets.every(validTarget);
  }
  return exactKeys(raw, [
    "schema_version", "mode", "workspace_id", "job_id", "delivery_event_id",
    "channel_id", "root_relay_event_id", "message_sha256", "protocol_version",
    "decision_event_id",
    "reviewer_pubkey", "decision", "reason", "command_sha256",
    "command_created_at_epoch", "reused",
  ])
    && raw.job_id === action.job_id
    && raw.delivery_event_id === action.delivery_event_id
    && raw.channel_id === action.channel_id
    && raw.root_relay_event_id === action.root_relay_event_id
    && raw.message_sha256 === action.message_sha256
    && raw.protocol_version === action.protocol_version
    && raw.decision_event_id === action.decision_event_id
    && raw.reviewer_pubkey === action.reviewer_pubkey
    && raw.decision === action.decision
    && raw.reason === action.reason
    && raw.command_sha256 === action.command_sha256
    && raw.command_created_at_epoch === action.command_created_at_epoch
    && typeof raw.reused === "boolean";
}

export async function executeBuzzReviewAction(
  config: BuzzReviewSupabaseConfig,
  action: BuzzReviewAction,
  fetcher: typeof fetch = fetch,
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  const rpc = rpcRequest(action, config.workspaceId, config);
  const requestUrl = `${config.supabaseUrl}/rest/v1/rpc/${rpc.name}`;
  const requestHeaders = {
    apikey: config.serviceRoleKey,
    Authorization: `Bearer ${config.authorizationKey ?? config.serviceRoleKey}`,
    "Content-Type": "application/json",
  };
  const requestBody = JSON.stringify(rpc.body);
  const maxAttempts = action.action === "record" ? 2 : 1;
  let response: Response | undefined;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      response = await fetcher(requestUrl, {
        method: "POST",
        headers: requestHeaders,
        body: requestBody,
        signal: signal ?? AbortSignal.timeout(10_000),
      });
    } catch {
      if (attempt + 1 < maxAttempts) continue;
      throw new BuzzReviewError("buzz_review_storage_unavailable");
    }
    if (
      response.status >= 500
      && response.status <= 599
      && attempt + 1 < maxAttempts
    ) {
      continue;
    }
    break;
  }
  if (!response) {
    throw new BuzzReviewError("buzz_review_storage_unavailable");
  }
  if (!response.ok) {
    throw new BuzzReviewError(
      response.status === 409
        ? "buzz_review_decision_conflict"
        : "buzz_review_storage_unavailable",
    );
  }
  let raw: unknown;
  try {
    raw = await response.json();
  } catch {
    throw new BuzzReviewError("buzz_review_invalid_response");
  }
  if (!validResponse(action, raw, config.workspaceId)) {
    throw new BuzzReviewError("buzz_review_invalid_response");
  }
  return raw as Record<string, unknown>;
}
