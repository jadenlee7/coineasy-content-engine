import type { Config, Context } from "@netlify/functions";
import {
  contentCatalogConfig,
  isCatalogUuid,
} from "./_shared/content-catalog.mts";
import {
  ContentPublicationError,
  getStudioTelegramPublication,
  getStudioTelegramPublicationTarget,
  kickTelegramPublicationWorker,
  publicationWorkerConfig,
  requestStudioTelegramPublication,
  studioTelegramPublishClientAllowed,
  studioTelegramPublishEnabled,
} from "./_shared/content-publications.mts";
import {
  requireStudioSession,
  studioSessionJson,
} from "./_shared/studio-session.mts";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const MAX_BODY_BYTES = 1_024;

function publicationStatus(error: unknown): number {
  const code = error instanceof ContentPublicationError ? error.code : "";
  if (code === "telegram_publication_not_found") return 404;
  if (code === "telegram_publication_client_not_allowed") return 403;
  if (code === "mock_content_cannot_be_published") return 422;
  if (
    code === "invalid_telegram_publication"
    || code === "telegram_publication_kind_not_supported"
    || code === "telegram_publication_payload_incomplete"
  ) return 400;
  if (
    code === "telegram_publication_idempotency_conflict"
    || code === "telegram_publication_not_approved"
    || code === "telegram_publication_version_conflict"
    || code === "telegram_publication_conflict"
  ) return 409;
  return 502;
}

async function jsonBody(req: Request): Promise<Record<string, unknown> | null> {
  const declared = Number(req.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return null;
  const raw = await req.text();
  if (Buffer.byteLength(raw, "utf8") > MAX_BODY_BYTES) return null;
  try {
    const value: unknown = JSON.parse(raw);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (!new Set(["GET", "POST"]).has(req.method)) {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "GET, POST" });
  }
  const accessError = requireStudioSession(req);
  if (accessError) return accessError;
  if (
    req.method === "POST"
    && !studioTelegramPublishEnabled((name) => Netlify.env.get(name))
  ) {
    return studioSessionJson({ error: "telegram_publication_not_enabled" }, 503);
  }

  const contentItemId = (context.params.contentId || "").toLowerCase();
  if (!isCatalogUuid(contentItemId)) {
    return studioSessionJson({ error: "invalid_library_item_id" }, 400);
  }
  const config = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!config) {
    return studioSessionJson({ error: "content_catalog_not_configured" }, 503);
  }

  let contentVersionId = "";
  let idempotencyKey = "";
  if (req.method === "GET") {
    const url = new URL(req.url);
    const queryEntries = [...url.searchParams.entries()];
    if (
      queryEntries.length !== 2
      || queryEntries.some(([key]) => !["content_version_id", "channel"].includes(key))
      || !url.searchParams.has("content_version_id")
      || url.searchParams.get("channel") !== "telegram"
    ) {
      return studioSessionJson({ error: "invalid_telegram_publication" }, 400);
    }
    contentVersionId = (url.searchParams.get("content_version_id") || "").toLowerCase();
  } else {
    idempotencyKey = (req.headers.get("idempotency-key") || "").trim().toLowerCase();
    if (!UUID_PATTERN.test(idempotencyKey)) {
      return studioSessionJson({ error: "invalid_telegram_publication_idempotency_key" }, 400);
    }
    const body = await jsonBody(req);
    if (
      !body
      || Object.keys(body).length !== 2
      || Object.keys(body).some((key) => !["content_version_id", "channel"].includes(key))
      || body.channel !== "telegram"
      || typeof body.content_version_id !== "string"
    ) {
      return studioSessionJson({ error: "invalid_telegram_publication" }, 400);
    }
    contentVersionId = body.content_version_id.toLowerCase();
  }
  if (!isCatalogUuid(contentVersionId)) {
    return studioSessionJson({ error: "invalid_telegram_publication" }, 400);
  }

  try {
    if (req.method === "GET") {
      const publication = await getStudioTelegramPublication(
        config,
        contentItemId,
        contentVersionId,
      );
      if (!publication) {
        return studioSessionJson({ error: "telegram_publication_not_found" }, 404);
      }
      return studioSessionJson(publication, 200);
    }

    const item = await getStudioTelegramPublicationTarget(config, contentItemId);
    if (!item) {
      return studioSessionJson({ error: "telegram_publication_not_found" }, 404);
    }
    if (!studioTelegramPublishClientAllowed(
      item.client_id,
      (name) => Netlify.env.get(name),
    )) {
      return studioSessionJson({ error: "telegram_publication_client_not_allowed" }, 403);
    }
    if (item.content_kind !== "daily_news") {
      return studioSessionJson({ error: "telegram_publication_kind_not_supported" }, 400);
    }
    if (item.current_version_id !== contentVersionId) {
      return studioSessionJson({ error: "telegram_publication_version_conflict" }, 409);
    }

    const publication = await requestStudioTelegramPublication(
      config,
      contentItemId,
      contentVersionId,
      idempotencyKey,
    );
    let current = publication;
    if (!publication.reused && publication.status === "queued") {
      const worker = publicationWorkerConfig((name) => Netlify.env.get(name));
      if (worker) {
        await kickTelegramPublicationWorker(worker);
        try {
          current = await getStudioTelegramPublication(
            config,
            contentItemId,
            contentVersionId,
          ) || publication;
        } catch {
          // The queue receipt is durable. A status projection or worker kick
          // failure must not turn it into a retryable-looking queue failure.
        }
      }
    }
    return studioSessionJson(
      current,
      ["queued", "publishing"].includes(current.status) ? 202 : 200,
    );
  } catch (error) {
    const code = error instanceof ContentPublicationError
      ? error.code
      : "telegram_publication_storage_unavailable";
    return studioSessionJson({ error: code }, publicationStatus(error));
  }
};

export const config: Config = {
  path: "/api/library/:contentId/publish",
};
