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
const MAX_EVIDENCE_JSON_BYTES = 64 * 1024;
const MAX_EVIDENCE_NOTES = 8;
const MAX_EVIDENCE_REFERENCES = 6;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const AGENT_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const MEDIA_KEY_PATTERN = /^[0-9]+_[0-9]+$/;
const EVIDENCE_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?Z$/;
const X_STATUS_PATH_PATTERN = /^\/origin_trail\/status\/[0-9]{1,19}$/;
const FACT_CHECK_SCHEMA_VERSION = "1.0";
const FACT_CHECK_POLICY_VERSION = "origintrail-media-fact-evidence@1";
const X_SOURCE_HOSTS = new Set(["x.com"]);
const X_MEDIA_HOSTS = new Set(["pbs.twimg.com"]);
const REFERENCE_HOSTS_BY_KIND = {
  origintrail_implementation: new Set(["github.com"]),
  prime_intellect_announcement: new Set(["primeintellect.ai", "www.primeintellect.ai"]),
  prime_agent_release: new Set(["github.com"]),
  arc_community_leaderboard: new Set(["arcprize.org", "www.arcprize.org"]),
  arc_methodology: new Set(["arcprize.org", "www.arcprize.org"]),
  scorecard_source: new Set(["github.com"]),
} as const;
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

type BatchReviewFactCheckMedia = {
  type: "photo" | "video" | "animated_gif";
  media_key: string;
  recorded_url: string;
  preview_url: string;
  preview_url_sha256: string;
  width: number;
  height: number;
  factual_evidence: false;
};

type BatchReviewFactCheckReference = {
  kind: keyof typeof REFERENCE_HOSTS_BY_KIND;
  label_ko: string;
  url: string;
  observed_at: string;
  snapshot_sha256: string | null;
  availability: "available" | "unavailable";
  finding_ko: string;
};

type BatchReviewFactCheckPayload = {
  schema_version: typeof FACT_CHECK_SCHEMA_VERSION;
  policy_version: typeof FACT_CHECK_POLICY_VERSION;
  review_status: "qualified";
  human_review_required: true;
  verified_at: string;
  source_url: string;
  source_content_sha256: string;
  media: BatchReviewFactCheckMedia;
  review_notes_ko: string[];
  official_references: BatchReviewFactCheckReference[];
};

export type BatchReviewFactCheckEvidence = {
  payload: BatchReviewFactCheckPayload;
  evidence_sha256: string;
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
  fact_check_evidence?: BatchReviewFactCheckEvidence;
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

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.length
    && keys.every(key => expected.includes(key));
}

function boundedNonblankString(value: unknown, maximum: number): value is string {
  return typeof value === "string"
    && value.length >= 1
    && value.length <= maximum
    && value.trim() === value;
}

function validEvidenceDate(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = EVIDENCE_DATE_PATTERN.exec(value);
  if (!match) return false;
  const instant = new Date(value);
  return Number.isFinite(instant.getTime())
    && instant.getUTCFullYear() === Number(match[1])
    && instant.getUTCMonth() + 1 === Number(match[2])
    && instant.getUTCDate() === Number(match[3])
    && instant.getUTCHours() === Number(match[4])
    && instant.getUTCMinutes() === Number(match[5])
    && instant.getUTCSeconds() === Number(match[6]);
}

function exactHttpsUrl(
  value: unknown,
  allowedHosts: ReadonlySet<string>,
): string | null {
  if (
    typeof value !== "string"
    || value.length < 1
    || value.length > MAX_SOURCE_URL_LENGTH
    || value.trim() !== value
  ) return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:"
      || url.username
      || url.password
      || url.port
      || url.hash
      || !allowedHosts.has(url.hostname)
      || url.href !== value
    ) return null;
    return url.href;
  } catch {
    return null;
  }
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(item => canonicalJson(item)).join(",")}]`;
  }
  if (isRecord(value)) {
    return `{${Object.keys(value)
      .sort()
      .map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
      .join(",")}}`;
  }
  const serialized = JSON.stringify(value);
  if (serialized === undefined) throw new TypeError("invalid canonical JSON value");
  return serialized;
}

function sha256(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function validReferencePath(
  kind: keyof typeof REFERENCE_HOSTS_BY_KIND,
  url: URL,
): boolean {
  if (url.search) return false;
  switch (kind) {
    case "origintrail_implementation":
      return /^\/OriginTrail\/dkg\/blob\/[a-f0-9]{40}\/packages\/adapter-prime-agent\/README\.md$/.test(
        url.pathname,
      );
    case "prime_intellect_announcement":
      return url.pathname === "/blog/prime-agent";
    case "prime_agent_release":
      return /^\/PrimeIntellect-ai\/prime-agent\/(?:releases\/tag\/v[0-9.]+|commit\/[a-f0-9]{40})$/.test(
        url.pathname,
      );
    case "arc_community_leaderboard":
      return url.pathname === "/api/leaderboards";
    case "arc_methodology":
      return url.pathname === "/media/ARC_AGI_3_Technical_Report.pdf";
    case "scorecard_source":
      return /^\/PrimeIntellect-ai\/arc-agi-3-prime-agent-scorecard\/commit\/[a-f0-9]{40}$/.test(
        url.pathname,
      );
  }
}

function exactFactCheckReference(value: unknown): BatchReviewFactCheckReference | null {
  if (!isRecord(value) || !hasExactKeys(value, [
    "kind",
    "label_ko",
    "url",
    "observed_at",
    "snapshot_sha256",
    "availability",
    "finding_ko",
  ])) return null;

  const kind = value.kind;
  if (
    typeof kind !== "string"
    || !Object.hasOwn(REFERENCE_HOSTS_BY_KIND, kind)
  ) return null;
  const typedKind = kind as keyof typeof REFERENCE_HOSTS_BY_KIND;
  const url = exactHttpsUrl(value.url, REFERENCE_HOSTS_BY_KIND[typedKind]);
  if (
    !boundedNonblankString(value.label_ko, 160)
    || !url
    || !validEvidenceDate(value.observed_at)
    || !(
      value.snapshot_sha256 === null
      || (typeof value.snapshot_sha256 === "string"
        && SHA256_PATTERN.test(value.snapshot_sha256))
    )
    || !(value.availability === "available" || value.availability === "unavailable")
    || !boundedNonblankString(value.finding_ko, 600)
    || !validReferencePath(typedKind, new URL(url))
  ) return null;

  return {
    kind: typedKind,
    label_ko: value.label_ko,
    url,
    observed_at: value.observed_at,
    snapshot_sha256: value.snapshot_sha256,
    availability: value.availability,
    finding_ko: value.finding_ko,
  };
}

function exactFactCheckMedia(value: unknown): BatchReviewFactCheckMedia | null {
  if (!isRecord(value) || !hasExactKeys(value, [
    "type",
    "media_key",
    "recorded_url",
    "preview_url",
    "preview_url_sha256",
    "width",
    "height",
    "factual_evidence",
  ])) return null;

  const recordedUrl = exactHttpsUrl(value.recorded_url, X_MEDIA_HOSTS);
  const previewUrl = exactHttpsUrl(value.preview_url, X_MEDIA_HOSTS);
  if (!recordedUrl || !previewUrl) return null;
  const recordedMediaUrl = new URL(recordedUrl);
  if (
    recordedMediaUrl.search
    || !/^\/(?:media|amplify_video_thumb|ext_tw_video_thumb|tweet_video_thumb)\//.test(
      recordedMediaUrl.pathname,
    )
  ) return null;
  const expectedPreviewUrl = new URL(recordedUrl);
  expectedPreviewUrl.searchParams.set("name", "orig");
  if (
    !(value.type === "photo" || value.type === "video" || value.type === "animated_gif")
    || typeof value.media_key !== "string"
    || value.media_key.length > 128
    || !MEDIA_KEY_PATTERN.test(value.media_key)
    || previewUrl !== expectedPreviewUrl.href
    || typeof value.preview_url_sha256 !== "string"
    || !SHA256_PATTERN.test(value.preview_url_sha256)
    || value.preview_url_sha256 !== sha256(previewUrl)
    || !validBoundedInteger(value.width, 8_192)
    || value.width < 1
    || !validBoundedInteger(value.height, 8_192)
    || value.height < 1
    || value.factual_evidence !== false
  ) return null;

  return {
    type: value.type,
    media_key: value.media_key,
    recorded_url: recordedUrl,
    preview_url: previewUrl,
    preview_url_sha256: value.preview_url_sha256,
    width: value.width,
    height: value.height,
    factual_evidence: false,
  };
}

function exactFactCheckEvidence(
  value: unknown,
  expectedSourceUrl: string,
  sourceContent: string,
): BatchReviewFactCheckEvidence | null {
  let byteLength = Number.POSITIVE_INFINITY;
  try {
    byteLength = Buffer.byteLength(JSON.stringify(value), "utf8");
  } catch {
    return null;
  }
  if (
    byteLength > MAX_EVIDENCE_JSON_BYTES
    || !isRecord(value)
    || !hasExactKeys(value, ["payload", "evidence_sha256"])
    || typeof value.evidence_sha256 !== "string"
    || !SHA256_PATTERN.test(value.evidence_sha256)
    || !isRecord(value.payload)
  ) return null;

  const payload = value.payload;
  if (!hasExactKeys(payload, [
    "schema_version",
    "policy_version",
    "review_status",
    "human_review_required",
    "verified_at",
    "source_url",
    "source_content_sha256",
    "media",
    "review_notes_ko",
    "official_references",
  ])) return null;

  const sourceUrl = exactHttpsUrl(payload.source_url, X_SOURCE_HOSTS);
  const media = exactFactCheckMedia(payload.media);
  const notes = payload.review_notes_ko;
  const references = payload.official_references;
  if (
    payload.schema_version !== FACT_CHECK_SCHEMA_VERSION
    || payload.policy_version !== FACT_CHECK_POLICY_VERSION
    || payload.review_status !== "qualified"
    || payload.human_review_required !== true
    || !validEvidenceDate(payload.verified_at)
    || !sourceUrl
    || new URL(sourceUrl).search.length > 0
    || !X_STATUS_PATH_PATTERN.test(new URL(sourceUrl).pathname)
    || sourceUrl !== expectedSourceUrl
    || typeof payload.source_content_sha256 !== "string"
    || !SHA256_PATTERN.test(payload.source_content_sha256)
    || payload.source_content_sha256 !== sha256(sourceContent)
    || !media
    || !Array.isArray(notes)
    || notes.length < 1
    || notes.length > MAX_EVIDENCE_NOTES
    || notes.some(note => !boundedNonblankString(note, 600))
    || !Array.isArray(references)
    || references.length !== MAX_EVIDENCE_REFERENCES
  ) return null;

  const parsedReferences = references.map(exactFactCheckReference);
  if (
    parsedReferences.some(reference => reference === null)
    || new Set(parsedReferences.map(reference => reference?.kind)).size
      !== parsedReferences.length
  ) return null;

  const parsedPayload: BatchReviewFactCheckPayload = {
    schema_version: FACT_CHECK_SCHEMA_VERSION,
    policy_version: FACT_CHECK_POLICY_VERSION,
    review_status: "qualified",
    human_review_required: true,
    verified_at: payload.verified_at,
    source_url: sourceUrl,
    source_content_sha256: payload.source_content_sha256,
    media,
    review_notes_ko: [...notes] as string[],
    official_references: parsedReferences as BatchReviewFactCheckReference[],
  };
  if (value.evidence_sha256 !== sha256(canonicalJson(parsedPayload))) return null;
  return {
    payload: parsedPayload,
    evidence_sha256: value.evidence_sha256,
  };
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
  const sourceUrl = listFields?.source_url ?? null;
  const rawFactCheckEvidence = isRecord(result)
    ? result.fact_check_evidence
    : undefined;
  const factCheckEvidence = (
    rawFactCheckEvidence === undefined
    || rawFactCheckEvidence === null
    || !sourceContent
    || !sourceUrl
  )
    ? null
    : exactFactCheckEvidence(rawFactCheckEvidence, sourceUrl, sourceContent);
  if (
    !listFields
    || listFields.job_id !== normalizedJobId
    || !isRecord(result)
    || !SHA256_PATTERN.test(String(result.input_sha256 || ""))
    || !validBoundedInteger(result.actual_input_tokens, 1_050_000)
    || !validBoundedInteger(result.actual_output_tokens, 128_000)
    || !resultPayload
    || !sourceContent
    || (
      rawFactCheckEvidence !== undefined
      && rawFactCheckEvidence !== null
      && !factCheckEvidence
    )
  ) throw new BatchReviewError("batch_review_invalid_response");

  const detail: BatchReviewDetail = {
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
  if (factCheckEvidence) detail.fact_check_evidence = factCheckEvidence;
  return detail;
}
