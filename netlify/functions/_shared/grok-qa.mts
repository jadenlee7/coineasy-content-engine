import { createHash, timingSafeEqual } from "node:crypto";

import type {
  ContentCatalogConfig,
  ContentLibraryDetail,
  ContentLibraryListItem,
} from "./content-catalog.mts";

const CONNECTOR_TOKEN_MIN_BYTES = 32;
const CONNECTOR_TOKEN_MAX_BYTES = 512;
const QA_RELAY_TIMEOUT_MS = 18_000;
const MAX_BANNER_BYTES = 3_000_000;
const URL_LIMIT = 8;

export const GROK_QA_DECISIONS = ["PASS", "WARN", "BLOCK"] as const;
export const GROK_QA_NEXT_ACTIONS = [
  "ready_for_human_approval",
  "human_review",
  "verify_source",
  "revise_copy",
  "revise_banner",
] as const;

export type GrokQaDecision = typeof GROK_QA_DECISIONS[number];
export type GrokQaNextAction = typeof GROK_QA_NEXT_ACTIONS[number];

export type GrokQaVerdict = {
  decision: GrokQaDecision;
  summary: string;
  fact_check: {
    status: GrokQaDecision;
    checks: string[];
    source_urls: string[];
  };
  brand_check: {
    status: GrokQaDecision;
    checks: string[];
  };
  issues: Array<{
    severity: "WARN" | "BLOCK";
    code: string;
    message: string;
    evidence_url?: string;
  }>;
  next_action: GrokQaNextAction;
};

export type GrokQaConnectorConfig = {
  token: string;
};

export type GrokQaReviewPackage = {
  content_item_id: string;
  content_version_id: string;
  client_id: ContentLibraryDetail["client_id"];
  content_kind: ContentLibraryDetail["content_kind"];
  title: string;
  status: "needs_review";
  version_number: number;
  locale: string;
  generated_content: Record<string, unknown>;
  channel_copy: Record<string, string>;
  automated_qa: {
    content_qa: Record<string, unknown>;
    brand_qa: Record<string, unknown> | null;
    fact_check: Record<string, unknown> | null;
  };
  source_urls: string[];
  banner: {
    available: boolean;
    mime_type: string | null;
    width: number | null;
    height: number | null;
    sha256: string | null;
  };
  review_rules: string[];
};

export type GrokQaVerdictReceipt = {
  claimed: boolean;
  status: "claimed" | "sent" | "failed" | "duplicate_conflict";
  payload_sha256: string | null;
  decision: GrokQaDecision | null;
};

export type GrokQaRelayConfig = {
  railwayUrl: string;
  apiSecret: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function safeText(value: unknown, maximum: number): string {
  return typeof value === "string"
    ? value.replace(/\r\n?/g, "\n").trim().slice(0, maximum)
    : "";
}

function safeTextList(value: unknown, maximumItems: number, maximumText: number): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    .slice(0, maximumItems)
    .map((item) => safeText(item, maximumText));
}

function safeUrl(value: unknown): string | null {
  if (typeof value !== "string" || value.length > 2_048) return null;
  try {
    const url = new URL(value);
    if (
      url.protocol !== "https:"
      || !url.hostname
      || url.username
      || url.password
      || url.hash
    ) return null;
    return url.toString();
  } catch {
    return null;
  }
}

function safeJson(value: unknown, depth = 0): unknown {
  if (depth > 8) return null;
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") return safeText(value, 20_000);
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (Array.isArray(value)) return value.slice(0, 100).map((item) => safeJson(item, depth + 1));
  if (!isRecord(value)) return null;
  return Object.fromEntries(
    Object.entries(value)
      .slice(0, 100)
      .map(([key, item]) => [key.slice(0, 120), safeJson(item, depth + 1)]),
  );
}

function safeGeneratedJson(value: unknown, depth = 0): unknown {
  if (depth > 8) return null;
  if (Array.isArray(value)) {
    return value.slice(0, 100).map((item) => safeGeneratedJson(item, depth + 1));
  }
  if (!isRecord(value)) return safeJson(value, depth);
  const blockedKey = /(?:^|_)(?:url|urls|storage_path|request_hash|submitted_content|resolved_content|token|secret|cookie)$/i;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([key]) => !blockedKey.test(key))
      .slice(0, 100)
      .map(([key, item]) => [key.slice(0, 120), safeGeneratedJson(item, depth + 1)]),
  );
}

function selectedGeneratedContent(detail: ContentLibraryDetail): Record<string, unknown> {
  const content = detail.current_version.content;
  if (detail.content_kind === "daily_news") {
    return {
      spec: safeGeneratedJson(content.spec),
      render: safeGeneratedJson(content.render),
    };
  }
  if (detail.content_kind === "article") {
    return {
      lead: safeGeneratedJson(content.lead),
      sections: safeGeneratedJson(content.sections),
      key_takeaways: safeGeneratedJson(content.key_takeaways),
      visuals: safeGeneratedJson(content.visuals),
      markdown: safeGeneratedJson(content.markdown),
    };
  }
  return {
    series: safeGeneratedJson(content.series),
    lessons: safeGeneratedJson(content.lessons),
    lesson_count: safeGeneratedJson(content.lesson_count),
  };
}

export function grokQaSourceUrls(detail: ContentLibraryDetail): string[] {
  const content = detail.current_version.content;
  const source = isRecord(content.source) ? content.source : {};
  const spec = isRecord(content.spec) ? content.spec : {};
  const sourceMap = Array.isArray(content.source_map) ? content.source_map : [];
  const candidates: unknown[] = [source.url, spec.source_url];
  for (const item of sourceMap) {
    if (isRecord(item)) candidates.push(item.source_url);
  }
  const urls: string[] = [];
  for (const candidate of candidates) {
    const url = safeUrl(candidate);
    if (url && !urls.includes(url)) urls.push(url);
    if (urls.length >= URL_LIMIT) break;
  }
  return urls;
}

function selectedChannelCopy(detail: ContentLibraryDetail): Record<string, string> {
  const copy: Record<string, string> = {};
  for (const channel of ["telegram", "x"] as const) {
    const value = safeText(detail.current_version.channel_copy[channel], 4_000);
    if (value) copy[channel] = value;
  }
  return copy;
}

export function buildGrokQaReviewPackage(detail: ContentLibraryDetail): GrokQaReviewPackage {
  if (detail.status !== "needs_review") throw new Error("qa_status_conflict");
  if (detail.current_version.generation_meta.mock_mode === true) {
    throw new Error("qa_mock_content_disabled");
  }
  const brandQa = detail.current_version.generation_meta.brand_qa;
  const factCheck = detail.current_version.generation_meta.fact_check;
  const banner = detail.assets.find((asset) => (
    asset.asset_kind === "png"
    && asset.mime_type === "image/png"
    && safeUrl(asset.url) !== null
  ));
  return {
    content_item_id: detail.content_item_id,
    content_version_id: detail.current_version_id,
    client_id: detail.client_id,
    content_kind: detail.content_kind,
    title: safeText(detail.title, 200),
    status: "needs_review",
    version_number: detail.current_version.version_number,
    locale: safeText(detail.current_version.locale, 32),
    generated_content: selectedGeneratedContent(detail),
    channel_copy: selectedChannelCopy(detail),
    automated_qa: {
      content_qa: safeJson(detail.current_version.qa) as Record<string, unknown>,
      brand_qa: isRecord(brandQa) ? safeJson(brandQa) as Record<string, unknown> : null,
      fact_check: isRecord(factCheck) ? safeJson(factCheck) as Record<string, unknown> : null,
    },
    source_urls: grokQaSourceUrls(detail),
    banner: {
      available: Boolean(banner),
      mime_type: banner?.mime_type || null,
      width: banner?.width || null,
      height: banner?.height || null,
      sha256: banner?.sha256 || null,
    },
    review_rules: [
      "공식 source URL을 직접 확인하고 사실과 추론을 분리한다.",
      "한국 GTM 문구의 이해도와 해당 client의 공식 브랜드 톤을 별도로 확인한다.",
      "판정은 자문용이며 Studio 승인 또는 외부 발행을 수행하지 않는다.",
    ],
  };
}

export function grokQaListItem(item: ContentLibraryListItem): Record<string, unknown> {
  return {
    content_item_id: item.content_item_id,
    content_version_id: item.content_version_id,
    client_id: item.client_id,
    content_kind: item.content_kind,
    title: safeText(item.title, 200),
    status: item.status,
    created_at: item.created_at,
    updated_at: item.updated_at,
    banner_available: Boolean(item.primary_asset_id),
  };
}

export function grokQaConnectorConfig(
  getEnv: (name: string) => string | undefined,
): GrokQaConnectorConfig | null {
  const token = (getEnv("GROK_QA_CONNECTOR_TOKEN") || "").trim();
  const bytes = Buffer.byteLength(token, "utf8");
  const forbidden = [
    "STUDIO_ACCESS_TOKEN",
    "STUDIO_AUTOMATION_TOKEN",
    "API_SECRET",
    "PUBLICATION_WORKER_TOKEN",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_GROK_QA_OAUTH_KEY",
    "GROK_QA_OAUTH_OPERATOR_SECRET",
    "GROK_QA_OAUTH_SIGNING_SECRET",
  ].some((name) => {
    const existing = (getEnv(name) || "").trim();
    return Boolean(existing) && existing === token;
  });
  return bytes >= CONNECTOR_TOKEN_MIN_BYTES
    && bytes <= CONNECTOR_TOKEN_MAX_BYTES
    && !forbidden
    ? { token }
    : null;
}

export function hasGrokQaConnectorAccess(req: Request, token: string): boolean {
  const header = req.headers.get("authorization") || "";
  if (!header.startsWith("Bearer ") || header.length > CONNECTOR_TOKEN_MAX_BYTES + 16) {
    return false;
  }
  const supplied = header.slice(7);
  if (!supplied || supplied !== supplied.trim()) return false;
  const actual = createHash("sha256").update(supplied, "utf8").digest();
  const expected = createHash("sha256").update(token, "utf8").digest();
  return timingSafeEqual(actual, expected);
}

function supabaseHeaders(config: ContentCatalogConfig): Record<string, string> {
  return {
    apikey: config.serviceRoleKey,
    Authorization: `Bearer ${config.serviceRoleKey}`,
    "Content-Type": "application/json",
  };
}

async function callReceiptRpc(
  config: ContentCatalogConfig,
  rpc: string,
  body: Record<string, unknown>,
  fetcher: typeof fetch,
): Promise<Record<string, unknown>> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/${rpc}`, {
      method: "POST",
      headers: supabaseHeaders(config),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new Error("qa_receipt_unavailable");
  }
  if (!response.ok) throw new Error("qa_receipt_unavailable");
  let result: unknown;
  try {
    result = await response.json();
  } catch {
    throw new Error("qa_receipt_invalid_response");
  }
  if (!isRecord(result)) throw new Error("qa_receipt_invalid_response");
  return result;
}

export async function claimGrokQaVerdict(
  config: ContentCatalogConfig,
  contentItemId: string,
  contentVersionId: string,
  verdict: GrokQaVerdict,
  fetcher: typeof fetch = fetch,
): Promise<GrokQaVerdictReceipt> {
  const result = await callReceiptRpc(config, "claim_grok_qa_verdict", {
    target_workspace_id: config.workspaceId,
    target_content_item_id: contentItemId,
    target_content_version_id: contentVersionId,
    target_payload: verdict,
  }, fetcher);
  const status = result.status;
  const decision = result.decision;
  const payloadSha256 = result.payload_sha256;
  if (
    typeof result.claimed !== "boolean"
    || !["claimed", "sent", "failed", "duplicate_conflict"].includes(String(status))
    || !(decision === null || GROK_QA_DECISIONS.includes(decision as GrokQaDecision))
    || !(payloadSha256 === null || (
      typeof payloadSha256 === "string" && /^[a-f0-9]{64}$/.test(payloadSha256)
    ))
  ) throw new Error("qa_receipt_invalid_response");
  return {
    claimed: result.claimed,
    status: status as GrokQaVerdictReceipt["status"],
    payload_sha256: payloadSha256 as string | null,
    decision: decision as GrokQaDecision | null,
  };
}

export async function finalizeGrokQaVerdict(
  config: ContentCatalogConfig,
  contentVersionId: string,
  payloadSha256: string,
  outcome: "sent" | "failed",
  failureCode: string | null,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const result = await callReceiptRpc(config, "finalize_grok_qa_verdict", {
    target_workspace_id: config.workspaceId,
    target_content_version_id: contentVersionId,
    target_payload_sha256: payloadSha256,
    target_outcome: outcome,
    target_failure_code: failureCode,
  }, fetcher);
  if (result.status !== outcome) throw new Error("qa_receipt_finalize_failed");
}

export function grokQaRelayConfig(
  getEnv: (name: string) => string | undefined,
): GrokQaRelayConfig | null {
  const railwayUrl = (getEnv("RAILWAY_API_URL") || "").trim().replace(/\/+$/, "");
  const apiSecret = (getEnv("API_SECRET") || "").trim();
  try {
    const url = new URL(railwayUrl);
    if (
      url.protocol !== "https:"
      || url.username
      || url.password
      || (url.pathname !== "/" && url.pathname !== "")
      || url.search
      || url.hash
      || apiSecret.length < 16
    ) return null;
  } catch {
    return null;
  }
  return { railwayUrl, apiSecret };
}

export async function sendGrokQaVerdict(
  config: GrokQaRelayConfig,
  detail: ContentLibraryDetail,
  verdict: GrokQaVerdict,
  reviewUrl: string,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  try {
    const response = await fetcher(`${config.railwayUrl}/internal/grok-qa-verdict`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": config.apiSecret,
      },
      body: JSON.stringify({
        content_item_id: detail.content_item_id,
        content_version_id: detail.current_version_id,
        client_id: detail.client_id,
        content_kind: detail.content_kind,
        title: detail.title,
        ...verdict,
        review_url: reviewUrl,
      }),
      signal: AbortSignal.timeout(QA_RELAY_TIMEOUT_MS),
    });
    if (!response.ok) return false;
    const result = await response.json() as Record<string, unknown>;
    return result.sent === true;
  } catch {
    return false;
  }
}

export async function grokQaBannerImage(
  detail: ContentLibraryDetail,
  fetcher: typeof fetch = fetch,
): Promise<{ data: string; mimeType: "image/png" } | null> {
  const asset = detail.assets.find((candidate) => (
    candidate.asset_kind === "png"
    && candidate.mime_type === "image/png"
    && candidate.byte_size !== null
    && candidate.byte_size > 8
    && candidate.byte_size <= MAX_BANNER_BYTES
    && typeof candidate.sha256 === "string"
    && /^[a-f0-9]{64}$/.test(candidate.sha256)
    && safeUrl(candidate.url) !== null
  ));
  if (!asset) return null;
  let response: Response;
  try {
    response = await fetcher(asset.url, { signal: AbortSignal.timeout(10_000) });
  } catch {
    return null;
  }
  if (!response.ok) return null;
  if ((response.headers.get("content-type") || "").split(";", 1)[0].trim() !== "image/png") {
    return null;
  }
  const declared = Number(response.headers.get("content-length") || 0);
  if (declared > MAX_BANNER_BYTES) return null;
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (
    bytes.byteLength > MAX_BANNER_BYTES
    || bytes.byteLength !== asset.byte_size
    || createHash("sha256").update(bytes).digest("hex") !== asset.sha256
    || ![0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
      .every((byte, index) => bytes[index] === byte)
  ) return null;
  return { data: Buffer.from(bytes).toString("base64"), mimeType: "image/png" };
}
