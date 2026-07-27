import type { Config, Context } from "@netlify/functions";
import {
  buildArticleBannerSvg,
  buildArticleInlineVisualSvg,
} from "./_shared/article-banner-svg.mts";
import {
  articleVisualFilename,
  deriveArticleVisuals,
} from "./_shared/article-visual-plan.mts";
import {
  contentCatalogConfig,
  ContentCatalogError,
  getContentLibraryItem,
} from "./_shared/content-catalog.mts";
import type { EditableClientId } from "./_shared/editable-svg.mts";
import { requireStudioSession } from "./_shared/studio-session.mts";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const VISUAL_IDS = new Set(["hero", "visual-1", "visual-2"]);
const CLIENT_LOGOS: Record<EditableClientId, string> = {
  yellow: "/assets/brands/yellow-dark.svg",
  origintrail: "/assets/brands/origintrail-dark.png",
  squid: "/assets/brands/squid-dark.png",
  babylon: "/assets/brands/babylon-dark.png",
};

function jsonError(error: string, status: number): Response {
  return Response.json({ error }, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function firstWebUrl(...values: unknown[]): string {
  for (const value of values) {
    if (typeof value !== "string" || !value.trim()) continue;
    try {
      const url = new URL(value.trim());
      if (url.protocol === "https:" || url.protocol === "http:") return url.toString();
    } catch {
      // Continue to the next immutable source record.
    }
  }
  return "";
}

async function fetchLogoDataUrl(url: string): Promise<string> {
  const response = await fetch(url, { signal: AbortSignal.timeout(8_000) });
  if (!response.ok) throw new Error("logo_fetch_failed");
  const contentType = (response.headers.get("content-type") || "").split(";", 1)[0].toLowerCase();
  if (!/^image\/(?:png|svg\+xml)$/.test(contentType)) throw new Error("unsupported_logo_type");
  const declaredSize = Number(response.headers.get("content-length") || 0);
  if (declaredSize > 512_000) throw new Error("logo_too_large");
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length > 512_000) throw new Error("logo_too_large");
  return `data:${contentType};base64,${bytes.toString("base64")}`;
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "GET") return jsonError("method_not_allowed", 405);
  const studioAccessError = requireStudioSession(req);
  if (studioAccessError) return studioAccessError;

  const contentId = (context.params.contentId || "").toLowerCase();
  const visualId = context.params.visualId || "";
  if (!UUID_PATTERN.test(contentId)) return jsonError("invalid_content_id", 400);
  if (!VISUAL_IDS.has(visualId)) return jsonError("invalid_visual_id", 400);

  const storageConfig = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!storageConfig) return jsonError("durable_storage_not_configured", 503);

  let detail;
  try {
    detail = await getContentLibraryItem(
      storageConfig,
      contentId,
      fetch,
      60,
      AbortSignal.timeout(10_000),
    );
  } catch (error) {
    const code = error instanceof ContentCatalogError
      ? error.code
      : "durable_library_unavailable";
    return jsonError(code, 503);
  }
  if (!detail) return jsonError("content_not_found", 404);
  if (detail.content_kind !== "article") return jsonError("content_is_not_article", 422);

  const clientId = detail.client_id as EditableClientId;
  const content = detail.current_version.content;
  const sections = Array.isArray(content.sections) ? content.sections : [];
  const keyTakeaways = Array.isArray(content.key_takeaways) ? content.key_takeaways : [];
  const visuals = deriveArticleVisuals({
    title: detail.title,
    sections,
    key_takeaways: keyTakeaways,
    visuals: content.visuals,
  });
  if (visuals.length !== 2) return jsonError("article_visuals_unavailable", 422);

  const source = isRecord(content.source) ? content.source : {};
  const sourceMap = Array.isArray(content.source_map) ? content.source_map : [];
  const sourceUrl = firstWebUrl(
    source.url,
    source.source_url,
    isRecord(sourceMap[0]) ? sourceMap[0].source_url : "",
  );
  const date = detail.created_at.slice(0, 10).replace(/-/g, ".");
  const siteOrigin = new URL(context.site.url).origin;
  const logoUrl = new URL(CLIENT_LOGOS[clientId], siteOrigin).toString();
  const logoDataUrl = await fetchLogoDataUrl(logoUrl).catch(() => "");

  let svg: string;
  if (visualId === "hero") {
    svg = buildArticleBannerSvg(clientId, {
      title: visuals[0].headline,
      lead: visuals[0].caption,
      sourceUrl,
      date,
      motif: visuals[0].motif,
    }, logoDataUrl);
  } else {
    const visual = visuals.find(item => item.id === visualId);
    if (!visual) return jsonError("article_visual_not_found", 404);
    svg = buildArticleInlineVisualSvg(clientId, {
      visual,
      sourceUrl,
      date,
    }, logoDataUrl);
  }

  const filename = articleVisualFilename(
    clientId,
    visualId as "hero" | "visual-1" | "visual-2",
    "svg",
  );
  return new Response(svg, {
    status: 200,
    headers: {
      "Cache-Control": "private, no-store",
      "Content-Disposition": `inline; filename="${filename}"`,
      "Content-Type": "image/svg+xml; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
    },
  });
};

export const config: Config = {
  path: "/api/article-visual/:contentId/:visualId",
};
