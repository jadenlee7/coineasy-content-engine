import type { Config, Context } from "@netlify/functions";
import {
  ArticleRequestInputError,
  articleRetryResponse,
  catalogRequestHash,
  validatedArticleRequest,
} from "./article.mts";
import {
  contentCatalogConfig,
  ContentCatalogError,
  findGeneratedContent,
  type ContentCatalogClient,
} from "./_shared/content-catalog.mts";
import {
  hasValidStudioAutomationAccess,
  requireStudioGenerationAccess,
} from "./_shared/studio-session.mts";
import {
  requireExpectedStudioRelease,
  STUDIO_EXPECTED_RELEASE_HEADER,
} from "./_shared/studio-release.mts";
import { STUDIO_BUILD_RELEASE_SHA } from "./_shared/studio-release.generated.mts";

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

export async function handleArticleResultRequest(
  req: Request,
  context: Context,
  buildReleaseSha: string | null = STUDIO_BUILD_RELEASE_SHA,
): Promise<Response> {
  if (req.method !== "GET" && req.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405);
  }
  const accessError = requireStudioGenerationAccess(req);
  if (accessError) return accessError;
  const reconcileOnly = req.method === "POST";
  const automationAccess = hasValidStudioAutomationAccess(req);
  if (reconcileOnly && !automationAccess) {
    return json({ error: "article_reconciliation_automation_only" }, 403);
  }
  if (
    reconcileOnly
    && req.headers.get(STUDIO_EXPECTED_RELEASE_HEADER) === null
  ) {
    return json({ error: "studio_release_mismatch" }, 503);
  }
  const studioReleaseError = requireExpectedStudioRelease(req, buildReleaseSha);
  if (studioReleaseError) return studioReleaseError;

  const clientParam = context.params.clientId;
  if (!clientParam || !ALLOWED_CLIENTS.has(clientParam as ContentCatalogClient)) {
    return json({ error: "unknown_client" }, 404);
  }
  const clientId = clientParam as ContentCatalogClient;
  const requestId = (context.params.requestId || "").trim().toLowerCase();
  if (!UUID_PATTERN.test(requestId)) {
    return json({ error: "invalid_article_idempotency_key" }, 400);
  }
  if (
    reconcileOnly
    && (req.headers.get("idempotency-key") || "").trim().toLowerCase() !== requestId
  ) {
    return json({ error: "invalid_article_idempotency_key" }, 400);
  }

  let requestHash = "";
  if (reconcileOnly) {
    let body: unknown;
    try {
      body = await req.json();
    } catch {
      return json({ error: "invalid_json" }, 400);
    }
    try {
      requestHash = validatedArticleRequest(body, clientId, true).requestHash;
    } catch (error) {
      const code = error instanceof ArticleRequestInputError
        ? error.code
        : "invalid_article_request";
      const status = error instanceof ArticleRequestInputError
        ? error.status
        : 400;
      return json({ error: code }, status);
    }
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
    if (reconcileOnly && catalogRequestHash(existing) !== requestHash) {
      return json({ error: "article_idempotency_conflict" }, 409);
    }
    return json(articleRetryResponse(existing, clientId));
  } catch (error) {
    const code = error instanceof ContentCatalogError
      ? error.code
      : "durable_catalog_lookup_failed";
    return json({ error: code }, code === "fact_check_regeneration_required" ? 409 : 503);
  }
}

export default handleArticleResultRequest;

export const config: Config = {
  path: "/api/article-result/:clientId/:requestId",
};
