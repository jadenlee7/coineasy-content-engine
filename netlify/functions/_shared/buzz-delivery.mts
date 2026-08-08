import { createHash, timingSafeEqual } from "node:crypto";

import type { ContentCatalogConfig } from "./content-catalog.mts";
import { isCatalogUuid } from "./content-catalog.mts";

export const BUZZ_DELIVERY_TOKEN_HEADER = "x-coineasy-buzz-delivery-key";

const TOKEN_MINIMUM = 32;
const TOKEN_MAXIMUM = 512;
const TOKEN_INVALID = /[^\x21-\x7e]/;
const HASH_PATTERN = /^[a-f0-9]{64}$/;
const WORKER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const STATUSES = new Set([
  "pending",
  "claimed",
  "attempt_started",
  "delivered",
  "delivery_unknown",
  "failed",
]);
const FAILURE_CODES = new Set([
  "buzz_cli_config_invalid",
  "buzz_cli_preflight_failed",
  "buzz_delivery_request_invalid",
  "buzz_delivery_unknown",
]);
const RESERVED_SECRET_ENVS = [
  "BUZZ_SHADOW_ACCESS_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY",
  "SUPABASE_BUZZ_DELIVERY_KEY",
  "SUPABASE_BUZZ_SHADOW_KEY",
  "STUDIO_ACCESS_TOKEN",
  "API_SECRET",
  "PUBLICATION_WORKER_TOKEN",
] as const;

export type BuzzDeliveryAction =
  | {
      action: "claim";
      event_id: string;
      job_id: string;
      channel_id: string;
      message_sha256: string;
      request_sha256: string;
      worker_id: string;
      lease_seconds: number;
    }
  | {
      action: "attempt";
      event_id: string;
      worker_id: string;
      request_sha256: string;
    }
  | {
      action: "complete";
      event_id: string;
      worker_id: string;
      request_sha256: string;
      relay_event_id: string;
    }
  | {
      action: "fail";
      event_id: string;
      worker_id: string;
      error_code: string;
      retryable_before_attempt: boolean;
    }
  | {
      action: "reconcile";
      limit: number;
    };

export class BuzzDeliveryError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "BuzzDeliveryError";
    this.code = code;
  }
}

export type BuzzDeliverySupabaseConfig = ContentCatalogConfig & {
  authorizationKey?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function digest(value: string): Buffer {
  return createHash("sha256").update(value, "utf8").digest();
}

function configuredToken(
  getEnv: (name: string) => string | undefined,
): string | null {
  const value = getEnv("BUZZ_DELIVERY_WORKER_TOKEN") || "";
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

export function buzzDeliveryAccessConfigured(
  getEnv: (name: string) => string | undefined,
): boolean {
  return configuredToken(getEnv) !== null;
}

// Adoption path for the scoped `coineasy_buzz_delivery` Postgres role: when
// SUPABASE_BUZZ_DELIVERY_KEY is a custom role JWT. Supabase requires custom
// JWTs in Authorization while `apikey` remains a project API key. Unset keeps
// today's service-role fallback, so rollback is deleting one variable.
export function buzzDeliverySupabaseConfig(
  config: ContentCatalogConfig,
  getEnv: (name: string) => string | undefined,
): BuzzDeliverySupabaseConfig {
  const scoped = (getEnv("SUPABASE_BUZZ_DELIVERY_KEY") || "").trim();
  return scoped ? { ...config, authorizationKey: scoped } : config;
}

export function hasValidBuzzDeliveryAccess(
  request: Request,
  getEnv: (name: string) => string | undefined,
): boolean {
  const expected = configuredToken(getEnv);
  const candidate = request.headers.get(BUZZ_DELIVERY_TOKEN_HEADER) || "";
  if (
    !expected
    || candidate.length < TOKEN_MINIMUM
    || candidate.length > TOKEN_MAXIMUM
  ) return false;
  return timingSafeEqual(digest(candidate), digest(expected));
}

function exactKeys(value: Record<string, unknown>, expected: string[]): boolean {
  const actual = Object.keys(value).sort();
  return actual.length === expected.length
    && actual.every((key, index) => key === [...expected].sort()[index]);
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

export function parseBuzzDeliveryAction(value: unknown): BuzzDeliveryAction {
  if (!isRecord(value) || typeof value.action !== "string") {
    throw new BuzzDeliveryError("invalid_buzz_delivery_request");
  }
  if (value.action === "claim") {
    if (
      !exactKeys(value, [
        "action", "event_id", "job_id", "channel_id", "message_sha256",
        "request_sha256", "worker_id", "lease_seconds",
      ])
      || !hash(value.event_id)
      || !uuid(value.job_id)
      || !uuid(value.channel_id)
      || !hash(value.message_sha256)
      || !hash(value.request_sha256)
      || !worker(value.worker_id)
      || !Number.isSafeInteger(value.lease_seconds)
      || Number(value.lease_seconds) < 180
      || Number(value.lease_seconds) > 600
    ) throw new BuzzDeliveryError("invalid_buzz_delivery_request");
    return {
      action: "claim",
      event_id: value.event_id,
      job_id: value.job_id.toLowerCase(),
      channel_id: value.channel_id.toLowerCase(),
      message_sha256: value.message_sha256,
      request_sha256: value.request_sha256,
      worker_id: value.worker_id,
      lease_seconds: Number(value.lease_seconds),
    };
  }
  if (value.action === "attempt") {
    if (
      !exactKeys(value, ["action", "event_id", "worker_id", "request_sha256"])
      || !hash(value.event_id)
      || !worker(value.worker_id)
      || !hash(value.request_sha256)
    ) throw new BuzzDeliveryError("invalid_buzz_delivery_request");
    return value as BuzzDeliveryAction;
  }
  if (value.action === "complete") {
    if (
      !exactKeys(value, [
        "action", "event_id", "worker_id", "request_sha256", "relay_event_id",
      ])
      || !hash(value.event_id)
      || !worker(value.worker_id)
      || !hash(value.request_sha256)
      || !hash(value.relay_event_id)
    ) throw new BuzzDeliveryError("invalid_buzz_delivery_request");
    return value as BuzzDeliveryAction;
  }
  if (value.action === "fail") {
    if (
      !exactKeys(value, [
        "action", "event_id", "worker_id", "error_code",
        "retryable_before_attempt",
      ])
      || !hash(value.event_id)
      || !worker(value.worker_id)
      || typeof value.error_code !== "string"
      || !FAILURE_CODES.has(value.error_code)
      || typeof value.retryable_before_attempt !== "boolean"
    ) throw new BuzzDeliveryError("invalid_buzz_delivery_request");
    return value as BuzzDeliveryAction;
  }
  if (value.action === "reconcile") {
    if (
      !exactKeys(value, ["action", "limit"])
      || !Number.isSafeInteger(value.limit)
      || Number(value.limit) < 1
      || Number(value.limit) > 100
    ) throw new BuzzDeliveryError("invalid_buzz_delivery_request");
    return { action: "reconcile", limit: Number(value.limit) };
  }
  throw new BuzzDeliveryError("invalid_buzz_delivery_request");
}

function rpcRequest(
  action: BuzzDeliveryAction,
  workspaceId: string,
): { name: string; body: Record<string, unknown> } {
  if (action.action === "claim") return {
    name: "claim_origintrail_buzz_delivery",
    body: {
      target_workspace_id: workspaceId,
      target_job_id: action.job_id,
      target_event_id: action.event_id,
      target_channel_id: action.channel_id,
      target_message_sha256: action.message_sha256,
      target_request_sha256: action.request_sha256,
      target_worker_id: action.worker_id,
      target_lease_seconds: action.lease_seconds,
    },
  };
  if (action.action === "attempt") return {
    name: "mark_origintrail_buzz_delivery_attempt",
    body: {
      target_workspace_id: workspaceId,
      target_event_id: action.event_id,
      target_worker_id: action.worker_id,
      target_request_sha256: action.request_sha256,
    },
  };
  if (action.action === "complete") return {
    name: "complete_origintrail_buzz_delivery",
    body: {
      target_workspace_id: workspaceId,
      target_event_id: action.event_id,
      target_worker_id: action.worker_id,
      target_request_sha256: action.request_sha256,
      target_relay_event_id: action.relay_event_id,
    },
  };
  if (action.action === "fail") return {
    name: "fail_origintrail_buzz_delivery",
    body: {
      target_workspace_id: workspaceId,
      target_event_id: action.event_id,
      target_worker_id: action.worker_id,
      target_error_code: action.error_code,
      target_retryable_before_attempt: action.retryable_before_attempt,
    },
  };
  return {
    name: "reconcile_origintrail_buzz_delivery_leases",
    body: {
      target_workspace_id: workspaceId,
      target_limit: action.limit,
    },
  };
}

function validResponse(
  action: BuzzDeliveryAction,
  raw: unknown,
  workspaceId: string,
): boolean {
  if (!isRecord(raw)) return false;
  if (action.action === "reconcile") {
    return uuid(raw.workspace_id)
      && raw.workspace_id === workspaceId
      && ["reconciled_count", "pending_count", "failed_count", "delivery_unknown_count"]
        .every((key) => Number.isSafeInteger(raw[key]) && Number(raw[key]) >= 0);
  }
  if (
    !hash(raw.event_id)
    || !uuid(raw.job_id)
    || typeof raw.status !== "string"
    || !STATUSES.has(raw.status)
    || typeof raw.reused !== "boolean"
  ) return false;
  if (raw.event_id !== action.event_id) return false;
  if (action.action === "claim") {
    return raw.job_id === action.job_id
      && raw.channel_id === action.channel_id
      && raw.message_sha256 === action.message_sha256
      && raw.request_sha256 === action.request_sha256
      && typeof raw.claim_granted === "boolean"
      && (raw.lease_expires_at === null || typeof raw.lease_expires_at === "string");
  }
  if (action.action === "attempt") {
    return raw.request_sha256 === action.request_sha256
      && typeof raw.authorized_once === "boolean";
  }
  if (action.action === "complete") {
    return raw.request_sha256 === action.request_sha256
      && raw.relay_event_id === action.relay_event_id;
  }
  return true;
}

export async function executeBuzzDeliveryAction(
  config: BuzzDeliverySupabaseConfig,
  action: BuzzDeliveryAction,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<Record<string, unknown>> {
  const rpc = rpcRequest(action, config.workspaceId);
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/${rpc.name}`, {
      method: "POST",
      headers: {
        apikey: config.serviceRoleKey,
        Authorization: `Bearer ${config.authorizationKey ?? config.serviceRoleKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(rpc.body),
      signal,
    });
  } catch {
    throw new BuzzDeliveryError("buzz_delivery_storage_unavailable");
  }
  if (!response.ok) {
    throw new BuzzDeliveryError(
      response.status === 409
        ? "buzz_delivery_receipt_conflict"
        : "buzz_delivery_storage_unavailable",
    );
  }
  let raw: unknown;
  try {
    raw = await response.json();
  } catch {
    throw new BuzzDeliveryError("buzz_delivery_invalid_response");
  }
  if (!validResponse(action, raw, config.workspaceId)) {
    throw new BuzzDeliveryError("buzz_delivery_invalid_response");
  }
  return raw as Record<string, unknown>;
}
