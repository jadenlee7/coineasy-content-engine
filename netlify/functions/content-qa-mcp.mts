import type { Config, Context } from "@netlify/functions";
import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import { z } from "zod";

import {
  ContentCatalogError, getContentLibraryItem,
  isCatalogUuid, listContentLibrary,
} from "./_shared/content-catalog.mts";
import {
  buildContentQaPackage, contentQaBannerImage, contentQaConnectorConfig,
  contentQaDatabaseConfig,
  contentQaListItem, contentQaPassConflictsWithStoredBrandQa, contentQaSourceUrls,
  hasContentQaConnectorAccess, isStoredContentQaSourceSubset, recordContentQaVerdict,
  isEligibleContentQaReadiness, sameContentQaProvenance,
  getContentQaJob, isNewContentQaCandidateReadiness,
  type ContentQaExpectedProvenance, type ContentQaVerdict,
} from "./_shared/content-qa.mts";
import { currentStudioReleaseSha } from "./_shared/studio-release.mts";
import { getContentReviewReadiness, type ContentReviewReadiness } from "./_shared/content-review-readiness.mts";

const HOST = "coineasy-newscard.netlify.app";
const MAX_REQUEST_BYTES = 128 * 1024;
const supportedClient = z.enum(["yellow", "squid", "babylon"]);
const uuid = z.string().uuid();
const decision = z.enum(["PASS", "WARN", "BLOCK"]);
const safeUrl = z.string().url().max(2048).refine((value) => {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.hash;
  } catch {
    return false;
  }
});
const verdict = z.object({
  decision,
  summary: z.string().trim().min(10).max(800),
  fact_check: z.object({ status: decision, checks: z.array(z.string().trim().min(3).max(300)).min(1).max(6), source_urls: z.array(safeUrl).max(8) }).strict(),
  brand_check: z.object({ status: decision, checks: z.array(z.string().trim().min(3).max(300)).min(1).max(6) }).strict(),
  issues: z.array(z.object({ severity: z.enum(["WARN", "BLOCK"]), code: z.string().regex(/^[a-z][a-z0-9_]{2,47}$/), message: z.string().trim().min(3).max(500), evidence_url: safeUrl.optional() }).strict()).max(3),
  next_action: z.enum(["ready_for_human_approval", "human_review", "verify_source", "revise_copy", "revise_banner"]),
}).strict().superRefine((value, ctx) => {
  if (value.decision === "PASS" && (value.fact_check.status !== "PASS" || value.brand_check.status !== "PASS" || value.issues.length || value.next_action !== "ready_for_human_approval")) ctx.addIssue({ code: "custom", message: "invalid PASS verdict" });
  if (value.decision !== "PASS" && value.next_action === "ready_for_human_approval") ctx.addIssue({ code: "custom", message: "invalid next action" });
  if (value.decision === "BLOCK" && value.fact_check.status !== "BLOCK" && value.brand_check.status !== "BLOCK" && !value.issues.some((item) => item.severity === "BLOCK")) ctx.addIssue({ code: "custom", message: "BLOCK needs blocking evidence" });
});

const jsonContent = (value: unknown) => [{ type: "text" as const, text: JSON.stringify(value) }];
const toolError = (error: string) => ({ isError: true, content: jsonContent({ error }), structuredContent: { error } });
function catalog() {
  const value = contentQaDatabaseConfig((name) => Netlify.env.get(name));
  if (!value) throw new Error("content_catalog_not_configured");
  return value;
}
function errorCode(error: unknown, fallback: string): string {
  return error instanceof ContentCatalogError ? error.code : error instanceof Error ? error.message : fallback;
}
function expectedProvenance(
  readiness: ContentReviewReadiness & {
    generate_job_id: string; source_item_id: string; source_published_at: string; banner_sha256: string;
  },
  sourceCanonicalUrl: string,
): ContentQaExpectedProvenance {
  return {
    generate_job_id: readiness.generate_job_id,
    source_item_id: readiness.source_item_id,
    source_canonical_url: sourceCanonicalUrl,
    source_published_at: readiness.source_published_at,
    banner_sha256: readiness.banner_sha256,
  };
}

function server(): McpServer {
  const mcp = new McpServer({ name: "coineasy-content-qa", version: "1.0.0" });
  mcp.registerTool("coineasy_list_content_qa_candidates", {
    title: "List content QA candidates", description: "List up to five non-mock needs_review candidates. Read-only.",
    inputSchema: z.object({ client_id: supportedClient.optional(), content_kind: z.literal("daily_news").optional(), limit: z.number().int().min(1).max(5).default(5) }).strict(),
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  }, async ({ client_id, content_kind, limit }) => {
    try {
      const page = await listContentLibrary(catalog(), { clientId: client_id || null, contentKind: content_kind || "daily_news", status: "needs_review", limit });
      const items = [];
      for (const item of page.items.filter((candidate) => ["yellow", "squid", "babylon"].includes(candidate.client_id) && candidate.status === "needs_review" && !candidate.mock_mode)) {
        const readiness = await getContentReviewReadiness(catalog(), item.content_item_id, item.content_version_id);
        if (!isNewContentQaCandidateReadiness(readiness)) continue;
        if (await getContentQaJob(catalog(), item.content_item_id, item.content_version_id)) continue;
        items.push({
          ...contentQaListItem(item), generate_job_id: readiness.generate_job_id,
          source_item_id: readiness.source_item_id, source_published_at: readiness.source_published_at,
          banner_sha256: readiness.banner_sha256, approval_count: 0, publication_count: 0,
        });
      }
      const output = { items, count: items.length, next_cursor: null };
      return { content: jsonContent(output), structuredContent: output };
    } catch (error) { return toolError(errorCode(error, "content_qa_list_unavailable")); }
  });
  mcp.registerTool("coineasy_get_content_qa_package", {
    title: "Get exact content QA package", description: "Get one sanitized exact-version package and hash-verified PNG. Read-only.",
    inputSchema: z.object({ content_item_id: uuid, content_version_id: uuid }).strict(),
    annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  }, async ({ content_item_id, content_version_id }) => {
    try {
      const detail = await getContentLibraryItem(catalog(), content_item_id.toLowerCase(), fetch, 60, AbortSignal.timeout(10_000));
      if (!detail) return toolError("content_qa_item_not_found");
      if (detail.current_version_id !== content_version_id.toLowerCase()) return toolError("content_qa_version_conflict");
      if (!["yellow", "squid", "babylon"].includes(detail.client_id)) return toolError("content_qa_client_not_supported");
      const readiness = await getContentReviewReadiness(catalog(), detail.content_item_id, detail.current_version_id);
      if (!isEligibleContentQaReadiness(readiness)) return toolError("content_qa_not_eligible");
      if (await getContentQaJob(catalog(), detail.content_item_id, detail.current_version_id)) {
        return toolError("content_qa_already_reviewed");
      }
      if (!isNewContentQaCandidateReadiness(readiness)) return toolError("content_qa_not_eligible");
      const output = buildContentQaPackage(detail);
      const sources = contentQaSourceUrls(detail);
      if (!sources[0]) return toolError("content_qa_not_eligible");
      const provenance = expectedProvenance(readiness, sources[0]);
      const image = await contentQaBannerImage(detail, fetch, readiness.banner_sha256);
      if (!image) return toolError("content_qa_banner_unavailable");
      const packaged = { ...output, provenance };
      return { content: [...jsonContent(packaged), { type: "image" as const, data: image.data, mimeType: image.mimeType }], structuredContent: packaged };
    } catch (error) { return toolError(errorCode(error, "content_qa_package_unavailable")); }
  });
  mcp.registerTool("coineasy_record_content_qa_verdict", {
    title: "Record content QA verdict", description: "Record an advisory exact-version verdict in the database only. Never relays or publishes.",
    inputSchema: z.object({
      content_item_id: uuid, content_version_id: uuid,
      expected_provenance: z.object({
        generate_job_id: uuid, source_item_id: uuid,
        source_canonical_url: safeUrl,
        source_published_at: z.string().datetime({ offset: true }),
        banner_sha256: z.string().regex(/^[a-f0-9]{64}$/),
      }).strict(),
      verdict,
    }).strict(),
    annotations: { readOnlyHint: false, destructiveHint: false, idempotentHint: true, openWorldHint: false },
  }, async ({ content_item_id, content_version_id, expected_provenance, verdict: submitted }) => {
    const itemId = content_item_id.toLowerCase(); const versionId = content_version_id.toLowerCase();
    if (!isCatalogUuid(itemId) || !isCatalogUuid(versionId)) return toolError("invalid_content_qa_identity");
    try {
      const release = currentStudioReleaseSha();
      if (!release) return toolError("content_qa_release_unstamped");
      const cfg = catalog();
      const detail = await getContentLibraryItem(cfg, itemId, fetch, 60, AbortSignal.timeout(10_000));
      if (!detail) return toolError("content_qa_item_not_found");
      if (detail.current_version_id !== versionId) return toolError("content_qa_version_conflict");
      if (!["yellow", "squid", "babylon"].includes(detail.client_id)) return toolError("content_qa_client_not_supported");
      if (detail.status !== "needs_review") return toolError("content_qa_status_conflict");
      if (detail.current_version.generation_meta.mock_mode === true) return toolError("content_qa_mock_disabled");
      const readiness = await getContentReviewReadiness(cfg, itemId, versionId);
      if (!isEligibleContentQaReadiness(readiness)) return toolError("content_qa_not_eligible");
      const existingJob = await getContentQaJob(cfg, itemId, versionId);
      if (!existingJob && !isNewContentQaCandidateReadiness(readiness)) {
        return toolError("content_qa_not_eligible");
      }
      if (contentQaPassConflictsWithStoredBrandQa(detail, submitted)) return toolError("content_qa_brand_conflict");
      const sources = contentQaSourceUrls(detail);
      if (!sources[0]) return toolError("content_qa_not_eligible");
      const currentProvenance = expectedProvenance(readiness, sources[0]);
      if (!sameContentQaProvenance(expected_provenance, currentProvenance)) return toolError("content_qa_provenance_conflict");
      if (!isStoredContentQaSourceSubset(submitted.fact_check.source_urls, sources)) return toolError("content_qa_source_mismatch");
      if (submitted.decision === "PASS" && sources.length === 0) return toolError("content_qa_pass_requires_source");
      if (submitted.issues.some((issue) => issue.evidence_url && !sources.includes(issue.evidence_url))) return toolError("content_qa_evidence_mismatch");
      if (!await contentQaBannerImage(detail, fetch, readiness.banner_sha256)) return toolError("content_qa_banner_unavailable");
      const receipt = await recordContentQaVerdict(cfg, detail, submitted as ContentQaVerdict, release, currentProvenance);
      if (receipt.status === "duplicate_conflict") return toolError("content_qa_verdict_conflict");
      const output = { ...receipt, content_item_id: itemId, content_version_id: versionId, advisory_only: true, public_publish: false };
      return { content: jsonContent(output), structuredContent: output };
    } catch (error) { return toolError(errorCode(error, "content_qa_record_unavailable")); }
  });
  return mcp;
}

const handler = createMcpHandler(() => server());
const response = (body: Record<string, unknown>, status: number, headers = {}) => Response.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
export default async (req: Request, _context: Context): Promise<Response> => {
  const host = new URL(req.url).hostname.toLowerCase();
  if (![HOST, "localhost", "127.0.0.1"].includes(host)) return response({ error: "invalid_connector_host" }, 421);
  const cfg = contentQaConnectorConfig((name) => Netlify.env.get(name));
  if (!cfg) return response({ error: "content_qa_connector_not_configured" }, 503);
  if (!hasContentQaConnectorAccess(req, cfg.token)) return response({ error: "invalid_token" }, 401, { "WWW-Authenticate": 'Bearer realm="coineasy-content-qa"' });
  const declared = Number(req.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > MAX_REQUEST_BYTES) return response({ error: "mcp_request_too_large" }, 413);
  if (req.method === "POST" && declared === 0 && (await req.clone().arrayBuffer()).byteLength > MAX_REQUEST_BYTES) return response({ error: "mcp_request_too_large" }, 413);
  const result = await handler.fetch(req); const headers = new Headers(result.headers);
  headers.set("Cache-Control", "no-store"); headers.set("X-Content-Type-Options", "nosniff");
  return new Response(result.body, { status: result.status, statusText: result.statusText, headers });
};
export const config: Config = { path: "/api/content-qa/mcp" };
