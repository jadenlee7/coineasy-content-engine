import { createHash } from "node:crypto";

import {
  contentCatalogConfig,
  isCatalogUuid,
  type ContentCatalogConfig,
} from "./content-catalog.mts";

export const MAX_BATCH_REVIEW_LIMIT = 50;

const BATCH_REVIEW_CLIENT = "origintrail";
const BATCH_REVIEW_AGENT = "origintrail_client_agent";
const BATCH_REVIEW_WORKFLOW = "official_source_nonurgent_pack";
const BATCH_REVIEW_STATUS = "completed";
const BATCH_REVIEW_RESULT_CODE = "needs_review";
const MAX_RESULT_JSON_BYTES = 32 * 1024;
const MAX_SOURCE_URL_LENGTH = 2_048;
const MAX_SOURCE_CONTENT_LENGTH = 60_000;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const AGENT_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const MODEL_BY_TIER = {
  S: "gpt-5.6-luna",
  M: "gpt-5.6-terra",
} as const;
const RESULT_FIELD_LIMITS = {
  headline_ko: 120,
  body_ko: 1_800,
  x_copy_ko: 500,
  telegram_copy_ko: 1_800,
} as const;

export type BatchReviewConfig = ContentCatalogConfig & {
  authorizationKey?: string;
};

export type BatchReviewListItem = {
  ref: string;
  job_id: string;
  client_id: "origintrail";
  agent_id: "origintrail_client_agent";
  workflow_kind: "official_source_nonurgent_pack";
  stage: "generate";
  status: "completed";
  model: "gpt-5.6-luna" | "gpt-5.6-terra";
  model_tier: "S" | "M";
  title: string;
  result_code: "needs_review";
  actual_cost_microusd: number;
  finished_at: string;
  source_url: string | null;
};

export type BatchReviewCursor = {
  finished_at: string;
  job_id: string;
};

export type BatchReviewPage = {
  items: BatchReviewListItem[];
  next_cursor: BatchReviewCursor | null;
};

export type BatchReviewPayload = {
  headline_ko: string;
  body_ko: string;
  x_copy_ko: string;
  telegram_copy_ko: string;
};

export type BatchReviewDetail = BatchReviewListItem & {
  result_payload: BatchReviewPayload;
  source_content: string | null;
  source_evidence: {
    storage: "inline" | "hash_only_archive";
    content_length: number;
    content_sha256: string;
    verified_at: string;
  };
  input_sha256: string;
  actual_input_tokens: number;
  actual_output_tokens: number;
};

export class BatchReviewError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "BatchReviewError";
    this.code = code;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function validDate(value: unknown): value is string {
  return typeof value === "string"
    && value.length >= 20
    && value.length <= 40
    && Number.isFinite(Date.parse(value));
}

function validBoundedInteger(value: unknown, maximum: number): value is number {
  return Number.isSafeInteger(value) && Number(value) >= 0 && Number(value) <= maximum;
}

function safeSourceUrl(value: unknown): string | null | undefined {
  if (value === null) return null;
  if (
    typeof value !== "string"
    || value.length > MAX_SOURCE_URL_LENGTH
    || value.trim() !== value
  ) return undefined;
  try {
    const url = new URL(value);
    if (
      !["https:", "http:"].includes(url.protocol)
      || url.username
      || url.password
      || !url.hostname
    ) return undefined;
    return url.href;
  } catch {
    return undefined;
  }
}

function exactBatchResultPayload(value: unknown): BatchReviewPayload | null {
  let byteLength = Number.POSITIVE_INFINITY;
  try {
    byteLength = Buffer.byteLength(JSON.stringify(value), "utf8");
  } catch {
    return null;
  }
  if (byteLength > MAX_RESULT_JSON_BYTES || !isRecord(value)) return null;
  const keys = Object.keys(value);
  const expectedKeys = Object.keys(RESULT_FIELD_LIMITS);
  if (
    keys.length !== expectedKeys.length
    || keys.some(key => !Object.hasOwn(RESULT_FIELD_LIMITS, key))
  ) return null;
  for (const [key, maximum] of Object.entries(RESULT_FIELD_LIMITS)) {
    const field = value[key];
    if (
      typeof field !== "string"
      || field.trim().length < 1
      || field.length > maximum
    ) return null;
  }
  return {
    headline_ko: value.headline_ko as string,
    body_ko: value.body_ko as string,
    x_copy_ko: value.x_copy_ko as string,
    telegram_copy_ko: value.telegram_copy_ko as string,
  };
}

function exactSourceContent(value: unknown): string | null {
  if (
    typeof value !== "string"
    || value.trim().length < 1
    || value.length > MAX_SOURCE_CONTENT_LENGTH
  ) return null;
  return value;
}

function parseListItem(value: unknown): BatchReviewListItem | null {
  if (!isRecord(value)) return null;
  const modelTier = value.model_tier;
  const sourceUrl = safeSourceUrl(value.source_url);
  if (
    !isCatalogUuid(value.job_id)
    || value.client_id !== BATCH_REVIEW_CLIENT
    || value.agent_id !== BATCH_REVIEW_AGENT
    || !AGENT_ID_PATTERN.test(value.agent_id)
    || value.workflow_kind !== BATCH_REVIEW_WORKFLOW
    || value.stage !== "generate"
    || value.status !== BATCH_REVIEW_STATUS
    || !(modelTier === "S" || modelTier === "M")
    || value.model !== MODEL_BY_TIER[modelTier]
    || typeof value.title !== "string"
    || !value.title.trim()
    || value.title.length > 200
    || value.result_code !== BATCH_REVIEW_RESULT_CODE
    || !validBoundedInteger(value.actual_cost_microusd, 500_000)
    || !validDate(value.finished_at)
    || sourceUrl === undefined
  ) return null;

  const jobId = value.job_id.toLowerCase();
  return {
    ref: `batch:${jobId}`,
    job_id: jobId,
    client_id: BATCH_REVIEW_CLIENT,
    agent_id: value.agent_id,
    workflow_kind: BATCH_REVIEW_WORKFLOW,
    stage: value.stage,
    status: BATCH_REVIEW_STATUS,
    model: value.model,
    model_tier: modelTier,
    title: value.title.trim(),
    result_code: BATCH_REVIEW_RESULT_CODE,
    actual_cost_microusd: value.actual_cost_microusd,
    finished_at: value.finished_at,
    source_url: sourceUrl,
  };
}

function rpcHeaders(config: BatchReviewConfig): Record<string, string> {
  return {
    apikey: config.serviceRoleKey,
    Authorization: `Bearer ${config.authorizationKey ?? config.serviceRoleKey}`,
    "Content-Type": "application/json",
  };
}

async function rpcJson(
  config: BatchReviewConfig,
  name: string,
  body: Record<string, unknown>,
  fetcher: typeof fetch,
  signal: AbortSignal,
): Promise<unknown> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/${name}`, {
      method: "POST",
      headers: rpcHeaders(config),
      body: JSON.stringify(body),
      signal,
    });
  } catch {
    throw new BatchReviewError("batch_review_unavailable");
  }
  if (!response.ok) throw new BatchReviewError("batch_review_unavailable");
  try {
    return await response.json();
  } catch {
    throw new BatchReviewError("batch_review_invalid_response");
  }
}

export function batchReviewConfig(
  getEnv: (name: string) => string | undefined,
): BatchReviewConfig | null {
  return contentCatalogConfig(getEnv);
}

export async function listBatchReviewInbox(
  config: BatchReviewConfig,
  filters: {
    limit?: number;
    beforeFinishedAt?: string | null;
    beforeJobId?: string | null;
  } = {},
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<BatchReviewPage> {
  const limit = filters.limit ?? 24;
  const beforeFinishedAt = filters.beforeFinishedAt ?? null;
  const beforeJobId = filters.beforeJobId ?? null;
  if (
    !Number.isSafeInteger(limit)
    || limit < 1
    || limit > MAX_BATCH_REVIEW_LIMIT
    || ((beforeFinishedAt === null) !== (beforeJobId === null))
    || (beforeFinishedAt !== null && !validDate(beforeFinishedAt))
    || (beforeJobId !== null && !isCatalogUuid(beforeJobId))
  ) throw new BatchReviewError("invalid_batch_review_filters");

  const result = await rpcJson(
    config,
    "list_agent_batch_review_inbox",
    {
      target_workspace_id: config.workspaceId,
      target_limit: limit,
      target_before_finished_at: beforeFinishedAt,
      target_before_job_id: beforeJobId?.toLowerCase() ?? null,
    },
    fetcher,
    signal,
  );
  if (!isRecord(result) || !Array.isArray(result.items) || result.items.length > limit) {
    throw new BatchReviewError("batch_review_invalid_response");
  }
  const items = result.items.map(parseListItem);
  if (items.some(item => item === null)) {
    throw new BatchReviewError("batch_review_invalid_response");
  }

  let nextCursor: BatchReviewCursor | null = null;
  if (result.next_cursor !== null) {
    const cursor = result.next_cursor;
    if (
      !isRecord(cursor)
      || !validDate(cursor.finished_at)
      || !isCatalogUuid(cursor.job_id)
    ) throw new BatchReviewError("batch_review_invalid_response");
    nextCursor = {
      finished_at: cursor.finished_at,
      job_id: cursor.job_id.toLowerCase(),
    };
  }
  return { items: items as BatchReviewListItem[], next_cursor: nextCursor };
}

export async function getBatchReviewItem(
  config: BatchReviewConfig,
  jobId: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<BatchReviewDetail | null> {
  if (!isCatalogUuid(jobId)) {
    throw new BatchReviewError("invalid_batch_review_item_id");
  }
  const normalizedJobId = jobId.toLowerCase();
  const result = await rpcJson(
    config,
    "get_agent_batch_review_item",
    {
      target_workspace_id: config.workspaceId,
      target_job_id: normalizedJobId,
    },
    fetcher,
    signal,
  );
  if (result === null) return null;
  const listFields = parseListItem(result);
  const resultPayload = isRecord(result)
    ? exactBatchResultPayload(result.result_payload)
    : null;
  const sourceContent = isRecord(result)
    ? exactSourceContent(result.source_content)
    : null;
  if (
    !listFields
    || listFields.job_id !== normalizedJobId
    || !isRecord(result)
    || !SHA256_PATTERN.test(String(result.input_sha256 || ""))
    || !validBoundedInteger(result.actual_input_tokens, 1_050_000)
    || !validBoundedInteger(result.actual_output_tokens, 128_000)
    || !resultPayload
    || !sourceContent
  ) throw new BatchReviewError("batch_review_invalid_response");

  return {
    ...listFields,
    result_payload: resultPayload,
    source_content: sourceContent,
    source_evidence: {
      storage: "inline",
      content_length: sourceContent.length,
      content_sha256: createHash("sha256").update(sourceContent, "utf8").digest("hex"),
      verified_at: listFields.finished_at,
    },
    input_sha256: result.input_sha256 as string,
    actual_input_tokens: result.actual_input_tokens,
    actual_output_tokens: result.actual_output_tokens,
  };
}
