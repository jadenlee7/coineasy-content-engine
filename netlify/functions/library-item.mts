import type { Config, Context } from "@netlify/functions";
import {
  contentCatalogConfig,
  ContentCatalogError,
  getContentLibraryItem,
  isCatalogUuid,
} from "./_shared/content-catalog.mts";
import { listContentPromotionRecommendations } from "./_shared/content-promotions.mts";
import {
  getStudioTelegramPublication,
  studioTelegramPublishClientAllowed,
  studioTelegramPublishEnabled,
  type StudioTelegramPublication,
} from "./_shared/content-publications.mts";
import { getContentReviewSummary } from "./_shared/content-reviews.mts";
import { requireStudioSession, studioSessionJson } from "./_shared/studio-session.mts";

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "GET") {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "GET" });
  }

  const accessError = requireStudioSession(req);
  if (accessError) return accessError;

  const contentItemId = context.params.contentId;
  if (!isCatalogUuid(contentItemId)) {
    return studioSessionJson({ error: "invalid_library_item_id" }, 400);
  }
  const config = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!config) return studioSessionJson({ error: "content_catalog_not_configured" }, 503);

  try {
    const item = await getContentLibraryItem(config, contentItemId);
    if (!item) return studioSessionJson({ error: "library_item_not_found" }, 404);
    let latestReview = null;
    try {
      latestReview = await getContentReviewSummary(config, contentItemId);
    } catch {
      // Keep the durable item readable during a rolling database migration.
    }
    const telegramPublishingEnabled = studioTelegramPublishEnabled(
      (name) => Netlify.env.get(name),
    );
    const telegramClientAllowed = studioTelegramPublishClientAllowed(
      item.client_id,
      (name) => Netlify.env.get(name),
    );
    let telegramPublication: StudioTelegramPublication | null = null;
    let telegramPublicationAvailable = false;
    try {
      telegramPublication = await getStudioTelegramPublication(
        config,
        contentItemId,
        item.current_version_id,
      );
      telegramPublicationAvailable = true;
    } catch {
      // Status remains readable after a feature rollback, but a rolling
      // migration or projection outage must fail closed for new requests.
    }
    const publicationFields = {
      publication_capabilities: {
        telegram: telegramPublishingEnabled
          && telegramClientAllowed
          && telegramPublicationAvailable,
        telegram_client_allowed: telegramClientAllowed,
      },
      telegram_publication: telegramPublication,
    };
    try {
      const promotionSummary = await listContentPromotionRecommendations(
        config,
        contentItemId,
        item.current_version_id,
      );
      return studioSessionJson({
        ...item,
        latest_review: latestReview,
        ...publicationFields,
        promotion_recommendations: promotionSummary.items,
        manual_publications: promotionSummary.publications,
        promotions_available: true,
      }, 200);
    } catch {
      // Performance evidence is an optional editorial aid. A stale or
      // unavailable signal bridge must never hide the durable content item.
      return studioSessionJson({
        ...item,
        latest_review: latestReview,
        ...publicationFields,
        promotion_recommendations: [],
        manual_publications: [],
        promotions_available: false,
      }, 200);
    }
  } catch (error) {
    const code = error instanceof ContentCatalogError
      ? error.code
      : "durable_library_unavailable";
    const statusCode = code === "invalid_library_item_id" ? 400 : 502;
    return studioSessionJson({ error: code }, statusCode);
  }
};

export const config: Config = {
  path: "/api/library/:contentId",
};
