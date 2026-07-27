import type { Config, Context } from "@netlify/functions";
import {
  contentCatalogConfig,
  isCatalogUuid,
} from "./_shared/content-catalog.mts";
import {
  ContentPromotionError,
  recordManualPublicationObservation,
  type PromotionChannel,
} from "./_shared/content-promotions.mts";
import {
  requireStudioSession,
  studioSessionJson,
} from "./_shared/studio-session.mts";

const CHANNELS = new Set(["x", "telegram"]);
const MAX_BODY_BYTES = 2_048;

async function body(req: Request): Promise<Record<string, unknown> | null> {
  const declared = Number(req.headers.get("content-length") || 0);
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return null;
  const raw = await req.text();
  if (Buffer.byteLength(raw, "utf8") > MAX_BODY_BYTES) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
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

  const contentItemId = context.params.contentId;
  if (!isCatalogUuid(contentItemId)) {
    return studioSessionJson({ error: "invalid_library_item_id" }, 400);
  }
  const payload = await body(req);
  if (
    !payload
    || Object.keys(payload).some((key) =>
      !new Set(["content_version_id", "channel", "external_url"]).has(key)
    )
    || !isCatalogUuid(payload.content_version_id)
    || typeof payload.channel !== "string"
    || !CHANNELS.has(payload.channel)
    || typeof payload.external_url !== "string"
  ) {
    return studioSessionJson({ error: "invalid_publication_observation" }, 400);
  }
  const config = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!config) {
    return studioSessionJson({ error: "content_catalog_not_configured" }, 503);
  }

  try {
    const receipt = await recordManualPublicationObservation(
      config,
      contentItemId,
      payload.content_version_id,
      payload.channel as PromotionChannel,
      payload.external_url,
    );
    return studioSessionJson(receipt, receipt.reused ? 200 : 201);
  } catch (error) {
    const code = error instanceof ContentPromotionError
      ? error.code
      : "publication_observation_unavailable";
    const status = code === "invalid_publication_observation"
      || code === "invalid_library_item_id"
      ? 400
      : code === "publication_observation_conflict"
        ? 409
        : 502;
    return studioSessionJson({ error: code }, status);
  }
};

export const config: Config = {
  path: "/api/library/:contentId/performance",
};
