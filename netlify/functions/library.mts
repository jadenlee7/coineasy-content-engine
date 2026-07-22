import type { Config } from "@netlify/functions";
import {
  contentCatalogConfig,
  ContentCatalogError,
  isContentCatalogClient,
  isContentCatalogKind,
  isContentCatalogStatus,
  listContentLibrary,
  MAX_LIBRARY_LIMIT,
  type ContentCatalogClient,
  type ContentCatalogKind,
  type ContentCatalogStatus,
} from "./_shared/content-catalog.mts";
import { requireStudioSession, studioSessionJson } from "./_shared/studio-session.mts";

function optionalFilter<T extends string>(
  value: string | null,
  valid: (candidate: unknown) => candidate is T,
): T | null | undefined {
  if (value === null || value === "") return null;
  return valid(value) ? value : undefined;
}

export default async (req: Request): Promise<Response> => {
  if (req.method !== "GET") {
    return studioSessionJson({ error: "method_not_allowed" }, 405, { Allow: "GET" });
  }

  const accessError = requireStudioSession(req);
  if (accessError) return accessError;

  const config = contentCatalogConfig((name) => Netlify.env.get(name));
  if (!config) return studioSessionJson({ error: "content_catalog_not_configured" }, 503);

  const url = new URL(req.url);
  const clientId = optionalFilter<ContentCatalogClient>(
    url.searchParams.get("client"),
    isContentCatalogClient,
  );
  const contentKind = optionalFilter<ContentCatalogKind>(
    url.searchParams.get("kind"),
    isContentCatalogKind,
  );
  const status = optionalFilter<ContentCatalogStatus>(
    url.searchParams.get("status"),
    isContentCatalogStatus,
  );
  const limitRaw = url.searchParams.get("limit");
  const limit = limitRaw === null || limitRaw === "" ? 24 : Number(limitRaw);
  const beforeCreatedAt = url.searchParams.get("before_created_at");
  const beforeId = url.searchParams.get("before_id");
  if (
    clientId === undefined
    || contentKind === undefined
    || status === undefined
    || !Number.isSafeInteger(limit)
    || limit < 1
    || limit > MAX_LIBRARY_LIMIT
  ) return studioSessionJson({ error: "invalid_library_filters" }, 400);

  try {
    const page = await listContentLibrary(config, {
      clientId,
      contentKind,
      status,
      limit,
      beforeCreatedAt,
      beforeId,
    });
    return studioSessionJson(page, 200);
  } catch (error) {
    const code = error instanceof ContentCatalogError
      ? error.code
      : "durable_library_unavailable";
    const statusCode = code === "invalid_library_filters" ? 400 : 502;
    return studioSessionJson({ error: code }, statusCode);
  }
};

export const config: Config = {
  path: "/api/library",
};
