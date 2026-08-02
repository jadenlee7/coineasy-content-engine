import type { Config, Context } from "@netlify/functions";
import {
  buildEditableSvg,
  effectiveEditableTemplateStyle,
  type EditableCardAssets,
  type EditableClientId,
  type EditableTemplateStyle,
} from "./_shared/editable-svg.mts";
import {
  fetchOfficialArticleHeroAssetDataUrls,
  fetchOfficialBrandLogoDataUrl,
  OFFICIAL_BRAND_ASSETS,
  type OfficialLogoVariant,
} from "./_shared/official-brand-assets.mts";
import { requireStudioSession } from "./_shared/studio-session.mts";

type EditableCardRequest = {
  spec?: unknown;
  template_style?: unknown;
  source_image_url?: unknown;
  source_visual_file?: unknown;
};

// Netlify's buffered Functions response limit is 6 MB. A binary image grows by
// roughly 4/3 when embedded as a base64 data URL, so 3 MB leaves about 2 MB for
// the SVG document and response metadata.
export const CLEANED_SOURCE_IMAGE_MAX_BYTES = 3_000_000;

function jsonError(error: string, status: number): Response {
  return Response.json({ error }, {
    status,
    headers: { "Cache-Control": "no-store", "Content-Type": "application/json; charset=utf-8" },
  });
}

function allowlistedSourceImage(value: unknown): string {
  if (typeof value !== "string" || !value) return "";
  try {
    const url = new URL(value);
    return url.protocol === "https:" && url.hostname === "pbs.twimg.com" ? url.toString() : "";
  } catch {
    return "";
  }
}

function cleanBaseUrl(value: string): string {
  return value.replace(/\/+$/, "");
}

export function clientScopedSourceVisualFile(
  value: unknown,
  clientId: EditableClientId,
): string {
  if (typeof value !== "string" || value.length > 512) return "";
  const escapedClientId = clientId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    `^${escapedClientId}/news_[0-9]+/source_visual_cleaned\\.jpg$`,
  ).test(value)
    ? value
    : "";
}

export function needsCleanedSquidVisual(
  clientId: EditableClientId,
  templateStyle: EditableTemplateStyle,
  spec: Record<string, unknown>,
): boolean {
  return clientId === "squid"
    && templateStyle === "remix"
    && spec.source_text_visible === true
    && Array.isArray(spec.translation_regions)
    && spec.translation_regions.length > 0;
}

export function requiredOfficialLogoVariant(
  clientId: EditableClientId,
  templateStyle: EditableTemplateStyle,
  spec: Record<string, unknown>,
): OfficialLogoVariant | null {
  if (templateStyle === "remix") {
    if (clientId === "squid" || spec.source_logo_visible === true) return null;
    return "dark";
  }
  if (clientId === "squid" && templateStyle === "classic") {
    return spec.creative_family === "milestone_metric"
      || spec.creative_family === "product_proof"
      ? "dark"
      : "light";
  }
  if (templateStyle === "signal") return "dark";
  return spec.theme === "yellow" ? "light" : "dark";
}

async function fetchImageDataUrl(
  url: string,
  maxBytes: number,
  headers?: HeadersInit,
): Promise<string> {
  const response = await fetch(url, { headers, signal: AbortSignal.timeout(12_000) });
  if (!response.ok) throw new Error("image_fetch_failed");
  const contentType = (response.headers.get("content-type") || "").split(";", 1)[0].toLowerCase();
  if (!/^image\/(?:png|jpeg|webp|svg\+xml)$/.test(contentType)) throw new Error("unsupported_image_type");
  const declaredSize = Number(response.headers.get("content-length") || 0);
  if (declaredSize > maxBytes) throw new Error("image_too_large");
  const bytes = Buffer.from(await response.arrayBuffer());
  if (bytes.length > maxBytes) throw new Error("image_too_large");
  return `data:${contentType};base64,${bytes.toString("base64")}`;
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") return jsonError("method_not_allowed", 405);

  const studioAccessError = requireStudioSession(req);
  if (studioAccessError) return studioAccessError;

  const clientId = context.params.clientId as EditableClientId | undefined;
  if (!clientId || !(clientId in OFFICIAL_BRAND_ASSETS)) return jsonError("unknown_client", 404);

  let body: EditableCardRequest;
  try {
    body = (await req.json()) as EditableCardRequest;
  } catch {
    return jsonError("invalid_json", 400);
  }
  if (!body.spec || typeof body.spec !== "object" || Array.isArray(body.spec)) {
    return jsonError("invalid_spec", 400);
  }
  const spec = body.spec as Record<string, unknown>;
  const allowedStyles = new Set<EditableTemplateStyle>(["remix", "classic", "editorial", "signal"]);
  const requestedTemplateStyle = typeof body.template_style === "string" && allowedStyles.has(body.template_style as EditableTemplateStyle)
    ? body.template_style as EditableTemplateStyle
    : "classic";
  const templateStyle = effectiveEditableTemplateStyle(clientId, requestedTemplateStyle);

  const siteOrigin = new URL(context.site.url).origin;
  const sourceImageUrl = allowlistedSourceImage(body.source_image_url);
  const sourceVisualFile = clientScopedSourceVisualFile(body.source_visual_file, clientId);
  const cleanedSourceRequired = needsCleanedSquidVisual(clientId, templateStyle, spec);
  if (typeof body.source_visual_file === "string" && body.source_visual_file && !sourceVisualFile) {
    return jsonError("invalid_source_visual_file", 400);
  }
  if (templateStyle === "remix" && !cleanedSourceRequired && !sourceImageUrl) {
    return jsonError("source_image_required", 422);
  }
  if (cleanedSourceRequired && !sourceVisualFile) {
    return jsonError("cleaned_source_required", 422);
  }
  const assets: EditableCardAssets = {};
  let officialLogoUnavailable = false;
  let officialSquidAssetsUnavailable = false;
  const assetRequests: Array<Promise<void>> = [];
  const logoVariant = requiredOfficialLogoVariant(clientId, templateStyle, spec);
  if (logoVariant) {
    assetRequests.push(
      fetchOfficialBrandLogoDataUrl(clientId, logoVariant, siteOrigin)
        .then((value) => {
          if (logoVariant === "dark") assets.logoDark = value;
          else assets.logoLight = value;
        })
        .catch(() => {
          officialLogoUnavailable = true;
        }),
    );
  }
  if (clientId === "squid" && templateStyle === "classic") {
    assetRequests.push(
      fetchOfficialArticleHeroAssetDataUrls(clientId, siteOrigin)
        .then((value) => {
          if (!value) {
            officialSquidAssetsUnavailable = true;
            return;
          }
          assets.squidFormLanguage = value.formLanguage;
          assets.squidSquib = value.squib;
          assets.squidBubbles = value.bubbles;
        })
        .catch(() => {
          officialSquidAssetsUnavailable = true;
        }),
    );
  }
  if (cleanedSourceRequired) {
    const apiSecret = Netlify.env.get("API_SECRET");
    if (!apiSecret) return jsonError("server_not_configured", 503);
    const railwayUrl = cleanBaseUrl(
      Netlify.env.get("RAILWAY_API_URL")
        || "https://coineasy-content-engine-production.up.railway.app",
    );
    const generatedSourceUrl = `${railwayUrl}/files/${sourceVisualFile
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`;
    assetRequests.push(
      fetchImageDataUrl(generatedSourceUrl, CLEANED_SOURCE_IMAGE_MAX_BYTES, { "X-API-Key": apiSecret })
        .then((value) => { assets.sourceImage = value; })
        .catch(() => undefined),
    );
  } else if (templateStyle === "remix" && sourceImageUrl) {
    assetRequests.push(
      fetchImageDataUrl(sourceImageUrl, 4_000_000)
        .then((value) => { assets.sourceImage = value; })
        .catch(() => undefined),
    );
  }
  await Promise.all(assetRequests);

  if (officialLogoUnavailable) {
    return jsonError("official_logo_unavailable", 503);
  }
  if (officialSquidAssetsUnavailable) {
    return jsonError("official_squid_assets_unavailable", 503);
  }
  if (cleanedSourceRequired && !assets.sourceImage) {
    return jsonError("cleaned_source_unavailable", 502);
  }
  if (templateStyle === "remix" && !assets.sourceImage) {
    return jsonError("source_image_unavailable", 502);
  }

  const svg = buildEditableSvg(clientId, templateStyle, spec, assets);
  const filename = `${clientId}-${templateStyle}-figma-editable.svg`;
  return new Response(svg, {
    status: 200,
    headers: {
      "Cache-Control": "no-store",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Content-Type": "image/svg+xml; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
    },
  });
};

export const config: Config = {
  path: "/api/editable-card/:clientId",
};
