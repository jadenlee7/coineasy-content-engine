import type { Config, Context } from "@netlify/functions";
import { requireStudioSession } from "./_shared/studio-session.mts";

type ArticleRequest = {
  source_content?: unknown;
  source_type?: unknown;
  source_url?: unknown;
};

type RailwayArticleResponse = {
  client_id: string;
  content_type: "article";
  title: string;
  lead: string;
  sections: Array<{ id: string; heading: string; body: string }>;
  key_takeaways: string[];
  source_map: Array<{ source_url: string; applies_to: string[] }>;
  channel_copy: { telegram: string; x: string };
  markdown: string;
  duration_ms: number;
};

const ALLOWED_CLIENTS = new Set(["yellow", "origintrail", "squid", "babylon"]);
const ALLOWED_SOURCE_TYPES = new Set(["tweet", "blog", "article"]);
// Leave enough of Netlify's synchronous request window for response parsing and
// error handling instead of spending the full platform budget upstream.
const RAILWAY_ARTICLE_BUDGET_MS = 50_000;

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": "no-store",
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

function cleanBaseUrl(value: string): string {
  return value.replace(/\/+$/, "");
}

function validSourceUrl(value: string): boolean {
  if (!value) return true;
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") && Boolean(url.hostname);
  } catch {
    return false;
  }
}

export function isRailwayArticleResponse(value: unknown): value is RailwayArticleResponse {
  if (!value || typeof value !== "object") return false;
  const article = value as Partial<RailwayArticleResponse>;
  return typeof article.client_id === "string"
    && article.content_type === "article"
    && typeof article.title === "string"
    && Boolean(article.title.trim())
    && typeof article.lead === "string"
    && Boolean(article.lead.trim())
    && Array.isArray(article.sections)
    && article.sections.length >= 3
    && article.sections.length <= 5
    && article.sections.every((section) => section
      && typeof section.id === "string"
      && Boolean(section.id.trim())
      && typeof section.heading === "string"
      && Boolean(section.heading.trim())
      && typeof section.body === "string"
      && Boolean(section.body.trim()))
    && Array.isArray(article.key_takeaways)
    && article.key_takeaways.length >= 3
    && article.key_takeaways.length <= 5
    && article.key_takeaways.every((item) => typeof item === "string" && Boolean(item.trim()))
    && Array.isArray(article.source_map)
    && article.source_map.every((source) => source
      && typeof source.source_url === "string"
      && Boolean(source.source_url.trim())
      && validSourceUrl(source.source_url)
      && Array.isArray(source.applies_to)
      && source.applies_to.every((item) => typeof item === "string"))
    && article.channel_copy !== null
    && typeof article.channel_copy === "object"
    && typeof article.channel_copy.telegram === "string"
    && typeof article.channel_copy.x === "string"
    && typeof article.markdown === "string"
    && Boolean(article.markdown.trim())
    && typeof article.duration_ms === "number"
    && Number.isFinite(article.duration_ms)
    && article.duration_ms >= 0;
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") return json({ error: "method_not_allowed" }, 405);

  const studioAccessError = requireStudioSession(req);
  if (studioAccessError) return studioAccessError;

  const clientId = context.params.clientId;
  if (!clientId || !ALLOWED_CLIENTS.has(clientId)) {
    return json({ error: "unknown_client" }, 404);
  }

  const apiSecret = Netlify.env.get("API_SECRET");
  if (!apiSecret) return json({ error: "server_not_configured" }, 503);

  let body: ArticleRequest;
  try {
    body = (await req.json()) as ArticleRequest;
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const sourceContent = typeof body.source_content === "string" ? body.source_content.trim() : "";
  const sourceType = typeof body.source_type === "string" ? body.source_type : "article";
  const sourceUrl = typeof body.source_url === "string" ? body.source_url.trim() : "";
  if (sourceContent.length < 300 || sourceContent.length > 60_000) {
    return json({ error: "source_content_must_be_300_to_60000_chars" }, 422);
  }
  if (!ALLOWED_SOURCE_TYPES.has(sourceType)) {
    return json({ error: "invalid_source_type" }, 400);
  }
  if (!validSourceUrl(sourceUrl)) {
    return json({ error: "invalid_source_url" }, 400);
  }

  const railwayUrl = cleanBaseUrl(
    Netlify.env.get("RAILWAY_API_URL")
      || "https://coineasy-content-engine-production.up.railway.app",
  );

  try {
    const generationResponse = await fetch(
      `${railwayUrl}/clients/${encodeURIComponent(clientId)}/generate/article`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiSecret,
        },
        body: JSON.stringify({
          source_content: sourceContent,
          source_type: sourceType,
          source_url: sourceUrl,
        }),
        signal: AbortSignal.timeout(RAILWAY_ARTICLE_BUDGET_MS),
      },
    );

    if (!generationResponse.ok) {
      const detail = await generationResponse.text();
      return json({
        error: "generation_failed",
        upstream_status: generationResponse.status,
        detail: detail.slice(0, 500),
      }, generationResponse.status >= 500 ? 502 : generationResponse.status);
    }

    const result = await generationResponse.json();
    if (!isRailwayArticleResponse(result) || result.client_id !== clientId) {
      return json({ error: "invalid_article_response" }, 502);
    }
    return json(result);
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown_error";
    const timedOut = error instanceof Error && error.name === "TimeoutError";
    return json(
      { error: timedOut ? "generation_timeout" : "upstream_unavailable", detail: message },
      timedOut ? 504 : 502,
    );
  }
};

export const config: Config = {
  path: "/api/article/:clientId",
};
