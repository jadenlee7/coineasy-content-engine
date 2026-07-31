import type { Config, Context } from "@netlify/functions";
import { articleRetryResponse } from "./article.mts";
import {
  contentCatalogConfig,
  ContentCatalogError,
  findGeneratedContent,
  type ContentCatalogClient,
} from "./_shared/content-catalog.mts";
import { requireStudioGenerationAccess } from "./_shared/studio-session.mts";

const ALLOWED_CLIENTS = new Set<ContentCatalogClient>([
  "yellow",
  "origintrail",
  "squid",
  "babylon",
]);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "GET") {
    return json({ error: "method_not_allowed" }, 405);
  }
  const accessError = requireStudioGenerationAccess(req);
  if (accessError) return accessError;

  const clientParam = context.params.clientId;
  if (!clientParam || !ALLOWED_CLIENTS.has(clientParam as ContentCatalogClient)) {
    return json({ error: "unknown_client" }, 404);
  }
  const clientId = clientParam as ContentCatalogClient;
  const requestId = (context.params.requestId || "").trim().toLowerCase();
  if (!UUID_PATTERN.test(requestId)) {
    return json({ error: "invalid_article_idempotency_key" }, 400);
  }

  const storageConfig = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!storageConfig) {
    return json({ error: "durable_storage_not_configured" }, 503);
  }

  try {
    const existing = await findGeneratedContent(
      storageConfig,
      requestId,
      clientId,
      "article",
      fetch,
      AbortSignal.timeout(6_000),
    );
    if (!existing) {
      return json({
        status: "generating",
        content_item_id: requestId,
      }, 202);
    }
    return json(articleRetryResponse(existing, clientId));
  } catch (error) {
    const code = error instanceof ContentCatalogError
      ? error.code
      : "durable_catalog_lookup_failed";
    return json({ error: code }, 503);
  }
};

export const config: Config = {
  path: "/api/article-result/:clientId/:requestId",
};
