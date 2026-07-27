import type { Config, Context } from "@netlify/functions";
import {
  contentCatalogConfig,
  ContentCatalogError,
  getContentLibraryItem,
  isCatalogUuid,
} from "./_shared/content-catalog.mts";
import { listContentPromotionRecommendations } from "./_shared/content-promotions.mts";
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
    try {
      const promotionSummary = await listContentPromotionRecommendations(
        config,
        contentItemId,
        item.current_version_id,
      );
      return studioSessionJson({
        ...item,
        promotion_recommendations: promotionSummary.items,
        manual_publications: promotionSummary.publications,
        promotions_available: true,
      }, 200);
    } catch {
      // Performance evidence is an optional editorial aid. A stale or
      // unavailable signal bridge must never hide the durable content item.
      return studioSessionJson({
        ...item,
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
