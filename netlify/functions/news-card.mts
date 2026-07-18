import type { Config, Context } from "@netlify/functions";
import {
  resolveSourceInput,
  SourceInputError,
  type ResolvedSource,
} from "./_shared/source-content.mts";

type NewsCardRequest = {
  source_content?: unknown;
  source_type?: unknown;
  source_url?: unknown;
  mock_mode?: unknown;
  template_style?: unknown;
};

type RailwayNewsCardResponse = {
  client_id: string;
  content_type: string;
  spec: Record<string, unknown>;
  png_path: string;
  template_style: string;
  manifest_path: string;
  duration_ms: number;
};

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

function generatedFilePath(pngPath: string, clientId: string): string | null {
  const marker = `/${clientId}/`;
  const markerIndex = pngPath.lastIndexOf(marker);
  if (markerIndex < 0) return null;
  return pngPath.slice(markerIndex + 1);
}

export default async (req: Request, context: Context): Promise<Response> => {
  if (req.method !== "POST") {
    return json({ error: "method_not_allowed" }, 405);
  }

  const clientId = context.params.clientId;
  const allowedClients = new Set(["yellow", "origintrail", "squid", "babylon"]);
  if (!clientId || !allowedClients.has(clientId)) {
    return json({ error: "unknown_client" }, 404);
  }

  const apiSecret = Netlify.env.get("API_SECRET");
  if (!apiSecret) {
    return json({ error: "server_not_configured" }, 503);
  }

  let body: NewsCardRequest;
  try {
    body = (await req.json()) as NewsCardRequest;
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const sourceContent = typeof body.source_content === "string" ? body.source_content.trim() : "";
  const sourceType = typeof body.source_type === "string" ? body.source_type : "tweet";
  const sourceUrl = typeof body.source_url === "string" ? body.source_url.trim() : "";
  const templateStyle = typeof body.template_style === "string" ? body.template_style : "classic";
  const allowedSourceTypes = new Set(["tweet", "blog", "article"]);
  const allowedTemplateStyles = new Set(["classic", "editorial", "signal"]);

  if (!allowedSourceTypes.has(sourceType)) {
    return json({ error: "invalid_source_type" }, 400);
  }
  if (!allowedTemplateStyles.has(templateStyle)) {
    return json({ error: "invalid_template_style" }, 400);
  }

  let resolvedSource: ResolvedSource;
  try {
    resolvedSource = await resolveSourceInput(sourceContent, sourceUrl);
  } catch (error) {
    if (error instanceof SourceInputError) {
      return json({ error: error.code, detail: error.message }, error.status);
    }
    return json({ error: "source_fetch_failed" }, 422);
  }

  const railwayUrl = cleanBaseUrl(
    Netlify.env.get("RAILWAY_API_URL") ||
      "https://coineasy-content-engine-production.up.railway.app",
  );
  const upstreamHeaders = {
    "Content-Type": "application/json",
    "X-API-Key": apiSecret,
  };

  try {
    const generationResponse = await fetch(
      `${railwayUrl}/clients/${encodeURIComponent(clientId)}/generate/news-card`,
      {
        method: "POST",
        headers: upstreamHeaders,
        body: JSON.stringify({
          source_content: resolvedSource.content,
          source_type: sourceType,
          source_url: resolvedSource.url,
          mock_mode: body.mock_mode === true,
          template_style: templateStyle,
        }),
        signal: AbortSignal.timeout(55_000),
      },
    );

    if (!generationResponse.ok) {
      const detail = await generationResponse.text();
      return json(
        {
          error: "generation_failed",
          upstream_status: generationResponse.status,
          detail: detail.slice(0, 500),
        },
        generationResponse.status >= 500 ? 502 : generationResponse.status,
      );
    }

    const result = (await generationResponse.json()) as RailwayNewsCardResponse;
    const filePath = generatedFilePath(result.png_path, clientId);
    if (!filePath) {
      return json({ error: "invalid_generated_file_path" }, 502);
    }

    const imageResponse = await fetch(
      `${railwayUrl}/files/${filePath.split("/").map(encodeURIComponent).join("/")}`,
      {
        headers: { "X-API-Key": apiSecret },
        signal: AbortSignal.timeout(20_000),
      },
    );

    if (!imageResponse.ok) {
      return json(
        { error: "generated_image_unavailable", upstream_status: imageResponse.status },
        502,
      );
    }

    const contentType = imageResponse.headers.get("content-type") || "image/png";
    const imageBytes = Buffer.from(await imageResponse.arrayBuffer());
    const imageDataUrl = `data:${contentType};base64,${imageBytes.toString("base64")}`;

    return json({
      client_id: result.client_id,
      content_type: result.content_type,
      spec: result.spec,
      source_mode: resolvedSource.mode,
      template_style: result.template_style || templateStyle,
      duration_ms: result.duration_ms,
      image_data_url: imageDataUrl,
      filename: `${clientId}-${templateStyle}-news-card.png`,
    });
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
  path: "/api/news-card/:clientId",
};
