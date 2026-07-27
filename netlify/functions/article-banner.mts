import type { Config, Context } from "@netlify/functions";
import {
  buildArticleBannerSvg,
  type ArticleBannerInput,
} from "./_shared/article-banner-svg.mts";
import type { EditableClientId } from "./_shared/editable-svg.mts";
import { requireStudioSession } from "./_shared/studio-session.mts";

type ArticleBannerRequest = {
  title?: unknown;
  lead?: unknown;
  source_url?: unknown;
  date?: unknown;
};

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

function requiredText(value: unknown, minimum: number, maximum: number): string {
  if (typeof value !== "string") return "";
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length >= minimum && normalized.length <= maximum ? normalized : "";
}

function optionalText(value: unknown, maximum: number): string {
  if (value === undefined || value === null || value === "") return "";
  if (typeof value !== "string") return "";
  const normalized = value.replace(/\s+/g, " ").trim();
  return normalized.length <= maximum ? normalized : "";
}

function optionalWebUrl(value: unknown): string {
  const normalized = optionalText(value, 2_048);
  if (!normalized) return "";
  try {
    const url = new URL(normalized);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : "";
  } catch {
    return "";
  }
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
  if (req.method !== "POST") return jsonError("method_not_allowed", 405);

  const studioAccessError = requireStudioSession(req);
  if (studioAccessError) return studioAccessError;

  const clientId = context.params.clientId as EditableClientId | undefined;
  if (!clientId || !(clientId in CLIENT_LOGOS)) return jsonError("unknown_client", 404);

  let body: ArticleBannerRequest;
  try {
    body = (await req.json()) as ArticleBannerRequest;
  } catch {
    return jsonError("invalid_json", 400);
  }
  const title = requiredText(body.title, 2, 200);
  const lead = requiredText(body.lead, 2, 800);
  if (!title) return jsonError("invalid_title", 400);
  if (!lead) return jsonError("invalid_lead", 400);

  const sourceUrl = optionalWebUrl(body.source_url);
  if (body.source_url && !sourceUrl) return jsonError("invalid_source_url", 400);
  const date = optionalText(body.date, 40);
  if (body.date && !date) return jsonError("invalid_date", 400);

  const siteOrigin = new URL(context.site.url).origin;
  const logoUrl = new URL(CLIENT_LOGOS[clientId], siteOrigin).toString();
  const logoDataUrl = await fetchLogoDataUrl(logoUrl).catch(() => "");
  const input: ArticleBannerInput = { title, lead, sourceUrl, date };
  const svg = buildArticleBannerSvg(clientId, input, logoDataUrl);

  return new Response(svg, {
    status: 200,
    headers: {
      "Cache-Control": "no-store",
      "Content-Disposition": `attachment; filename="${clientId}-article-banner-1200x630.svg"`,
      "Content-Type": "image/svg+xml; charset=utf-8",
      "X-Content-Type-Options": "nosniff",
    },
  });
};

export const config: Config = {
  path: "/api/article-banner/:clientId",
};
