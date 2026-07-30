import type { Config, Context } from "@netlify/functions";
import {
  contentCatalogConfig,
  ContentCatalogError,
  getContentLibraryItem,
  isCatalogUuid,
} from "./_shared/content-catalog.mts";
import { requireStudioSession, studioSessionJson } from "./_shared/studio-session.mts";
import {
  sendTelegramReviewNotification,
  telegramReviewConfig,
} from "./_shared/telegram-review-notification.mts";

const MAX_REQUEST_BYTES = 10_500_000;
const MAX_BANNER_BYTES = 10_000_000;
const ALLOWED_BANNER_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

function contentLength(req: Request): number | null {
  const raw = req.headers.get("content-length");
  if (!raw) return null;
  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : null;
}

async function validBannerBytes(banner: Blob): Promise<boolean> {
  const bytes = new Uint8Array(await banner.slice(0, 12).arrayBuffer());
  if (banner.type === "image/png") {
    return [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]
      .every((byte, index) => bytes[index] === byte);
  }
  if (banner.type === "image/jpeg") {
    return bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff;
  }
  if (banner.type === "image/webp") {
    return String.fromCharCode(...bytes.slice(0, 4)) === "RIFF"
      && String.fromCharCode(...bytes.slice(8, 12)) === "WEBP";
  }
  return false;
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "POST" });
  }
  const accessError = requireStudioSession(req);
  if (accessError) return accessError;

  const contentItemId = (context.params.contentId || "").toLowerCase();
  if (!isCatalogUuid(contentItemId)) {
    return studioSessionJson({ error: "invalid_library_item_id" }, 400);
  }
  const declaredBytes = contentLength(req);
  if (declaredBytes !== null && declaredBytes > MAX_REQUEST_BYTES) {
    return studioSessionJson({ error: "review_notification_too_large" }, 413);
  }

  const catalogConfig = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!catalogConfig) {
    return studioSessionJson({ error: "content_catalog_not_configured" }, 503);
  }
  const reviewConfig = telegramReviewConfig((name) => Netlify.env.get(name));
  if (!reviewConfig) {
    return studioSessionJson({ error: "telegram_review_not_configured" }, 503);
  }

  let form: FormData;
  try {
    form = await req.formData();
  } catch {
    return studioSessionJson({ error: "invalid_review_notification" }, 400);
  }
  const contentVersionId = String(form.get("content_version_id") || "").toLowerCase();
  if (!isCatalogUuid(contentVersionId)) {
    return studioSessionJson({ error: "invalid_review_notification" }, 400);
  }
  const rawBanner = form.get("banner");
  const banner = rawBanner instanceof Blob && rawBanner.size > 0 ? rawBanner : null;
  if (
    banner
    && (
      banner.size > MAX_BANNER_BYTES
      || !ALLOWED_BANNER_TYPES.has(banner.type)
      || !await validBannerBytes(banner)
    )
  ) {
    return studioSessionJson({ error: "invalid_review_notification_banner" }, 400);
  }

  try {
    const detail = await getContentLibraryItem(
      catalogConfig,
      contentItemId,
      fetch,
      180,
      AbortSignal.timeout(10_000),
    );
    if (!detail) return studioSessionJson({ error: "library_item_not_found" }, 404);
    if (detail.current_version_id !== contentVersionId) {
      return studioSessionJson({ error: "review_notification_version_conflict" }, 409);
    }
    if (detail.status !== "needs_review") {
      return studioSessionJson({ error: "review_notification_status_conflict" }, 409);
    }
    if (detail.current_version.generation_meta.mock_mode === true) {
      return studioSessionJson({ error: "mock_review_notification_disabled" }, 422);
    }
    const result = await sendTelegramReviewNotification(
      reviewConfig,
      detail,
      new URL(context.site.url).origin,
      banner,
    );
    if (!result.textSent) {
      return studioSessionJson({ error: "telegram_review_send_failed" }, 502);
    }
    return studioSessionJson({
      sent: true,
      photo_sent: result.photoSent,
      text_sent: result.textSent,
    }, 200);
  } catch (error) {
    const code = error instanceof ContentCatalogError
      ? error.code
      : "telegram_review_unavailable";
    return studioSessionJson({ error: code }, 503);
  }
};

export const config: Config = {
  path: "/api/library/:contentId/review-notification",
};
