export type SourceMode = "provided" | "x_oembed";

export type ResolvedSource = {
  content: string;
  url: string;
  mode: SourceMode;
};

export class SourceInputError extends Error {
  code: string;
  status: number;

  constructor(code: string, status: number, message: string) {
    super(message);
    this.name = "SourceInputError";
    this.code = code;
    this.status = status;
  }
}

export function normalizeSourceUrl(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return "";

  const candidate = /^(?:www\.)?(?:x\.com|twitter\.com)\//i.test(trimmed)
    ? `https://${trimmed}`
    : trimmed;

  try {
    const url = new URL(candidate);
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    url.hash = "";
    return url.toString();
  } catch {
    return null;
  }
}

export function canonicalXStatusUrl(value: string): string | null {
  const normalized = normalizeSourceUrl(value);
  if (!normalized) return null;

  const url = new URL(normalized);
  const hostname = url.hostname.toLowerCase().replace(/^www\./, "");
  if (hostname !== "x.com" && hostname !== "twitter.com") return null;

  const userStatusMatch = url.pathname.match(/^\/([A-Za-z0-9_]{1,15})\/status\/(\d+)/);
  if (userStatusMatch) {
    return `https://x.com/${userStatusMatch[1]}/status/${userStatusMatch[2]}`;
  }

  const webStatusMatch = url.pathname.match(/^\/i\/web\/status\/(\d+)/);
  if (webStatusMatch) {
    return `https://x.com/i/web/status/${webStatusMatch[1]}`;
  }

  return null;
}

function decodeHtmlEntities(value: string): string {
  const namedEntities: Record<string, string> = {
    amp: "&",
    apos: "'",
    gt: ">",
    lt: "<",
    nbsp: " ",
    quot: '"',
  };

  return value.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (entity, token: string) => {
    if (token.startsWith("#x") || token.startsWith("#X")) {
      return String.fromCodePoint(Number.parseInt(token.slice(2), 16));
    }
    if (token.startsWith("#")) {
      return String.fromCodePoint(Number.parseInt(token.slice(1), 10));
    }
    return namedEntities[token.toLowerCase()] ?? entity;
  });
}

export function extractXPostText(html: string): string {
  const paragraph = html.match(/<p\b[^>]*>([\s\S]*?)<\/p>/i)?.[1] ?? "";
  return decodeHtmlEntities(
    paragraph
      .replace(/<br\s*\/?\s*>/gi, "\n")
      .replace(/<[^>]+>/g, ""),
  )
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

async function fetchXPostText(
  statusUrl: string,
  fetchImpl: typeof fetch,
): Promise<string> {
  const endpoint = new URL("https://publish.x.com/oembed");
  endpoint.searchParams.set("url", statusUrl);
  endpoint.searchParams.set("omit_script", "1");
  endpoint.searchParams.set("dnt", "1");

  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      headers: { Accept: "application/json" },
      signal: AbortSignal.timeout(10_000),
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "request_failed";
    throw new SourceInputError("source_fetch_failed", 422, detail);
  }

  if (!response.ok) {
    throw new SourceInputError(
      "source_fetch_failed",
      422,
      `X oEmbed returned ${response.status}`,
    );
  }

  const payload = (await response.json()) as { html?: unknown };
  const text = typeof payload.html === "string" ? extractXPostText(payload.html) : "";
  if (text.length < 10) {
    throw new SourceInputError(
      "source_fetch_failed",
      422,
      "X post text was unavailable",
    );
  }
  return text;
}

export async function resolveSourceInput(
  sourceContent: string,
  sourceUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ResolvedSource> {
  const content = sourceContent.trim();
  const normalizedUrl = normalizeSourceUrl(sourceUrl);
  if (sourceUrl.trim() && normalizedUrl === null) {
    throw new SourceInputError("invalid_source_url", 400, "Invalid source URL");
  }

  const contentXUrl = canonicalXStatusUrl(content);
  const contentUrl = normalizeSourceUrl(content);
  const fieldXUrl = canonicalXStatusUrl(normalizedUrl || "");
  const xStatusUrl = fieldXUrl || contentXUrl;
  const resolvedUrl = xStatusUrl || normalizedUrl || "";
  const contentIsOnlyXLink = contentXUrl !== null;

  if (content.length > 20_000 && !contentIsOnlyXLink) {
    throw new SourceInputError(
      "source_content_must_be_10_to_20000_chars",
      400,
      "Source content exceeds 20,000 characters",
    );
  }

  if (contentUrl && !contentIsOnlyXLink) {
    throw new SourceInputError(
      "source_content_must_be_10_to_20000_chars",
      400,
      "Only public X status URLs can be imported without pasted source text",
    );
  }

  if (content.length >= 10 && content.length <= 20_000 && !contentIsOnlyXLink) {
    return { content, url: resolvedUrl, mode: "provided" };
  }

  if (xStatusUrl) {
    const fetchedContent = await fetchXPostText(xStatusUrl, fetchImpl);
    return { content: fetchedContent.slice(0, 20_000), url: xStatusUrl, mode: "x_oembed" };
  }

  throw new SourceInputError(
    "source_content_must_be_10_to_20000_chars",
    400,
    "Provide source text or a public X status URL",
  );
}
