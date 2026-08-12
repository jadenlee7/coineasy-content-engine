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
const ACK_SCHEMA_VERSION = "1.0";
const ACK_MODE = "durable_review_acknowledgement";
const ACK_TEMPLATE_VERSION = "origintrail-buzz-review-ack@1";
const EPOCH_PATTERN = /^[1-9][0-9]{8,9}$/;
const WORKER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const ACK_STATUSES = new Set([
  "pending",
  "claimed",
  "attempt_started",
  "delivered",
  "delivery_unknown",
  "failed",
]);
const ACK_FAILURE_CODES = new Set([
  "buzz_cli_config_invalid",
  "buzz_cli_preflight_failed",
  "buzz_delivery_request_invalid",
  "buzz_delivery_unknown",
]);
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
  action: "record" | "record_with_ack";
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
type AckReconcileAction = { action: "ack_reconcile"; limit: number };
type AckClaimAction = {
  action: "ack_claim";
  job_id: string | null;
  worker_id: string;
  lease_seconds: number;
};
type AckAttemptAction = {
  action: "ack_attempt";
  job_id: string;
  worker_id: string;
  message_sha256: string;
  request_sha256: string;
};
type AckCompleteAction = {
  action: "ack_complete";
  job_id: string;
  worker_id: string;
  request_sha256: string;
  relay_event_id: string;
  reconciled: boolean;
};
type AckFailAction = {
  action: "ack_fail";
  job_id: string;
  worker_id: string;
  error_code: string;
  retryable_before_attempt: boolean;
};
type AckUnknownAction = { action: "ack_unknown"; limit: number };
export type BuzzReviewAction =
  | ListAction
  | RecordAction
  | AckReconcileAction
  | AckClaimAction
  | AckAttemptAction
  | AckCompleteAction
  | AckFailAction
  | AckUnknownAction;

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

function worker(value: unknown): value is string {
  return typeof value === "string" && WORKER_PATTERN.test(value);
}

function epoch(value: unknown): value is number {
  return Number.isSafeInteger(value)
    && Number(value) >= 1
    && Number(value) <= 4_294_967_295;
}

function boundedLimit(value: unknown, maximum: number): value is number {
  return Number.isSafeInteger(value)
    && Number(value) >= 1
    && Number(value) <= maximum;
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

export function buzzReviewAckOutboxEnabled(
  getEnv: (name: string) => string | undefined,
): boolean {
  return getEnv("BUZZ_REVIEW_ACK_OUTBOX_ENABLED") === "true";
}

export function isBuzzReviewAckOutboxAction(
  action: BuzzReviewAction,
): boolean {
  return action.action === "record_with_ack" || action.action.startsWith("ack_");
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
      || !boundedLimit(value.limit, 10)
    ) throw new BuzzReviewError("invalid_buzz_review_request");
    return { action: "list", limit: Number(value.limit) };
  }
  if (value.action === "record" || value.action === "record_with_ack") {
    if (!exactKeys(value, [
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
      || !epoch(value.command_created_at_epoch)
    ) throw new BuzzReviewError("invalid_buzz_review_request");
    return {
      action: value.action,
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
  if (value.action === "ack_reconcile") {
    if (!exactKeys(value, ["action", "limit"]) || !boundedLimit(value.limit, 100)) {
      throw new BuzzReviewError("invalid_buzz_review_request");
    }
    return { action: "ack_reconcile", limit: Number(value.limit) };
  }
  if (value.action === "ack_claim") {
    if (
      !exactKeys(value, ["action", "job_id", "worker_id", "lease_seconds"])
      || !(value.job_id === null || uuid(value.job_id))
      || !worker(value.worker_id)
      || !Number.isSafeInteger(value.lease_seconds)
      || Number(value.lease_seconds) < 180
      || Number(value.lease_seconds) > 600
    ) throw new BuzzReviewError("invalid_buzz_review_request");
    return {
      action: "ack_claim",
      job_id: value.job_id === null ? null : value.job_id.toLowerCase(),
      worker_id: value.worker_id,
      lease_seconds: Number(value.lease_seconds),
    };
  }
  if (value.action === "ack_attempt") {
    if (
      !exactKeys(value, [
        "action", "job_id", "worker_id", "message_sha256", "request_sha256",
      ])
      || !uuid(value.job_id)
      || !worker(value.worker_id)
      || !hash(value.message_sha256)
      || !hash(value.request_sha256)
    ) throw new BuzzReviewError("invalid_buzz_review_request");
    return {
      action: "ack_attempt",
      job_id: value.job_id.toLowerCase(),
      worker_id: value.worker_id,
      message_sha256: value.message_sha256,
      request_sha256: value.request_sha256,
    };
  }
  if (value.action === "ack_complete") {
    if (
      !exactKeys(value, [
        "action", "job_id", "worker_id", "request_sha256", "relay_event_id",
        "reconciled",
      ])
      || !uuid(value.job_id)
      || !worker(value.worker_id)
      || !hash(value.request_sha256)
      || !hash(value.relay_event_id)
      || typeof value.reconciled !== "boolean"
    ) throw new BuzzReviewError("invalid_buzz_review_request");
    return {
      action: "ack_complete",
      job_id: value.job_id.toLowerCase(),
      worker_id: value.worker_id,
      request_sha256: value.request_sha256,
      relay_event_id: value.relay_event_id,
      reconciled: value.reconciled,
    };
  }
  if (value.action === "ack_fail") {
    if (
      !exactKeys(value, [
        "action", "job_id", "worker_id", "error_code",
        "retryable_before_attempt",
      ])
      || !uuid(value.job_id)
      || !worker(value.worker_id)
      || typeof value.error_code !== "string"
      || !ACK_FAILURE_CODES.has(value.error_code)
      || typeof value.retryable_before_attempt !== "boolean"
    ) throw new BuzzReviewError("invalid_buzz_review_request");
    return {
      action: "ack_fail",
      job_id: value.job_id.toLowerCase(),
      worker_id: value.worker_id,
      error_code: value.error_code,
      retryable_before_attempt: value.retryable_before_attempt,
    };
  }
  if (value.action === "ack_unknown") {
    if (!exactKeys(value, ["action", "limit"]) || !boundedLimit(value.limit, 10)) {
      throw new BuzzReviewError("invalid_buzz_review_request");
    }
    return { action: "ack_unknown", limit: Number(value.limit) };
  }
  throw new BuzzReviewError("invalid_buzz_review_request");
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
  if (action.action === "record" || action.action === "record_with_ack") {
    if (action.command_sha256 !== commandSha256(action, workspaceId)) {
      throw new BuzzReviewError("invalid_buzz_review_request");
    }
    return {
      name: action.action === "record_with_ack"
        ? "record_origintrail_buzz_review_decision_with_ack"
        : "record_origintrail_buzz_review_decision",
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
  if (action.action === "ack_reconcile") return {
    name: "reconcile_origintrail_buzz_review_ack_leases",
    body: {
      target_workspace_id: workspaceId,
      target_limit: action.limit,
    },
  };
  if (action.action === "ack_claim") return {
    name: "claim_origintrail_buzz_review_ack",
    body: {
      target_workspace_id: workspaceId,
      target_job_id: action.job_id,
      target_worker_id: action.worker_id,
      target_lease_seconds: action.lease_seconds,
    },
  };
  if (action.action === "ack_attempt") return {
    name: "mark_origintrail_buzz_review_ack_attempt",
    body: {
      target_workspace_id: workspaceId,
      target_job_id: action.job_id,
      target_worker_id: action.worker_id,
      target_message_sha256: action.message_sha256,
      target_request_sha256: action.request_sha256,
    },
  };
  if (action.action === "ack_complete") return {
    name: "complete_origintrail_buzz_review_ack",
    body: {
      target_workspace_id: workspaceId,
      target_job_id: action.job_id,
      target_worker_id: action.worker_id,
      target_request_sha256: action.request_sha256,
      target_relay_event_id: action.relay_event_id,
      target_reconciled: action.reconciled,
    },
  };
  if (action.action === "ack_fail") return {
    name: "fail_origintrail_buzz_review_ack",
    body: {
      target_workspace_id: workspaceId,
      target_job_id: action.job_id,
      target_worker_id: action.worker_id,
      target_error_code: action.error_code,
      target_retryable_before_attempt: action.retryable_before_attempt,
    },
  };
  return {
    name: "list_origintrail_buzz_review_ack_unknown",
    body: {
      target_workspace_id: workspaceId,
      target_limit: action.limit,
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
    && epoch(raw.delivered_at_epoch);
}

function expectedAckMessage(decision: unknown, reason: unknown): string | null {
  if (decision === "approved" && reason === null) return [
    "✅ 게시 승인 접수",
    "원문·최종물 확인 결정을 기록했습니다.",
    "",
    "현재 상태: 검토 결정 기록 완료",
    "자동 발행: OFF",
  ].join("\n");
  if (decision === "changes_requested" && typeof reason === "string") return [
    "🛠 수정 요청 접수",
    "사유는 검토자의 원문 답글에 기록했습니다.",
    "",
    "현재 상태: 수정 대기",
    "자동 재생성·발행: OFF",
  ].join("\n");
  return null;
}

function validAcknowledgement(raw: unknown): boolean {
  if (!isRecord(raw) || !exactKeys(raw, [
    "job_id", "channel_id", "root_relay_event_id", "decision_event_id",
    "decision", "reason", "command_created_at_epoch", "template_version",
    "message", "status", "claim_granted", "reused", "message_sha256",
    "request_sha256", "delivery_started_at_epoch", "relay_event_id",
  ])) return false;
  const message = expectedAckMessage(raw.decision, raw.reason);
  const reasonValid = raw.decision === "approved"
    ? raw.reason === null
    : raw.decision === "changes_requested"
      && typeof raw.reason === "string"
      && raw.reason.trim() === raw.reason
      && raw.reason.length >= 1
      && raw.reason.length <= 500
      && Buffer.byteLength(raw.reason, "utf8") <= 1_500
      && !CONTROL_PATTERN.test(raw.reason);
  const hashesValid = [
    raw.message_sha256, raw.request_sha256, raw.relay_event_id,
  ].every((value) => value === null || hash(value));
  const attemptStarted = [
    "attempt_started", "delivered", "delivery_unknown",
  ].includes(String(raw.status));
  return uuid(raw.job_id)
    && uuid(raw.channel_id)
    && hash(raw.root_relay_event_id)
    && hash(raw.decision_event_id)
    && reasonValid
    && epoch(raw.command_created_at_epoch)
    && raw.template_version === ACK_TEMPLATE_VERSION
    && message !== null
    && raw.message === message
    && Buffer.byteLength(message, "utf8") <= 1_024
    && ACK_STATUSES.has(String(raw.status))
    && typeof raw.claim_granted === "boolean"
    && typeof raw.reused === "boolean"
    && hashesValid
    && (raw.delivery_started_at_epoch === null || epoch(raw.delivery_started_at_epoch))
    && (!attemptStarted || (
      hash(raw.message_sha256)
      && hash(raw.request_sha256)
      && epoch(raw.delivery_started_at_epoch)
    ))
    && (raw.status === "delivered") === hash(raw.relay_event_id);
}

function validAckEnvelope(raw: unknown, workspaceId: string): raw is Record<string, unknown> {
  return isRecord(raw)
    && raw.schema_version === ACK_SCHEMA_VERSION
    && raw.mode === ACK_MODE
    && raw.workspace_id === workspaceId;
}

function validDecisionResponse(
  action: RecordAction,
  raw: Record<string, unknown>,
): boolean {
  const keys = [
    "schema_version", "mode", "workspace_id", "job_id", "delivery_event_id",
    "channel_id", "root_relay_event_id", "message_sha256", "protocol_version",
    "decision_event_id", "reviewer_pubkey", "decision", "reason",
    "command_sha256", "command_created_at_epoch", "reused",
    ...(action.action === "record_with_ack" ? ["acknowledgement_status"] : []),
  ];
  return exactKeys(raw, keys)
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
    && typeof raw.reused === "boolean"
    && (action.action !== "record_with_ack"
      || ACK_STATUSES.has(String(raw.acknowledgement_status)));
}

function validResponse(
  action: BuzzReviewAction,
  raw: unknown,
  workspaceId: string,
): boolean {
  if (action.action === "list") {
    return isRecord(raw)
      && raw.schema_version === "2.0"
      && raw.mode === "publish_intent_review"
      && raw.workspace_id === workspaceId
      && exactKeys(raw, ["schema_version", "mode", "workspace_id", "targets"])
      && Array.isArray(raw.targets)
      && raw.targets.length <= action.limit
      && raw.targets.every(validTarget);
  }
  if (action.action === "record" || action.action === "record_with_ack") {
    return isRecord(raw)
      && raw.schema_version === "2.0"
      && raw.mode === "publish_intent_review"
      && raw.workspace_id === workspaceId
      && validDecisionResponse(action, raw);
  }
  if (!validAckEnvelope(raw, workspaceId)) return false;
  if (action.action === "ack_reconcile") {
    return exactKeys(raw, [
      "schema_version", "mode", "workspace_id", "reconciled_count",
      "pending_count", "failed_count", "delivery_unknown_count",
    ]) && [
      raw.reconciled_count, raw.pending_count, raw.failed_count,
      raw.delivery_unknown_count,
    ].every((value) => Number.isSafeInteger(value) && Number(value) >= 0);
  }
  if (action.action === "ack_claim") {
    return exactKeys(raw, [
      "schema_version", "mode", "workspace_id", "acknowledgement",
    ])
      && (raw.acknowledgement === null || (
        validAcknowledgement(raw.acknowledgement)
        && (action.job_id === null
          || (raw.acknowledgement as Record<string, unknown>).job_id === action.job_id)
      ));
  }
  if (action.action === "ack_attempt") {
    return exactKeys(raw, [
      "schema_version", "mode", "workspace_id", "job_id", "status",
      "message_sha256", "request_sha256", "authorized_once", "reused",
    ])
      && raw.job_id === action.job_id
      && raw.status === "attempt_started"
      && raw.message_sha256 === action.message_sha256
      && raw.request_sha256 === action.request_sha256
      && typeof raw.authorized_once === "boolean"
      && typeof raw.reused === "boolean"
      && !(raw.authorized_once === true && raw.reused === true);
  }
  if (action.action === "ack_complete") {
    return exactKeys(raw, [
      "schema_version", "mode", "workspace_id", "job_id", "status",
      "request_sha256", "relay_event_id", "reused",
    ])
      && raw.job_id === action.job_id
      && raw.status === "delivered"
      && raw.request_sha256 === action.request_sha256
      && raw.relay_event_id === action.relay_event_id
      && typeof raw.reused === "boolean";
  }
  if (action.action === "ack_fail") {
    return exactKeys(raw, [
      "schema_version", "mode", "workspace_id", "job_id", "status", "reused",
    ])
      && raw.job_id === action.job_id
      && ACK_STATUSES.has(String(raw.status))
      && typeof raw.reused === "boolean";
  }
  return exactKeys(raw, [
    "schema_version", "mode", "workspace_id", "acknowledgements",
  ])
    && Array.isArray(raw.acknowledgements)
    && raw.acknowledgements.length <= action.limit
    && raw.acknowledgements.every((acknowledgement) => (
      validAcknowledgement(acknowledgement)
      && (acknowledgement as Record<string, unknown>).status === "delivery_unknown"
      && (acknowledgement as Record<string, unknown>).claim_granted === false
    ));
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
  const maxAttempts = (
    action.action === "record" || action.action === "record_with_ack"
  ) ? 2 : 1;
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
        ? isBuzzReviewAckOutboxAction(action)
          ? "buzz_review_acknowledgement_conflict"
          : "buzz_review_decision_conflict"
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
