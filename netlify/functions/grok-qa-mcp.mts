import type { Config, Context } from "@netlify/functions";
import { createMcpHandler, McpServer } from "@modelcontextprotocol/server";
import { z } from "zod";

import {
  contentCatalogConfig,
  ContentCatalogError,
  getContentLibraryItem,
  isCatalogUuid,
  listContentLibrary,
} from "./_shared/content-catalog.mts";
import {
  buildGrokQaReviewPackage,
  claimGrokQaVerdict,
  finalizeGrokQaVerdict,
  grokQaBannerImage,
  grokQaConnectorConfig,
  grokQaListItem,
  grokQaRelayConfig,
  grokQaSourceUrls,
  hasGrokQaConnectorAccess,
  sendGrokQaVerdictOutcome,
  type GrokQaVerdict,
} from "./_shared/grok-qa.mts";
import { grokQaOauthConfig } from "./_shared/grok-qa-oauth.mts";

const PRODUCTION_HOST = "coineasy-newscard.netlify.app";
const MAX_MCP_REQUEST_BYTES = 128 * 1024;

const clientSchema = z.enum(["yellow", "origintrail", "squid", "babylon"]);
const kindSchema = z.enum(["daily_news", "article", "tutorial"]);
const decisionSchema = z.enum(["PASS", "WARN", "BLOCK"]);
const nextActionSchema = z.enum([
  "ready_for_human_approval",
  "human_review",
  "verify_source",
  "revise_copy",
  "revise_banner",
]);
const uuidSchema = z.string().uuid();
const safeUrlSchema = z.string().url().max(2_048).refine((value) => {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.hash;
  } catch {
    return false;
  }
}, "Only credential-free HTTPS source URLs are accepted");

const verdictSchema = z.object({
  decision: decisionSchema.describe("Overall advisory QA verdict"),
  summary: z.string().trim().min(10).max(800),
  fact_check: z.object({
    status: decisionSchema,
    checks: z.array(z.string().trim().min(3).max(300)).min(1).max(6),
    source_urls: z.array(safeUrlSchema).max(8),
  }).strict(),
  brand_check: z.object({
    status: decisionSchema,
    checks: z.array(z.string().trim().min(3).max(300)).min(1).max(6),
  }).strict(),
  issues: z.array(z.object({
    severity: z.enum(["WARN", "BLOCK"]),
    code: z.string().regex(/^[a-z][a-z0-9_]{2,47}$/),
    message: z.string().trim().min(3).max(500),
    evidence_url: safeUrlSchema.optional(),
  }).strict()).max(3),
  next_action: nextActionSchema,
}).strict().superRefine((value, context) => {
  if (value.decision === "PASS") {
    if (
      value.fact_check.status !== "PASS"
      || value.brand_check.status !== "PASS"
      || value.issues.length !== 0
      || value.next_action !== "ready_for_human_approval"
    ) {
      context.addIssue({
        code: "custom",
        message: "PASS requires both checks to PASS, no issues, and ready_for_human_approval",
      });
    }
  } else if (value.next_action === "ready_for_human_approval") {
    context.addIssue({
      code: "custom",
      message: "WARN or BLOCK cannot request ready_for_human_approval",
    });
  }
  if (
    value.decision === "BLOCK"
    && value.fact_check.status !== "BLOCK"
    && value.brand_check.status !== "BLOCK"
    && !value.issues.some((issue) => issue.severity === "BLOCK")
  ) {
    context.addIssue({
      code: "custom",
      message: "BLOCK requires a blocking fact, brand, or issue finding",
    });
  }
});

function jsonContent(value: unknown): Array<{ type: "text"; text: string }> {
  return [{ type: "text", text: JSON.stringify(value) }];
}

function toolError(code: string) {
  const result = { error: code };
  return {
    isError: true,
    content: jsonContent(result),
    structuredContent: result,
  };
}

function configuredCatalog() {
  const config = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!config) throw new Error("content_catalog_not_configured");
  return config;
}

function studioReviewUrl(contentItemId: string): string {
  const url = new URL(`https://${PRODUCTION_HOST}/`);
  url.searchParams.set("view", "library");
  url.searchParams.set("content", contentItemId);
  return url.toString();
}

function isStoredSourceSubset(submitted: string[], stored: string[]): boolean {
  const allowed = new Set(stored);
  return new Set(submitted).size === submitted.length
    && submitted.every((url) => allowed.has(url));
}

function mcpServer(): McpServer {
  const server = new McpServer({
    name: "coineasy-grok-qa",
    version: "1.0.0",
  });

  server.registerTool(
    "coineasy_list_needs_review",
    {
      title: "List CoinEasy review items",
      description: "List up to five non-mock Content Studio items currently awaiting human review. This is read-only and cannot approve or publish content.",
      inputSchema: z.object({
        client_id: clientSchema.optional(),
        content_kind: kindSchema.optional(),
        limit: z.number().int().min(1).max(5).default(5),
      }).strict(),
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ client_id, content_kind, limit }) => {
      try {
        const page = await listContentLibrary(configuredCatalog(), {
          clientId: client_id || null,
          contentKind: content_kind || null,
          status: "needs_review",
          limit,
        });
        const items = page.items
          .filter((item) => !item.mock_mode && item.status === "needs_review")
          .map(grokQaListItem);
        const output = { items, count: items.length, next_cursor: null };
        return { content: jsonContent(output), structuredContent: output };
      } catch (error) {
        const code = error instanceof ContentCatalogError
          ? error.code
          : error instanceof Error ? error.message : "qa_list_unavailable";
        return toolError(code);
      }
    },
  );

  server.registerTool(
    "coineasy_get_review_package",
    {
      title: "Get exact CoinEasy review package",
      description: "Read one exact non-mock needs_review version, its generated Korean copy, automated QA, official source URLs, and banner preview. Raw submitted source text and signed asset URLs are never exposed.",
      inputSchema: z.object({
        content_item_id: uuidSchema,
        content_version_id: uuidSchema,
      }).strict(),
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false,
      },
    },
    async ({ content_item_id, content_version_id }) => {
      try {
        const detail = await getContentLibraryItem(
          configuredCatalog(),
          content_item_id.toLowerCase(),
          fetch,
          60,
          AbortSignal.timeout(10_000),
        );
        if (!detail) return toolError("qa_item_not_found");
        if (detail.current_version_id !== content_version_id.toLowerCase()) {
          return toolError("qa_version_conflict");
        }
        const output = buildGrokQaReviewPackage(detail);
        const image = await grokQaBannerImage(detail);
        return {
          content: [
            ...jsonContent(output),
            ...(image ? [{
              type: "image" as const,
              data: image.data,
              mimeType: image.mimeType,
            }] : []),
          ],
          structuredContent: output,
        };
      } catch (error) {
        const code = error instanceof ContentCatalogError
          ? error.code
          : error instanceof Error ? error.message : "qa_package_unavailable";
        return toolError(code);
      }
    },
  );

  server.registerTool(
    "coineasy_submit_qa_verdict",
    {
      title: "Submit advisory CoinEasy QA verdict",
      description: "Submit one structured PASS, WARN, or BLOCK verdict for the exact current version to the private CoinEasy Content Ops room. It is advisory only: this tool cannot approve Studio content, publish to Telegram/X, or choose a destination. A durable per-version receipt prevents duplicate delivery.",
      inputSchema: z.object({
        content_item_id: uuidSchema,
        content_version_id: uuidSchema,
        verdict: verdictSchema,
      }).strict(),
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: true,
      },
    },
    async ({ content_item_id, content_version_id, verdict }) => {
      const itemId = content_item_id.toLowerCase();
      const versionId = content_version_id.toLowerCase();
      if (!isCatalogUuid(itemId) || !isCatalogUuid(versionId)) {
        return toolError("invalid_qa_identity");
      }
      try {
        const catalog = configuredCatalog();
        const relay = grokQaRelayConfig((name) => Netlify.env.get(name));
        if (!relay) return toolError("qa_relay_not_configured");
        const detail = await getContentLibraryItem(
          catalog,
          itemId,
          fetch,
          60,
          AbortSignal.timeout(10_000),
        );
        if (!detail) return toolError("qa_item_not_found");
        if (detail.current_version_id !== versionId) return toolError("qa_version_conflict");
        if (detail.status !== "needs_review") return toolError("qa_status_conflict");
        if (detail.current_version.generation_meta.mock_mode === true) {
          return toolError("qa_mock_content_disabled");
        }
        const storedSources = grokQaSourceUrls(detail);
        if (!isStoredSourceSubset(verdict.fact_check.source_urls, storedSources)) {
          return toolError("qa_source_mismatch");
        }
        if (verdict.decision === "PASS" && storedSources.length === 0) {
          return toolError("qa_pass_requires_source");
        }
        for (const issue of verdict.issues) {
          if (issue.evidence_url && !storedSources.includes(issue.evidence_url)) {
            return toolError("qa_evidence_mismatch");
          }
        }

        // Fetch and verify the exact current-version banner before consuming a
        // receipt. Keep these bytes for the relay so a bannerless or swapped
        // asset cannot poison the durable per-version delivery receipt.
        const banner = await grokQaBannerImage(detail);
        if (!banner) return toolError("qa_banner_unavailable");

        const receipt = await claimGrokQaVerdict(
          catalog,
          itemId,
          versionId,
          verdict as GrokQaVerdict,
        );
        if (receipt.status === "duplicate_conflict") {
          return toolError("qa_verdict_conflict");
        }
        if (!receipt.claimed) {
          const output = {
            accepted: receipt.status === "sent",
            duplicate: true,
            delivery_status: receipt.status,
            content_item_id: itemId,
            content_version_id: versionId,
            decision: receipt.decision,
            advisory_only: true,
          };
          return { content: jsonContent(output), structuredContent: output };
        }
        if (!receipt.payload_sha256) return toolError("qa_receipt_invalid_response");

        const relayOutcome = await sendGrokQaVerdictOutcome(
          relay,
          detail,
          verdict as GrokQaVerdict,
          studioReviewUrl(itemId),
          fetch,
          banner.sha256,
          banner,
        );
        if (relayOutcome === "delivery_unknown") {
          return toolError("qa_delivery_state_unknown_no_retry");
        }
        const sent = relayOutcome === "sent";
        try {
          await finalizeGrokQaVerdict(
            catalog,
            versionId,
            receipt.payload_sha256,
            sent ? "sent" : "failed",
            sent ? null : "telegram_delivery_failed",
          );
        } catch {
          return toolError("qa_delivery_state_unknown_no_retry");
        }
        if (!sent) return toolError("qa_private_relay_failed_no_retry");
        const output = {
          accepted: true,
          duplicate: false,
          delivery_status: "sent",
          content_item_id: itemId,
          content_version_id: versionId,
          decision: verdict.decision,
          advisory_only: true,
          public_publish: false,
        };
        return { content: jsonContent(output), structuredContent: output };
      } catch (error) {
        const code = error instanceof ContentCatalogError
          ? error.code
          : error instanceof Error ? error.message : "qa_verdict_unavailable";
        return toolError(code);
      }
    },
  );

  return server;
}

const handler = createMcpHandler(() => mcpServer());

function jsonResponse(body: Record<string, unknown>, status: number, headers = {}): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      ...headers,
    },
  });
}

export default async (req: Request, _context: Context): Promise<Response> => {
  const requestUrl = new URL(req.url);
  const host = requestUrl.hostname.toLowerCase();
  if (host !== PRODUCTION_HOST && host !== "localhost" && host !== "127.0.0.1") {
    return jsonResponse({ error: "invalid_connector_host" }, 421);
  }
  const config = grokQaConnectorConfig((name) => Netlify.env.get(name));
  if (!config) return jsonResponse({ error: "grok_qa_connector_not_configured" }, 503);
  if (!hasGrokQaConnectorAccess(req, config.token)) {
    const oauth = grokQaOauthConfig((name) => Netlify.env.get(name), requestUrl.origin);
    const metadata = oauth
      ? `, resource_metadata="${oauth.issuer}/.well-known/oauth-protected-resource/api/grok-qa/mcp"`
      : "";
    return jsonResponse({ error: "invalid_token" }, 401, {
      "WWW-Authenticate": `Bearer realm="coineasy-grok-qa"${metadata}`,
    });
  }
  const declared = Number(req.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > MAX_MCP_REQUEST_BYTES) {
    return jsonResponse({ error: "mcp_request_too_large" }, 413);
  }
  if (req.method === "POST" && declared === 0) {
    const actual = (await req.clone().arrayBuffer()).byteLength;
    if (actual > MAX_MCP_REQUEST_BYTES) {
      return jsonResponse({ error: "mcp_request_too_large" }, 413);
    }
  }
  const response = await handler.fetch(req);
  const headers = new Headers(response.headers);
  headers.set("Cache-Control", "no-store");
  headers.set("X-Content-Type-Options", "nosniff");
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
};

export const config: Config = {
  path: "/api/grok-qa/mcp",
};
