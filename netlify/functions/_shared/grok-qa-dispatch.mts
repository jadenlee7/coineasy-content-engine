import { createHash, timingSafeEqual } from "node:crypto";

import {
  getContentLibraryItem,
  isCatalogUuid,
  type ContentCatalogConfig,
  type ContentLibraryDetail,
} from "./content-catalog.mts";
import {
  buildGrokQaReviewPackage,
  claimGrokQaVerdict,
  finalizeGrokQaVerdict,
  GROK_QA_DECISIONS,
  GROK_QA_NEXT_ACTIONS,
  grokQaBannerImage,
  grokQaPassConflictsWithStoredBrandQa,
  grokQaRelayConfig,
  grokQaSourceUrls,
  sendGrokQaVerdictOutcome,
  type GrokQaDecision,
  type GrokQaNextAction,
  type GrokQaVerdict,
} from "./grok-qa.mts";

export const GROK_QA_DISPATCH_SCHEMA_VERSION = "1.0";
export const GROK_QA_DISPATCH_MODE = "official_x_grok_qa_dispatch";
export const GROK_QA_DISPATCH_MODEL = "grok-4.5";
export const GROK_QA_DISPATCH_PROMPT_VERSION = "official-x-grok-qa@1";
export const GROK_QA_DISPATCH_MAX_COST_TICKS = 5_000_000_000;

const TOKEN_MINIMUM_BYTES = 32;
const TOKEN_MAXIMUM_BYTES = 512;
const HASH_PATTERN = /^[a-f0-9]{64}$/;
const WORKER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/;
const PROVIDER_RESPONSE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,199}$/;
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9_]{2,63}$/;
const CONTROL_PATTERN = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;
const CLIENTS = new Set(["yellow", "origintrail", "squid", "babylon"]);
const OFFICIAL_X_HANDLES: Record<string, string> = {
  yellow: "@Yellow",
  origintrail: "@origin_trail",
  squid: "@SquidRouter",
  babylon: "@babylonlabs_io",
};
const KINDS = new Set(["daily_news"]);
const SOURCE_EVENTS = new Set([
  "official_x_review_draft_completed",
  "origintrail_batch_review_pack_materialized",
]);
const JOB_STATUSES = new Set(["claimed"]);
const TERMINAL_STATUSES = new Set(["sent", "failed", "delivery_unknown"]);
const FAIL_STATUSES = new Set(["pending", "failed", "obsolete", "provider_unknown"]);
const DISPATCH_FAILURE_CODES = new Set([
  "grok_qa_configuration_invalid",
  "grok_qa_package_unavailable",
  "grok_qa_request_failed",
  "grok_qa_response_invalid",
  "grok_qa_source_evidence_missing",
  "grok_qa_cost_limit_exceeded",
  "grok_qa_worker_failed",
  "grok_qa_provider_unknown",
  "xai_rate_limited",
  "xai_temporarily_unavailable",
  "xai_request_rejected",
  "grok_qa_client_not_allowed",
  "grok_qa_provider_result_invalid",
  "grok_qa_provider_unavailable",
  "grok_qa_input_identity_mismatch",
  "xai_qa_unavailable",
  "xai_qa_request_failed",
  "xai_qa_response_too_large",
  "xai_qa_invalid_response",
  "xai_qa_response_incomplete",
  "xai_qa_model_mismatch",
  "xai_qa_response_id_invalid",
  "xai_qa_x_search_missing",
  "xai_qa_x_search_limit_exceeded",
  "xai_qa_exact_source_not_cited",
  "xai_qa_cost_invalid",
  "xai_qa_cost_cap_exceeded",
  "xai_qa_invalid_output",
  "xai_qa_response_refused",
  "xai_qa_invalid_verdict",
]);
const RESERVED_SECRET_ENVS = [
  "GROK_QA_CONNECTOR_TOKEN",
  "GROK_QA_RELAY_TOKEN",
  "STUDIO_ACCESS_TOKEN",
  "STUDIO_AUTOMATION_TOKEN",
  "API_SECRET",
  "PUBLICATION_WORKER_TOKEN",
  "SUPABASE_SERVICE_ROLE_KEY",
  "XAI_API_KEY",
  "X_BEARER_TOKEN",
  "TYPEFULLY_API_KEY",
  "TELEGRAM_REVIEW_BOT_TOKEN",
  "TELEGRAM_CONTENT_OPS_BOT_TOKEN",
  "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN",
  "TELEGRAM_BOT_TOKEN_SQUID",
  "TELEGRAM_BOT_TOKEN_YELLOW",
  "TELEGRAM_BOT_TOKEN_ORIGINTRAIL",
  "TELEGRAM_BOT_TOKEN_BABYLON",
] as const;

type ClaimAction = {
  action: "claim";
  worker_id: string;
  lease_seconds: number;
  allowed_clients: string[];
  canary_content_version_id: string | null;
};

type StageFields = {
  content_item_id: string;
  content_version_id: string;
  worker_id: string;
  verdict: GrokQaVerdict;
  model: string;
  prompt_version: string;
  provider_response_id: string;
  input_sha256: string;
  banner_sha256: string;
  cost_in_usd_ticks: number;
  x_search_citations: string[];
  x_search_calls: number;
};

type StageAction = StageFields & {
  action: "stage";
};

type DeliverAction = StageFields & {
  action: "deliver";
  verdict_sha256: string;
};

type MarkProviderAttemptAction = {
  action: "mark_provider_attempt";
  content_item_id: string;
  content_version_id: string;
  worker_id: string;
  input_sha256: string;
  banner_sha256: string;
};

type FailAction = {
  action: "fail";
  content_item_id: string;
  content_version_id: string;
  worker_id: string;
  error_code: string;
  retryable: boolean;
  retry_at: string | null;
};

type ReconcileAction = {
  action: "reconcile";
  limit: number;
};

export type GrokQaDispatchAction =
  | ClaimAction
  | MarkProviderAttemptAction
  | StageAction
  | DeliverAction
  | FailAction
  | ReconcileAction;

export class GrokQaDispatchError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "GrokQaDispatchError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
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
  return typeof value === "string" && HASH_PATTERN.test(value);
}

function worker(value: unknown): value is string {
  return typeof value === "string" && WORKER_PATTERN.test(value);
}

function boundedText(value: unknown, minimum: number, maximum: number): value is string {
  return typeof value === "string"
    && value.length >= minimum
    && value.length <= maximum
    && value.trim() === value
    && !CONTROL_PATTERN.test(value);
}

function safeUrl(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 2_048) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:"
      && Boolean(url.hostname)
      && !url.username
      && !url.password
      && !url.hash;
  } catch {
    return false;
  }
}

function exactOfficialSource(
  clientId: unknown,
  sourceUrl: unknown,
  sourceAuthorHandle: unknown,
  sourcePublishedAt: unknown,
): sourceUrl is string {
  if (
    typeof clientId !== "string"
    || typeof sourceUrl !== "string"
    || typeof sourceAuthorHandle !== "string"
    || typeof sourcePublishedAt !== "string"
  ) return false;
  const expectedHandle = OFFICIAL_X_HANDLES[clientId];
  if (!expectedHandle || sourceAuthorHandle.toLowerCase() !== expectedHandle.toLowerCase()) {
    return false;
  }
  const published = Date.parse(sourcePublishedAt);
  if (!Number.isFinite(published) || sourcePublishedAt.length > 40) return false;
  try {
    const url = new URL(sourceUrl);
    const parts = url.pathname.split("/");
    return url.protocol === "https:"
      && url.hostname === "x.com"
      && !url.username
      && !url.password
      && !url.search
      && !url.hash
      && parts.length === 4
      && parts[0] === ""
      && `@${parts[1]}`.toLowerCase() === expectedHandle.toLowerCase()
      && parts[2] === "status"
      && /^[0-9]{1,19}$/.test(parts[3]);
  } catch {
    return false;
  }
}

function canonicalPostId(value: string): string | null {
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:"
      || url.hostname !== "x.com"
      || url.username
      || url.password
      || url.port
      || url.search
      || url.hash
    ) return null;
    const exact = url.pathname.match(/^\/[A-Za-z0-9_]{1,15}\/status\/([1-9][0-9]{0,18})\/?$/);
    const canonical = url.pathname.match(/^\/i\/status\/([1-9][0-9]{0,18})\/?$/i);
    return exact?.[1] || canonical?.[1] || null;
  } catch {
    return null;
  }
}

function citationMatchesOfficialPost(citation: string, sourceUrl: string): boolean {
  try {
    const source = new URL(sourceUrl);
    const expected = source.pathname.match(
      /^\/([A-Za-z0-9_]{1,15})\/status\/([1-9][0-9]{0,18})$/,
    );
    const candidate = new URL(citation);
    if (
      !expected
      || candidate.protocol !== "https:"
      || candidate.hostname !== "x.com"
      || candidate.username
      || candidate.password
      || candidate.port
      || candidate.search
      || candidate.hash
    ) return false;
    const exact = candidate.pathname.match(
      /^\/([A-Za-z0-9_]{1,15})\/status\/([1-9][0-9]{0,18})\/?$/,
    );
    const canonical = candidate.pathname.match(
      /^\/i\/status\/([1-9][0-9]{0,18})\/?$/i,
    );
    return Boolean(
      (exact
        && exact[1].toLowerCase() === expected[1].toLowerCase()
        && exact[2] === expected[2])
      || (canonical && canonical[1] === expected[2]),
    );
  } catch {
    return false;
  }
}

function parseVerdict(value: unknown): GrokQaVerdict {
  if (!isRecord(value) || !exactKeys(value, [
    "decision", "summary", "fact_check", "brand_check", "issues", "next_action",
  ])) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  if (
    !GROK_QA_DECISIONS.includes(value.decision as GrokQaDecision)
    || !boundedText(value.summary, 10, 800)
    || !GROK_QA_NEXT_ACTIONS.includes(value.next_action as GrokQaNextAction)
    || !isRecord(value.fact_check)
    || !exactKeys(value.fact_check, ["status", "checks", "source_urls"])
    || !GROK_QA_DECISIONS.includes(value.fact_check.status as GrokQaDecision)
    || !Array.isArray(value.fact_check.checks)
    || value.fact_check.checks.length < 1
    || value.fact_check.checks.length > 6
    || value.fact_check.checks.some((item) => !boundedText(item, 3, 300))
    || !Array.isArray(value.fact_check.source_urls)
    || value.fact_check.source_urls.length > 8
    || value.fact_check.source_urls.some((item) => !safeUrl(item))
    || new Set(value.fact_check.source_urls).size !== value.fact_check.source_urls.length
    || !isRecord(value.brand_check)
    || !exactKeys(value.brand_check, ["status", "checks"])
    || !GROK_QA_DECISIONS.includes(value.brand_check.status as GrokQaDecision)
    || !Array.isArray(value.brand_check.checks)
    || value.brand_check.checks.length < 1
    || value.brand_check.checks.length > 6
    || value.brand_check.checks.some((item) => !boundedText(item, 3, 300))
    || !Array.isArray(value.issues)
    || value.issues.length > 3
  ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");

  for (const issue of value.issues) {
    if (
      !isRecord(issue)
      || !exactKeys(issue, issue.evidence_url === undefined
        ? ["severity", "code", "message"]
        : ["severity", "code", "message", "evidence_url"])
      || !["WARN", "BLOCK"].includes(String(issue.severity))
      || typeof issue.code !== "string"
      || !/^[a-z][a-z0-9_]{2,47}$/.test(issue.code)
      || !boundedText(issue.message, 3, 500)
      || !(issue.evidence_url === undefined || safeUrl(issue.evidence_url))
    ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  }

  const verdict = value as unknown as GrokQaVerdict;
  if (verdict.decision === "PASS" && (
    verdict.fact_check.status !== "PASS"
    || verdict.brand_check.status !== "PASS"
    || verdict.issues.length !== 0
    || verdict.next_action !== "ready_for_human_approval"
  )) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  if (
    verdict.decision !== "PASS"
    && verdict.next_action === "ready_for_human_approval"
  ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  if (
    verdict.decision === "BLOCK"
    && verdict.fact_check.status !== "BLOCK"
    && verdict.brand_check.status !== "BLOCK"
    && !verdict.issues.some((issue) => issue.severity === "BLOCK")
  ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  return verdict;
}

function configuredToken(
  getEnv: (name: string) => string | undefined,
): string | null {
  const token = getEnv("GROK_QA_DISPATCH_TOKEN") || "";
  const bytes = Buffer.byteLength(token, "utf8");
  const reused = RESERVED_SECRET_ENVS.some((name) => {
    const other = (getEnv(name) || "").trim();
    return Boolean(other) && other === token;
  });
  return bytes >= TOKEN_MINIMUM_BYTES
    && bytes <= TOKEN_MAXIMUM_BYTES
    && !/[^\x21-\x7e]/.test(token)
    && !reused
    ? token
    : null;
}

export function grokQaDispatchAccessConfigured(
  getEnv: (name: string) => string | undefined,
): boolean {
  return configuredToken(getEnv) !== null;
}

export function hasGrokQaDispatchAccess(
  request: Request,
  getEnv: (name: string) => string | undefined,
): boolean {
  const expected = configuredToken(getEnv);
  const header = request.headers.get("authorization") || "";
  if (!expected || !header.startsWith("Bearer ")) return false;
  const supplied = header.slice(7);
  if (
    supplied !== supplied.trim()
    || Buffer.byteLength(supplied, "utf8") < TOKEN_MINIMUM_BYTES
    || Buffer.byteLength(supplied, "utf8") > TOKEN_MAXIMUM_BYTES
  ) return false;
  const actualHash = createHash("sha256").update(supplied, "utf8").digest();
  const expectedHash = createHash("sha256").update(expected, "utf8").digest();
  return timingSafeEqual(actualHash, expectedHash);
}

function parseStageFields(value: Record<string, unknown>): StageFields {
  if (
    !uuid(value.content_item_id)
    || !uuid(value.content_version_id)
    || !worker(value.worker_id)
    || value.model !== GROK_QA_DISPATCH_MODEL
    || value.prompt_version !== GROK_QA_DISPATCH_PROMPT_VERSION
    || typeof value.provider_response_id !== "string"
    || !PROVIDER_RESPONSE_ID_PATTERN.test(value.provider_response_id)
    || !hash(value.input_sha256)
    || !hash(value.banner_sha256)
    || !Number.isSafeInteger(value.cost_in_usd_ticks)
    || Number(value.cost_in_usd_ticks) < 0
    || Number(value.cost_in_usd_ticks) > GROK_QA_DISPATCH_MAX_COST_TICKS
    || !Number.isSafeInteger(value.x_search_calls)
    || Number(value.x_search_calls) < 1
    || Number(value.x_search_calls) > 3
    || !Array.isArray(value.x_search_citations)
    || value.x_search_citations.length < 1
    || value.x_search_citations.length > 8
    || value.x_search_citations.some((citation) => !safeUrl(citation))
    || new Set(value.x_search_citations).size !== value.x_search_citations.length
  ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  return {
    content_item_id: value.content_item_id.toLowerCase(),
    content_version_id: value.content_version_id.toLowerCase(),
    worker_id: value.worker_id,
    verdict: parseVerdict(value.verdict),
    model: value.model,
    prompt_version: value.prompt_version,
    provider_response_id: value.provider_response_id,
    input_sha256: value.input_sha256,
    banner_sha256: value.banner_sha256,
    cost_in_usd_ticks: Number(value.cost_in_usd_ticks),
    x_search_citations: [...value.x_search_citations] as string[],
    x_search_calls: Number(value.x_search_calls),
  };
}

export function parseGrokQaDispatchAction(value: unknown): GrokQaDispatchAction {
  if (!isRecord(value) || typeof value.action !== "string") {
    throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
  }
  if (value.action === "claim") {
    if (
      !exactKeys(value, [
        "action", "worker_id", "lease_seconds", "allowed_clients",
        "canary_content_version_id",
      ])
      || !worker(value.worker_id)
      || !Number.isSafeInteger(value.lease_seconds)
      || Number(value.lease_seconds) < 180
      || Number(value.lease_seconds) > 600
      || !Array.isArray(value.allowed_clients)
      || value.allowed_clients.length < 1
      || value.allowed_clients.length > CLIENTS.size
      || value.allowed_clients.some((client) => !CLIENTS.has(String(client)))
      || new Set(value.allowed_clients).size !== value.allowed_clients.length
      || !(value.canary_content_version_id === null || uuid(value.canary_content_version_id))
    ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
    return {
      action: "claim",
      worker_id: value.worker_id,
      lease_seconds: Number(value.lease_seconds),
      allowed_clients: [...value.allowed_clients] as string[],
      canary_content_version_id: value.canary_content_version_id === null
        ? null
        : value.canary_content_version_id.toLowerCase(),
    };
  }
  if (value.action === "stage") {
    if (!exactKeys(value, [
      "action", "content_item_id", "content_version_id", "worker_id",
      "verdict", "model", "prompt_version", "provider_response_id",
      "input_sha256", "banner_sha256", "cost_in_usd_ticks", "x_search_citations",
      "x_search_calls",
    ])) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
    return { action: "stage", ...parseStageFields(value) };
  }
  if (value.action === "deliver") {
    if (!exactKeys(value, [
      "action", "content_item_id", "content_version_id", "worker_id",
      "verdict", "verdict_sha256", "model", "prompt_version", "provider_response_id",
      "input_sha256", "banner_sha256", "cost_in_usd_ticks", "x_search_citations",
      "x_search_calls",
    ]) || !hash(value.verdict_sha256)) {
      throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
    }
    return {
      action: "deliver",
      ...parseStageFields(value),
      verdict_sha256: value.verdict_sha256,
    };
  }
  if (value.action === "mark_provider_attempt") {
    if (
      !exactKeys(value, [
        "action", "content_item_id", "content_version_id", "worker_id", "input_sha256",
        "banner_sha256",
      ])
      || !uuid(value.content_item_id)
      || !uuid(value.content_version_id)
      || !worker(value.worker_id)
      || !hash(value.input_sha256)
      || !hash(value.banner_sha256)
    ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
    return {
      action: "mark_provider_attempt",
      content_item_id: value.content_item_id.toLowerCase(),
      content_version_id: value.content_version_id.toLowerCase(),
      worker_id: value.worker_id,
      input_sha256: value.input_sha256,
      banner_sha256: value.banner_sha256,
    };
  }
  if (value.action === "fail") {
    if (
      !exactKeys(value, [
        "action", "content_item_id", "content_version_id", "worker_id",
        "error_code", "retryable", "retry_at",
      ])
      || !uuid(value.content_item_id)
      || !uuid(value.content_version_id)
      || !worker(value.worker_id)
      || typeof value.error_code !== "string"
      || !ERROR_CODE_PATTERN.test(value.error_code)
      || !DISPATCH_FAILURE_CODES.has(value.error_code)
      || typeof value.retryable !== "boolean"
      || (value.retryable
        ? typeof value.retry_at !== "string"
          || value.retry_at.length > 40
          || !Number.isFinite(Date.parse(value.retry_at))
        : value.retry_at !== null)
    ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
    return {
      action: "fail",
      content_item_id: value.content_item_id.toLowerCase(),
      content_version_id: value.content_version_id.toLowerCase(),
      worker_id: value.worker_id,
      error_code: value.error_code,
      retryable: value.retryable,
      retry_at: value.retry_at as string | null,
    };
  }
  if (value.action === "reconcile") {
    if (
      !exactKeys(value, ["action", "limit"])
      || !Number.isSafeInteger(value.limit)
      || Number(value.limit) < 1
      || Number(value.limit) > 100
    ) throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
    return { action: "reconcile", limit: Number(value.limit) };
  }
  throw new GrokQaDispatchError("invalid_grok_qa_dispatch_request");
}

async function rpc(
  config: ContentCatalogConfig,
  name: string,
  body: Record<string, unknown>,
  fetcher: typeof fetch,
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/${name}`, {
      method: "POST",
      headers: {
        apikey: config.serviceRoleKey,
        Authorization: `Bearer ${config.serviceRoleKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new GrokQaDispatchError("grok_qa_dispatch_storage_unavailable");
  }
  if (!response.ok) {
    throw new GrokQaDispatchError(
      response.status === 409
        ? "grok_qa_dispatch_conflict"
        : "grok_qa_dispatch_storage_unavailable",
    );
  }
  try {
    return await response.json();
  } catch {
    throw new GrokQaDispatchError("grok_qa_dispatch_invalid_response");
  }
}

async function exactReviewDetail(
  config: ContentCatalogConfig,
  itemId: string,
  versionId: string,
  fetcher: typeof fetch,
): Promise<ContentLibraryDetail> {
  const noRedirectFetcher: typeof fetch = (input, init) => fetcher(input, {
    ...init,
    redirect: "error",
  });
  const detail = await getContentLibraryItem(
    config,
    itemId,
    noRedirectFetcher,
    60,
    AbortSignal.timeout(10_000),
  );
  if (!detail) throw new GrokQaDispatchError("grok_qa_dispatch_item_not_found");
  if (detail.current_version_id !== versionId) {
    throw new GrokQaDispatchError("grok_qa_dispatch_version_conflict");
  }
  if (
    detail.status !== "needs_review"
    || detail.current_version.generation_meta.mock_mode === true
  ) throw new GrokQaDispatchError("grok_qa_dispatch_status_conflict");
  return detail;
}

function validateVerdictSources(detail: ContentLibraryDetail, verdict: GrokQaVerdict): void {
  const stored = grokQaSourceUrls(detail);
  const allowed = new Set(stored);
  if (
    verdict.fact_check.source_urls.some((url) => !allowed.has(url))
    || verdict.issues.some((issue) => (
      Boolean(issue.evidence_url) && !allowed.has(issue.evidence_url!)
    ))
    || (verdict.decision === "PASS" && stored.length === 0)
  ) throw new GrokQaDispatchError("grok_qa_dispatch_source_mismatch");
}

function validateStageResponse(
  raw: unknown,
  action: StageAction | DeliverAction,
): Record<string, unknown> {
  if (
    !isRecord(raw)
    || !exactKeys(raw, [
      "schema_version", "content_item_id", "content_version_id", "status",
      "verdict_sha256", "model", "prompt_version", "provider_response_id",
      "input_sha256", "banner_sha256", "cost_in_usd_ticks", "x_search_citations",
      "x_search_calls",
      "reused",
    ])
    || raw.schema_version !== GROK_QA_DISPATCH_SCHEMA_VERSION
    || raw.content_item_id !== action.content_item_id
    || raw.content_version_id !== action.content_version_id
    || raw.status !== "claimed"
    || !hash(raw.verdict_sha256)
    || raw.model !== action.model
    || raw.prompt_version !== action.prompt_version
    || raw.provider_response_id !== action.provider_response_id
    || raw.input_sha256 !== action.input_sha256
    || raw.banner_sha256 !== action.banner_sha256
    || raw.cost_in_usd_ticks !== action.cost_in_usd_ticks
    || JSON.stringify(raw.x_search_citations) !== JSON.stringify(action.x_search_citations)
    || raw.x_search_calls !== action.x_search_calls
    || typeof raw.reused !== "boolean"
  ) throw new GrokQaDispatchError("grok_qa_dispatch_invalid_response");
  return raw;
}

async function stageVerdict(
  config: ContentCatalogConfig,
  action: StageAction | DeliverAction,
  fetcher: typeof fetch,
): Promise<{ detail: ContentLibraryDetail; result: Record<string, unknown> }> {
  const detail = await exactReviewDetail(
    config,
    action.content_item_id,
    action.content_version_id,
    fetcher,
  );
  if (grokQaPassConflictsWithStoredBrandQa(detail, action.verdict)) {
    throw new GrokQaDispatchError("grok_qa_dispatch_brand_qa_conflict");
  }
  validateVerdictSources(detail, action.verdict);
  const expectedSource = grokQaSourceUrls(detail)[0] || "";
  if (
    !canonicalPostId(expectedSource)
    || action.x_search_citations.some(
      (citation) => !citationMatchesOfficialPost(citation, expectedSource),
    )
  ) throw new GrokQaDispatchError("grok_qa_dispatch_source_mismatch");
  const raw = await rpc(config, "stage_grok_qa_dispatch_verdict", {
    target_workspace_id: config.workspaceId,
    target_content_version_id: action.content_version_id,
    target_worker_id: action.worker_id,
    target_verdict: action.verdict,
    target_model: action.model,
    target_prompt_version: action.prompt_version,
    target_provider_response_id: action.provider_response_id,
    target_input_sha256: action.input_sha256,
    target_banner_sha256: action.banner_sha256,
    target_cost_in_usd_ticks: action.cost_in_usd_ticks,
    target_x_search_citations: action.x_search_citations,
    target_x_search_calls: action.x_search_calls,
  }, fetcher);
  return { detail, result: validateStageResponse(raw, action) };
}

function validateCompleteResponse(
  raw: unknown,
  action: DeliverAction,
  expected: "sent" | "failed" | "delivery_unknown",
): Record<string, unknown> {
  if (
    !isRecord(raw)
    || !exactKeys(raw, [
      "schema_version", "content_item_id", "content_version_id", "status", "reused",
    ])
    || raw.schema_version !== GROK_QA_DISPATCH_SCHEMA_VERSION
    || raw.content_item_id !== action.content_item_id
    || raw.content_version_id !== action.content_version_id
    || (expected === "delivery_unknown"
      ? !TERMINAL_STATUSES.has(String(raw.status))
      : raw.status !== expected)
    || typeof raw.reused !== "boolean"
  ) throw new GrokQaDispatchError("grok_qa_dispatch_invalid_response");
  return raw;
}

async function completeDispatch(
  config: ContentCatalogConfig,
  action: DeliverAction,
  outcome: "sent" | "failed" | "delivery_unknown",
  errorCode: string | null,
  fetcher: typeof fetch,
): Promise<Record<string, unknown>> {
  const raw = await rpc(config, "complete_grok_qa_dispatch_job", {
    target_workspace_id: config.workspaceId,
    target_content_version_id: action.content_version_id,
    target_worker_id: action.worker_id,
    target_verdict_sha256: action.verdict_sha256,
    target_outcome: outcome,
    target_error_code: errorCode,
  }, fetcher);
  return validateCompleteResponse(raw, action, outcome);
}

function reviewUrl(itemId: string): string {
  const url = new URL("https://coineasy-newscard.netlify.app/");
  url.searchParams.set("view", "library");
  url.searchParams.set("content", itemId);
  return url.toString();
}

export async function executeGrokQaDispatchAction(
  config: ContentCatalogConfig,
  action: GrokQaDispatchAction,
  getEnv: (name: string) => string | undefined,
  fetcher: typeof fetch = fetch,
): Promise<Record<string, unknown>> {
  if (action.action === "claim") {
    const raw = await rpc(config, "claim_grok_qa_dispatch_job", {
      target_workspace_id: config.workspaceId,
      target_worker_id: action.worker_id,
      target_lease_seconds: action.lease_seconds,
      target_allowed_clients: action.allowed_clients,
      target_canary_content_version_id: action.canary_content_version_id,
    }, fetcher);
    if (
      !isRecord(raw)
      || !exactKeys(raw, ["schema_version", "mode", "workspace_id", "job"])
      || raw.schema_version !== GROK_QA_DISPATCH_SCHEMA_VERSION
      || raw.mode !== GROK_QA_DISPATCH_MODE
      || raw.workspace_id !== config.workspaceId
      || !(raw.job === null || isRecord(raw.job))
    ) throw new GrokQaDispatchError("grok_qa_dispatch_invalid_response");
    if (raw.job === null) return raw;
    const job = raw.job;
    if (
      !exactKeys(job, [
        "content_item_id", "content_version_id", "client_id", "content_kind",
        "source_item_id", "source_url", "source_author_handle", "source_published_at",
        "source_event_id", "source_event_type", "status", "attempts", "max_attempts",
        "lease_expires_at", "verdict", "verdict_sha256", "model", "prompt_version",
        "input_sha256", "banner_sha256", "provider_attempt_started_at", "provider_response_id",
        "cost_in_usd_ticks", "x_search_citations", "x_search_calls",
        "provider_call_required", "claim_granted",
      ])
      || !uuid(job.content_item_id)
      || !uuid(job.content_version_id)
      || (action.canary_content_version_id !== null
        && String(job.content_version_id).toLowerCase() !== action.canary_content_version_id)
      || !uuid(job.source_item_id)
      || !CLIENTS.has(String(job.client_id))
      || !KINDS.has(String(job.content_kind))
      || !exactOfficialSource(
        job.client_id,
        job.source_url,
        job.source_author_handle,
        job.source_published_at,
      )
      || !Number.isSafeInteger(job.source_event_id)
      || Number(job.source_event_id) < 1
      || !SOURCE_EVENTS.has(String(job.source_event_type))
      || !JOB_STATUSES.has(String(job.status))
      || !Number.isSafeInteger(job.attempts)
      || Number(job.attempts) < 1
      || !Number.isSafeInteger(job.max_attempts)
      || Number(job.max_attempts) < Number(job.attempts)
      || typeof job.lease_expires_at !== "string"
      || !Number.isFinite(Date.parse(job.lease_expires_at))
      || job.claim_granted !== true
      || !(job.verdict === null || isRecord(job.verdict))
      || !(job.verdict_sha256 === null || hash(job.verdict_sha256))
      || !(job.model === null || job.model === GROK_QA_DISPATCH_MODEL)
      || !(job.prompt_version === null || job.prompt_version === GROK_QA_DISPATCH_PROMPT_VERSION)
      || !(job.input_sha256 === null || hash(job.input_sha256))
      || !(job.banner_sha256 === null || hash(job.banner_sha256))
      || !(job.provider_attempt_started_at === null || (
        typeof job.provider_attempt_started_at === "string"
        && Number.isFinite(Date.parse(job.provider_attempt_started_at))
      ))
      || ((job.input_sha256 === null) !== (job.provider_attempt_started_at === null))
      || ((job.input_sha256 === null) !== (job.banner_sha256 === null))
      || !(job.provider_response_id === null || (
        typeof job.provider_response_id === "string"
        && PROVIDER_RESPONSE_ID_PATTERN.test(job.provider_response_id)
      ))
      || !(job.cost_in_usd_ticks === null || (
        Number.isSafeInteger(job.cost_in_usd_ticks)
        && Number(job.cost_in_usd_ticks) >= 0
        && Number(job.cost_in_usd_ticks) <= GROK_QA_DISPATCH_MAX_COST_TICKS
      ))
      || !(job.x_search_citations === null || (
        Array.isArray(job.x_search_citations)
        && job.x_search_citations.length >= 1
        && job.x_search_citations.length <= 8
        && job.x_search_citations.every((citation) => safeUrl(citation))
        && new Set(job.x_search_citations).size === job.x_search_citations.length
        && job.x_search_citations.every(
          (citation) => citationMatchesOfficialPost(citation, String(job.source_url)),
        )
      ))
      || !(job.x_search_calls === null || (
        Number.isSafeInteger(job.x_search_calls)
        && Number(job.x_search_calls) >= 1
        && Number(job.x_search_calls) <= 3
      ))
      || typeof job.provider_call_required !== "boolean"
      || ((job.provider_response_id === null) !== (job.cost_in_usd_ticks === null))
      || ((job.provider_response_id === null) !== (job.x_search_citations === null))
      || ((job.provider_response_id === null) !== (job.x_search_calls === null))
      || ((job.verdict === null) !== (job.verdict_sha256 === null))
      || ((job.verdict === null) !== (job.model === null))
      || ((job.verdict === null) !== (job.prompt_version === null))
      || ((job.verdict === null) !== (job.provider_response_id === null))
      || job.provider_call_required !== (job.verdict === null)
    ) throw new GrokQaDispatchError("grok_qa_dispatch_invalid_response");

    const itemId = String(job.content_item_id).toLowerCase();
    const versionId = String(job.content_version_id).toLowerCase();
    const detail = await exactReviewDetail(config, itemId, versionId, fetcher);
    if (detail.client_id !== job.client_id || detail.content_kind !== job.content_kind) {
      throw new GrokQaDispatchError("grok_qa_dispatch_identity_conflict");
    }
    if (!action.allowed_clients.includes(detail.client_id)) {
      throw new GrokQaDispatchError("grok_qa_dispatch_identity_conflict");
    }
    if (!grokQaSourceUrls(detail).includes(String(job.source_url))) {
      throw new GrokQaDispatchError("grok_qa_dispatch_source_mismatch");
    }
    let storedVerdict: GrokQaVerdict | null = null;
    if (job.verdict !== null) {
      storedVerdict = parseVerdict(job.verdict);
      validateVerdictSources(detail, storedVerdict);
    }
    const reviewPackage = {
      ...buildGrokQaReviewPackage(detail),
      // The durable outbox freezes one position-0 official X source. Do not
      // expose secondary links as alternative factual authority to the model.
      source_urls: [String(job.source_url)],
    };
    if (
      reviewPackage.banner.available !== true
      || !hash(reviewPackage.banner.sha256)
      || (job.banner_sha256 !== null
        && job.banner_sha256 !== reviewPackage.banner.sha256)
    ) throw new GrokQaDispatchError("grok_qa_dispatch_banner_conflict");
    const image = await grokQaBannerImage(
      detail,
      fetcher,
      job.banner_sha256 === null
        ? reviewPackage.banner.sha256
        : String(job.banner_sha256),
    );
    if (!image || image.sha256 !== reviewPackage.banner.sha256) {
      throw new GrokQaDispatchError("grok_qa_dispatch_banner_conflict");
    }
    return {
      schema_version: GROK_QA_DISPATCH_SCHEMA_VERSION,
      mode: GROK_QA_DISPATCH_MODE,
      workspace_id: config.workspaceId,
      job: {
        content_item_id: itemId,
        content_version_id: versionId,
        client_id: job.client_id,
        content_kind: job.content_kind,
        source_item_id: String(job.source_item_id).toLowerCase(),
        source_url: job.source_url,
        source_author_handle: job.source_author_handle,
        source_published_at: job.source_published_at,
        source_event_id: job.source_event_id,
        source_event_type: job.source_event_type,
        status: "claimed",
        attempts: job.attempts,
        max_attempts: job.max_attempts,
        lease_expires_at: job.lease_expires_at,
        verdict: storedVerdict,
        verdict_sha256: job.verdict_sha256,
        model: job.model,
        prompt_version: job.prompt_version,
        input_sha256: job.input_sha256,
        banner_sha256: job.banner_sha256,
        provider_attempt_started_at: job.provider_attempt_started_at,
        provider_response_id: job.provider_response_id,
        cost_in_usd_ticks: job.cost_in_usd_ticks,
        x_search_citations: job.x_search_citations,
        x_search_calls: job.x_search_calls,
        provider_call_required: job.provider_call_required,
        claim_granted: true,
      },
      review_package: reviewPackage,
      banner_image: image
        ? { data: image.data, mime_type: image.mimeType }
        : null,
    };
  }

  if (action.action === "mark_provider_attempt") {
    const raw = await rpc(config, "mark_grok_qa_dispatch_provider_attempt", {
      target_workspace_id: config.workspaceId,
      target_content_version_id: action.content_version_id,
      target_worker_id: action.worker_id,
      target_input_sha256: action.input_sha256,
      target_banner_sha256: action.banner_sha256,
    }, fetcher);
    const attemptStartedAt = isRecord(raw) && typeof raw.provider_attempt_started_at === "string"
      ? Date.parse(raw.provider_attempt_started_at)
      : Number.NaN;
    if (
      !isRecord(raw)
      || !exactKeys(raw, [
        "schema_version", "authorized_once", "content_item_id", "content_version_id",
        "input_sha256", "banner_sha256", "provider_attempt_started_at",
      ])
      || raw.schema_version !== GROK_QA_DISPATCH_SCHEMA_VERSION
      || raw.authorized_once !== true
      || raw.content_item_id !== action.content_item_id
      || raw.content_version_id !== action.content_version_id
      || raw.input_sha256 !== action.input_sha256
      || raw.banner_sha256 !== action.banner_sha256
      || !Number.isFinite(attemptStartedAt)
      || Math.abs(Date.now() - attemptStartedAt) > 120_000
    ) throw new GrokQaDispatchError("grok_qa_dispatch_invalid_response");
    return raw;
  }

  if (action.action === "stage") {
    const staged = await stageVerdict(config, action, fetcher);
    return staged.result;
  }

  if (action.action === "deliver") {
    const { detail, result: staged } = await stageVerdict(config, action, fetcher);
    if (staged.verdict_sha256 !== action.verdict_sha256) {
      throw new GrokQaDispatchError("grok_qa_dispatch_verdict_conflict");
    }
    const relay = grokQaRelayConfig(getEnv);
    if (!relay) throw new GrokQaDispatchError("grok_qa_dispatch_relay_not_configured");

    // Verify the current signed object bytes against the SHA durably fenced
    // before the receipt claim. Keep these exact bytes in memory so the receipt
    // claim and Telegram relay cannot observe a later asset-row/object swap.
    const relayBanner = await grokQaBannerImage(
      detail,
      fetcher,
      String(staged.banner_sha256),
    );
    if (!relayBanner || relayBanner.sha256 !== staged.banner_sha256) {
      throw new GrokQaDispatchError("grok_qa_dispatch_banner_conflict");
    }

    const receipt = await claimGrokQaVerdict(
      config,
      action.content_item_id,
      action.content_version_id,
      action.verdict,
      fetcher,
    );
    if (receipt.status === "duplicate_conflict") {
      throw new GrokQaDispatchError("grok_qa_dispatch_verdict_conflict");
    }
    if (!receipt.claimed) {
      const outcome = receipt.status === "sent"
        ? "sent"
        : receipt.status === "failed"
          ? "failed"
          : "delivery_unknown";
      const completed = await completeDispatch(
        config,
        action,
        outcome,
        outcome === "sent" ? null : outcome === "failed"
          ? "telegram_delivery_failed"
          : "qa_delivery_state_unknown",
        fetcher,
      );
      return {
        ...completed,
        delivered: outcome === "sent",
        duplicate: true,
        ...(outcome === "sent" ? {
          accepted: true,
          delivery_status: "duplicate",
        } : {}),
        advisory_only: true,
        public_publish: false,
      };
    }
    if (receipt.payload_sha256 !== action.verdict_sha256) {
      throw new GrokQaDispatchError("grok_qa_dispatch_verdict_conflict");
    }

    const relayOutcome = await sendGrokQaVerdictOutcome(
      relay,
      detail,
      action.verdict,
      reviewUrl(action.content_item_id),
      fetcher,
      action.banner_sha256,
      relayBanner,
    );
    if (relayOutcome === "delivery_unknown") {
      const completed = await completeDispatch(
        config,
        action,
        "delivery_unknown",
        "qa_delivery_state_unknown",
        fetcher,
      );
      return {
        ...completed,
        delivered: completed.status === "sent",
        duplicate: false,
        ...(completed.status === "sent" ? {
          accepted: true,
          delivery_status: "sent",
        } : {}),
        advisory_only: true,
        public_publish: false,
      };
    }
    const sent = relayOutcome === "sent";
    try {
      await finalizeGrokQaVerdict(
        config,
        action.content_version_id,
        action.verdict_sha256,
        sent ? "sent" : "failed",
        sent ? null : "telegram_delivery_failed",
        fetcher,
      );
    } catch {
      const completed = await completeDispatch(
        config,
        action,
        "delivery_unknown",
        "qa_delivery_state_unknown",
        fetcher,
      );
      return {
        ...completed,
        delivered: completed.status === "sent",
        duplicate: false,
        ...(completed.status === "sent" ? {
          accepted: true,
          delivery_status: "sent",
        } : {}),
        advisory_only: true,
        public_publish: false,
      };
    }
    const outcome = sent ? "sent" : "failed";
    const completed = await completeDispatch(
      config,
      action,
      outcome,
      sent ? null : "telegram_delivery_failed",
      fetcher,
    );
    return {
      ...completed,
      delivered: sent,
      duplicate: false,
      ...(sent ? { accepted: true, delivery_status: "sent" } : {}),
      advisory_only: true,
      public_publish: false,
    };
  }

  if (action.action === "fail") {
    const raw = await rpc(config, "fail_grok_qa_dispatch_job", {
      target_workspace_id: config.workspaceId,
      target_content_version_id: action.content_version_id,
      target_worker_id: action.worker_id,
      target_error_code: action.error_code,
      target_retryable: action.retryable,
      target_retry_at: action.retry_at,
    }, fetcher);
    if (
      !isRecord(raw)
      || !exactKeys(raw, [
        "schema_version", "content_item_id", "content_version_id", "status",
        "attempts", "max_attempts", "available_at", "reused",
      ])
      || raw.schema_version !== GROK_QA_DISPATCH_SCHEMA_VERSION
      || raw.content_item_id !== action.content_item_id
      || raw.content_version_id !== action.content_version_id
      || !FAIL_STATUSES.has(String(raw.status))
      || !Number.isSafeInteger(raw.attempts)
      || !Number.isSafeInteger(raw.max_attempts)
      || Number(raw.attempts) < 1
      || Number(raw.max_attempts) < Number(raw.attempts)
      || !(raw.available_at === null || (
        typeof raw.available_at === "string"
        && Number.isFinite(Date.parse(raw.available_at))
      ))
      || raw.reused !== false
    ) throw new GrokQaDispatchError("grok_qa_dispatch_invalid_response");
    return raw;
  }

  const raw = await rpc(config, "reconcile_grok_qa_dispatch_leases", {
    target_workspace_id: config.workspaceId,
    target_limit: action.limit,
  }, fetcher);
  if (
    !isRecord(raw)
      || !exactKeys(raw, [
        "schema_version", "workspace_id", "reconciled", "pending", "sent",
        "failed", "obsolete", "provider_unknown", "delivery_unknown",
    ])
    || raw.schema_version !== GROK_QA_DISPATCH_SCHEMA_VERSION
    || raw.workspace_id !== config.workspaceId
    || [
      "reconciled", "pending", "sent", "failed", "obsolete",
      "provider_unknown", "delivery_unknown",
    ]
      .some((key) => !Number.isSafeInteger(raw[key]) || Number(raw[key]) < 0)
  ) throw new GrokQaDispatchError("grok_qa_dispatch_invalid_response");
  return raw;
}

export { parseVerdict as parseGrokQaDispatchVerdict };
