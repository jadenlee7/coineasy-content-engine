import type { Config, Context } from "@netlify/functions";
import {
  contentCatalogConfig,
  isCatalogUuid,
} from "./_shared/content-catalog.mts";
import {
  cancelStudioTelegramDeliveryUnknown,
  ContentPublicationError,
  studioTelegramPublishEnabled,
} from "./_shared/content-publications.mts";
import {
  requireStudioSession,
  studioSessionJson,
} from "./_shared/studio-session.mts";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ISO_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const MAX_BODY_BYTES = 2_048;
const BODY_KEYS = new Set([
  "content_version_id",
  "publication_id",
  "delivery_started_at",
  "resolution",
  "public_channel",
  "channel_checked",
  "caption_checked",
  "png_checked",
]);

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

async function jsonBody(req: Request): Promise<Record<string, unknown> | null> {
  const declared = Number(req.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return null;
  const raw = await req.text();
  if (Buffer.byteLength(raw, "utf8") > MAX_BODY_BYTES) return null;
  try {
    return objectValue(JSON.parse(raw));
  } catch {
    return null;
  }
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "POST" });
  }
  const accessError = requireStudioSession(req);
  if (accessError) return accessError;

  // Unknown-delivery resolution is an incident/rollback operation. New Studio
  // publication requests must remain disabled while an operator performs it.
  if (studioTelegramPublishEnabled((name) => Netlify.env.get(name))) {
    return studioSessionJson({ error: "telegram_resolution_requires_publication_disabled" }, 409);
  }

  const contentItemId = (context.params.contentId || "").toLowerCase();
  const idempotencyKey = (req.headers.get("idempotency-key") || "").trim().toLowerCase();
  if (!isCatalogUuid(contentItemId) || !UUID_PATTERN.test(idempotencyKey)) {
    return studioSessionJson({ error: "invalid_telegram_delivery_resolution" }, 400);
  }
  const config = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!config) {
    return studioSessionJson({ error: "content_catalog_not_configured" }, 503);
  }

  const body = await jsonBody(req);
  const keys = Object.keys(body || {});
  const contentVersionId = String(body?.content_version_id || "").toLowerCase();
  const publicationId = String(body?.publication_id || "").toLowerCase();
  const deliveryStartedAt = String(body?.delivery_started_at || "");
  if (
    !body
    || keys.length !== BODY_KEYS.size
    || keys.some((key) => !BODY_KEYS.has(key))
    || !isCatalogUuid(contentVersionId)
    || !isCatalogUuid(publicationId)
    || !ISO_TIMESTAMP_PATTERN.test(deliveryStartedAt)
    || Number.isNaN(Date.parse(deliveryStartedAt))
    || new Date(deliveryStartedAt).toISOString() !== deliveryStartedAt
    || body.resolution !== "confirmed_not_observed_cancelled"
    || body.public_channel !== "squid_kor_update"
    || body.channel_checked !== true
    || body.caption_checked !== true
    || body.png_checked !== true
  ) {
    return studioSessionJson({ error: "invalid_telegram_delivery_resolution" }, 400);
  }

  try {
    const publication = await cancelStudioTelegramDeliveryUnknown(config, {
      contentItemId,
      contentVersionId,
      publicationId,
      deliveryStartedAt,
      publicChannel: "squid_kor_update",
      idempotencyKey,
    });
    return studioSessionJson(publication, publication.reused ? 200 : 201);
  } catch (error) {
    const code = error instanceof ContentPublicationError
      ? error.code
      : "telegram_publication_storage_unavailable";
    const status = code === "telegram_publication_not_found"
      ? 404
      : code === "invalid_telegram_delivery_resolution"
        ? 400
        : code === "telegram_publication_storage_unavailable"
          ? 502
          : 409;
    return studioSessionJson({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/library/:contentId/publish-resolution",
};
