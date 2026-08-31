import { createHash, timingSafeEqual } from "node:crypto";

import type { ContentLibraryDetail, ScopedContentCatalogConfig } from "./content-catalog.mts";
import type { ContentReviewReadiness } from "./content-review-readiness.mts";
import {
  buildGrokQaReviewPackage,
  grokQaBannerImage,
  grokQaListItem,
  grokQaPassConflictsWithStoredBrandQa,
  grokQaSourceUrls,
  type GrokQaVerdict,
} from "./grok-qa.mts";
import { currentStudioReleaseSha } from "./studio-release.mts";

const MIN_TOKEN_BYTES = 32;
const MAX_TOKEN_BYTES = 512;
const SHA256 = /^[a-f0-9]{64}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const JWT = /^[A-Za-z0-9_-]{2,2048}\.[A-Za-z0-9_-]{2,8192}\.[A-Za-z0-9_-]{16,8192}$/;
const PROJECT_HOST = /^([a-z0-9-]{8,80})\.supabase\.co$/;
const SENSITIVE_ENV = [
  "STUDIO_ACCESS_TOKEN", "STUDIO_AUTOMATION_TOKEN", "API_SECRET",
  "PUBLICATION_WORKER_TOKEN", "SUPABASE_SERVICE_ROLE_KEY",
  "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_CONTENT_QA_KEY",
  "GROK_QA_CONNECTOR_TOKEN", "GROK_QA_DISPATCH_TOKEN", "GROK_QA_RELAY_TOKEN",
  "XAI_API_KEY", "X_BEARER_TOKEN", "TYPEFULLY_API_KEY",
  "TELEGRAM_REVIEW_BOT_TOKEN", "TELEGRAM_CONTENT_OPS_BOT_TOKEN",
  "TELEGRAM_CONTENT_OPS_RELAY_BOT_TOKEN", "TELEGRAM_BOT_TOKEN_SQUID",
  "TELEGRAM_BOT_TOKEN_YELLOW", "TELEGRAM_BOT_TOKEN_ORIGINTRAIL",
  "TELEGRAM_BOT_TOKEN_BABYLON",
] as const;

function jwtObject(value: string): Record<string, unknown> | null {
  if (!JWT.test(value)) return null;
  try {
    const [encodedHeader, encodedPayload] = value.split(".");
    const header = JSON.parse(Buffer.from(encodedHeader, "base64url").toString("utf8"));
    const payload = JSON.parse(Buffer.from(encodedPayload, "base64url").toString("utf8"));
    if (!isRecord(header) || !isRecord(payload)
      || typeof header.alg !== "string" || header.alg.toLowerCase() === "none") return null;
    return payload;
  } catch {
    return null;
  }
}

function supabaseProject(value: string): { url: string; ref: string } | null {
  try {
    const url = new URL(value);
    const match = url.hostname.toLowerCase().match(PROJECT_HOST);
    if (url.protocol !== "https:" || !match || url.username || url.password
      || (url.pathname !== "" && url.pathname !== "/") || url.search || url.hash) return null;
    return { url: url.origin, ref: match[1] };
  } catch {
    return null;
  }
}

function publishableProjectKey(value: string): boolean {
  if (/^sb_publishable_[A-Za-z0-9_-]{20,2048}$/.test(value)) return true;
  return jwtObject(value)?.role === "anon";
}

export function contentQaDatabaseConfig(
  getEnv: (name: string) => string | undefined,
  nowSeconds = Math.floor(Date.now() / 1_000),
  buildReleaseSha: string | null = currentStudioReleaseSha(),
): ScopedContentCatalogConfig | null {
  const project = supabaseProject((getEnv("SUPABASE_URL") || "").trim());
  const projectKey = (getEnv("SUPABASE_PUBLISHABLE_KEY") || "").trim();
  const authorizationKey = (getEnv("SUPABASE_CONTENT_QA_KEY") || "").trim();
  const workspaceId = (getEnv("CONTENT_STUDIO_WORKSPACE_ID") || "").trim().toLowerCase();
  const claims = jwtObject(authorizationKey);
  const issuedAt = Number(claims?.iat);
  const expiresAt = Number(claims?.exp);
  const releaseSha = (buildReleaseSha || "").trim().toLowerCase();
  if (!project || !UUID.test(workspaceId) || !publishableProjectKey(projectKey)
    || authorizationKey === projectKey
    || !claims || claims.iss !== "supabase" || claims.aud !== "authenticated"
    || claims.role !== "coineasy_content_qa" || claims.workspace_id !== workspaceId
    || claims.sub !== "codex:content-qa" || claims.capability !== "content_qa_review"
    || claims.environment !== "production" || claims.ref !== project.ref
    || !/^[a-f0-9]{40}$/.test(releaseSha) || claims.release_sha !== releaseSha
    || claims.automatic_publication !== false || claims.max_external_actions !== 0
    || !Number.isSafeInteger(issuedAt) || issuedAt <= 0 || issuedAt > nowSeconds + 60
    || !Number.isSafeInteger(expiresAt) || expiresAt <= nowSeconds + 60
    || expiresAt - issuedAt < 1 || expiresAt - issuedAt > 2_678_400) return null;
  return {
    supabaseUrl: project.url,
    projectKey,
    authorizationKey,
    workspaceId,
    rpcNames: {
      listLibrary: "list_content_qa_library",
      getLibraryItem: "get_content_qa_library_item",
      getReviewReadiness: "get_content_qa_readiness",
    },
  };
}

export type ContentQaVerdict = GrokQaVerdict;
export const buildContentQaPackage = buildGrokQaReviewPackage;
export const contentQaBannerImage = grokQaBannerImage;
export const contentQaListItem = grokQaListItem;
export const contentQaSourceUrls = grokQaSourceUrls;
export const contentQaPassConflictsWithStoredBrandQa = grokQaPassConflictsWithStoredBrandQa;

export function isEligibleContentQaReadiness(value: ContentReviewReadiness | null): value is ContentReviewReadiness & {
  generate_job_id: string; source_item_id: string; source_published_at: string; banner_sha256: string;
} {
  return Boolean(value
    && value.generate_job_id
    && value.source_item_id
    && value.source_published_at
    && value.source_is_latest
    && value.source_within_24h
    && value.feed_active
    && value.feed_poll_interval_minutes === 15
    && value.feed_poll_recent
    && value.banner_sha256 && /^[a-f0-9]{64}$/.test(value.banner_sha256)
    && value.approval_count === 0
    && value.publication_count === 0);
}

export function isNewContentQaCandidateReadiness(value: ContentReviewReadiness | null): value is ContentReviewReadiness & {
  generate_job_id: string; source_item_id: string; source_published_at: string; banner_sha256: string;
} {
  if (!isEligibleContentQaReadiness(value)) return false;
  return value.grok_outbox_count === 0 || (
    value.grok_outbox_count === 1
    && value.grok_status === "pending"
    && value.grok_decision === null
    && value.grok_next_action === null
    && value.grok_verdict_sha256 === null
  );
}

export function contentQaConnectorConfig(
  getEnv: (name: string) => string | undefined,
): { token: string } | null {
  const token = getEnv("CONTENT_QA_CONNECTOR_TOKEN") || "";
  const bytes = Buffer.byteLength(token, "utf8");
  if (
    token !== token.trim()
    || bytes < MIN_TOKEN_BYTES
    || bytes > MAX_TOKEN_BYTES
    || /[^\x21-\x7e]/.test(token)
  ) return null;
  const reused = SENSITIVE_ENV.some((name) => {
    const value = getEnv(name) || "";
    return Boolean(value) && value === token;
  });
  return reused ? null : { token };
}

export function hasContentQaConnectorAccess(req: Request, token: string): boolean {
  const header = req.headers.get("authorization") || "";
  if (!header.startsWith("Bearer ") || header.length > MAX_TOKEN_BYTES + 16) return false;
  const supplied = header.slice(7);
  if (!supplied || supplied !== supplied.trim()) return false;
  return timingSafeEqual(
    createHash("sha256").update(supplied).digest(),
    createHash("sha256").update(token).digest(),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function isStoredContentQaSourceSubset(submitted: string[], stored: string[]): boolean {
  const allowed = new Set(stored);
  return new Set(submitted).size === submitted.length
    && submitted.every((url) => allowed.has(url));
}

export type ContentQaReceipt = {
  recorded: boolean;
  status: string;
  job_id: string;
  decision: ContentQaVerdict["decision"];
  input_sha256: string;
  verdict_sha256: string;
  policy_version: "official-x-content-qa@1";
  reviewer_principal: "codex:content-qa";
  reviewer_model: "codex";
  reviewer_release_sha: string;
};

export type ContentQaJob = {
  workspace_id: string;
  job_id: string;
  content_item_id: string;
  content_version_id: string;
  source_item_id: string;
  banner_sha256: string;
  input_sha256: string;
  policy_version: "official-x-content-qa@1";
  decision: ContentQaVerdict["decision"];
  verdict_sha256: string;
  reviewer_principal: "codex:content-qa";
  reviewer_model: "codex";
  reviewer_release_sha: string;
  status: "reviewed";
  reviewed_at: string;
};

export type ContentQaExpectedProvenance = {
  generate_job_id: string;
  source_item_id: string;
  source_canonical_url: string;
  source_published_at: string;
  banner_sha256: string;
};

export function sameContentQaProvenance(
  left: ContentQaExpectedProvenance,
  right: ContentQaExpectedProvenance,
): boolean {
  return Object.keys(left).every((key) => (
    left[key as keyof ContentQaExpectedProvenance]
      === right[key as keyof ContentQaExpectedProvenance]
  ));
}

export async function getContentQaJob(
  config: ScopedContentCatalogConfig,
  contentItemId: string,
  contentVersionId: string,
  fetcher: typeof fetch = fetch,
): Promise<ContentQaJob | null> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/get_content_qa_job`, {
      method: "POST",
      headers: {
        apikey: config.projectKey,
        Authorization: `Bearer ${config.authorizationKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        target_workspace_id: config.workspaceId,
        target_content_item_id: contentItemId,
        target_content_version_id: contentVersionId,
        target_policy_version: "official-x-content-qa@1",
      }),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new Error("content_qa_job_unavailable");
  }
  if (!response.ok) throw new Error("content_qa_job_unavailable");
  let value: unknown;
  try { value = await response.json(); } catch { throw new Error("content_qa_job_invalid_response"); }
  if (value === null) return null;
  if (!isRecord(value)) throw new Error("content_qa_job_invalid_response");
  const expectedKeys = [
    "banner_sha256", "content_item_id", "content_version_id", "decision",
    "input_sha256", "job_id", "policy_version", "reviewed_at",
    "reviewer_model", "reviewer_principal", "reviewer_release_sha",
    "source_item_id", "status", "verdict_sha256", "workspace_id",
  ];
  const validDate = typeof value.reviewed_at === "string"
    && value.reviewed_at.length >= 20 && value.reviewed_at.length <= 40
    && Number.isFinite(Date.parse(value.reviewed_at));
  if (
    Object.keys(value).sort().join("\0") !== expectedKeys.sort().join("\0")
    || value.workspace_id !== config.workspaceId
    || value.content_item_id !== contentItemId
    || value.content_version_id !== contentVersionId
    || typeof value.job_id !== "string" || !UUID.test(value.job_id)
    || typeof value.source_item_id !== "string" || !UUID.test(value.source_item_id)
    || typeof value.banner_sha256 !== "string" || !SHA256.test(value.banner_sha256)
    || typeof value.input_sha256 !== "string" || !SHA256.test(value.input_sha256)
    || value.policy_version !== "official-x-content-qa@1"
    || !["PASS", "WARN", "BLOCK"].includes(String(value.decision))
    || typeof value.verdict_sha256 !== "string" || !SHA256.test(value.verdict_sha256)
    || value.reviewer_principal !== "codex:content-qa"
    || value.reviewer_model !== "codex"
    || typeof value.reviewer_release_sha !== "string" || !/^[a-f0-9]{40}$/.test(value.reviewer_release_sha)
    || value.status !== "reviewed"
    || !validDate
  ) throw new Error("content_qa_job_invalid_response");
  return value as ContentQaJob;
}

export async function recordContentQaVerdict(
  config: ScopedContentCatalogConfig,
  detail: ContentLibraryDetail,
  verdict: ContentQaVerdict,
  releaseSha: string,
  expected: ContentQaExpectedProvenance,
  fetcher: typeof fetch = fetch,
): Promise<ContentQaReceipt> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/record_content_qa_verdict`, {
      method: "POST",
      headers: {
        apikey: config.projectKey,
        Authorization: `Bearer ${config.authorizationKey}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        target_workspace_id: config.workspaceId,
        target_content_item_id: detail.content_item_id,
        target_content_version_id: detail.current_version_id,
        target_policy_version: "official-x-content-qa@1",
        target_reviewer_principal: "codex:content-qa",
        target_reviewer_model: "codex",
        target_reviewer_release_sha: releaseSha,
        target_expected_generate_job_id: expected.generate_job_id,
        target_expected_source_item_id: expected.source_item_id,
        target_expected_source_canonical_url: expected.source_canonical_url,
        target_expected_source_published_at: expected.source_published_at,
        target_expected_banner_sha256: expected.banner_sha256,
        target_verdict: verdict,
      }),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new Error("content_qa_record_unavailable");
  }
  if (!response.ok) throw new Error("content_qa_record_unavailable");
  let value: unknown;
  try { value = await response.json(); } catch { throw new Error("content_qa_record_invalid_response"); }
  if (!isRecord(value)) throw new Error("content_qa_record_invalid_response");
  const status = value.status;
  const jobId = value.job_id;
  const decision = value.decision;
  const inputSha = value.input_sha256;
  const verdictSha = value.verdict_sha256;
  const expectedKeys = [
    "decision", "input_sha256", "job_id", "policy_version", "recorded",
    "reviewer_model", "reviewer_principal", "reviewer_release_sha", "status",
    "verdict_sha256",
  ];
  if (
    Object.keys(value).sort().join("\0") !== expectedKeys.sort().join("\0")
    ||
    typeof value.recorded !== "boolean"
    || typeof status !== "string" || !["reviewed", "duplicate_conflict"].includes(status)
    || typeof jobId !== "string" || !UUID.test(jobId)
    || !["PASS", "WARN", "BLOCK"].includes(String(decision))
    || (status === "reviewed" && decision !== verdict.decision)
    || typeof inputSha !== "string" || !SHA256.test(inputSha)
    || typeof verdictSha !== "string" || !SHA256.test(verdictSha)
    || (status === "duplicate_conflict" && value.recorded !== false)
    || value.policy_version !== "official-x-content-qa@1"
    || value.reviewer_principal !== "codex:content-qa"
    || value.reviewer_model !== "codex"
    || value.reviewer_release_sha !== releaseSha
  ) throw new Error("content_qa_record_invalid_response");
  return {
    recorded: value.recorded,
    status,
    job_id: jobId,
    decision: decision as ContentQaReceipt["decision"],
    input_sha256: inputSha,
    verdict_sha256: verdictSha,
    policy_version: "official-x-content-qa@1",
    reviewer_principal: "codex:content-qa",
    reviewer_model: "codex",
    reviewer_release_sha: releaseSha,
  };
}
