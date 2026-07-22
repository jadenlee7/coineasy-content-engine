import { createHash } from "node:crypto";

export const CONTENT_STUDIO_BUCKET = "content-studio";
export const CONTENT_CATALOG_CLIENTS = ["yellow", "origintrail", "squid", "babylon"] as const;
export const CONTENT_CATALOG_KINDS = ["daily_news", "article", "tutorial"] as const;
export const CONTENT_CATALOG_STATUSES = [
  "draft",
  "queued",
  "generating",
  "needs_review",
  "approved",
  "rejected",
  "scheduled",
  "published",
  "failed",
  "archived",
] as const;

export type ContentCatalogClient = typeof CONTENT_CATALOG_CLIENTS[number];
export type ContentCatalogKind = typeof CONTENT_CATALOG_KINDS[number];
export type ContentCatalogStatus = typeof CONTENT_CATALOG_STATUSES[number];

const CLIENTS = new Set<string>(CONTENT_CATALOG_CLIENTS);
const KINDS = new Set<string>(CONTENT_CATALOG_KINDS);
const GENERATED_KINDS = new Set<string>(["daily_news", "article"]);
const STATUSES = new Set<string>(CONTENT_CATALOG_STATUSES);
const ASSET_KINDS = new Set<string>([
  "source_image",
  "png",
  "svg",
  "carousel_page",
  "document",
  "thumbnail",
]);
const ASSET_MIME_TYPES = new Set<string>([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/svg+xml",
  "application/pdf",
  "application/json",
  "text/plain",
]);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const SAFE_ASSET_FILENAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/;
const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const MAX_ASSET_BYTES = 25 * 1024 * 1024;
const MAX_CONTENT_BYTES = 1024 * 1024;
const MAX_METADATA_BYTES = 64 * 1024;
const MAX_LIBRARY_LIMIT = 50;
const SIGNED_URL_MAX_SECONDS = 300;

export type ContentCatalogConfig = {
  supabaseUrl: string;
  serviceRoleKey: string;
  workspaceId: string;
};

export type ContentCatalogAsset = {
  assetId: string;
  assetKind: string;
  filename: string;
  storagePath: string;
  mimeType: string;
  byteSize: number | null;
  sha256: string | null;
  width: number | null;
  height: number | null;
};

export type GeneratedPngAsset = ContentCatalogAsset & {
  assetKind: "png";
  mimeType: "image/png";
  byteSize: number;
  sha256: string;
  width: number;
  height: number;
};

export type UploadedNewsCard = GeneratedPngAsset & {
  bytes: ArrayBuffer;
};

export type ContentCatalogRecord = {
  contentItemId: string;
  contentVersionId: string;
  assetIds: string[];
};

export type GeneratedContentLookup = {
  contentItemId: string;
  contentVersionId: string;
  title: string;
  content: Record<string, unknown>;
  channelCopy: Record<string, unknown>;
  generationMeta: Record<string, unknown>;
  assets: ContentCatalogAsset[];
};

export type ContentCatalogLookup = GeneratedContentLookup;

export type ContentLibraryListItem = {
  content_item_id: string;
  content_version_id: string;
  client_id: ContentCatalogClient;
  content_kind: ContentCatalogKind;
  title: string;
  status: ContentCatalogStatus;
  created_at: string;
  updated_at: string;
  asset_count: number;
  primary_asset_id: string | null;
  mock_mode: boolean;
};

export type ContentLibraryCursor = {
  created_at: string;
  id: string;
};

export type ContentLibraryPage = {
  items: ContentLibraryListItem[];
  next_cursor: ContentLibraryCursor | null;
};

export type PublicCatalogAsset = {
  asset_id: string;
  asset_kind: string;
  filename: string;
  mime_type: string;
  byte_size: number | null;
  sha256: string | null;
  width: number | null;
  height: number | null;
  url: string;
  expires_in: number;
};

export type ContentLibraryDetail = {
  content_item_id: string;
  client_id: ContentCatalogClient;
  content_kind: ContentCatalogKind;
  title: string;
  status: ContentCatalogStatus;
  current_version_id: string;
  created_at: string;
  updated_at: string;
  current_version: {
    content_version_id: string;
    version_number: number;
    prompt_version: string;
    locale: string;
    title: string;
    content: Record<string, unknown>;
    channel_copy: Record<string, unknown>;
    deliverables: Record<string, unknown>;
    qa: Record<string, unknown>;
    generation_meta: Record<string, unknown>;
    created_at: string;
  };
  assets: PublicCatalogAsset[];
  figma_links: Array<{
    figma_link_id: string;
    file_key: string;
    node_id: string;
    page_name: string | null;
    section_name: string | null;
    sync_status: string;
    last_synced_at: string | null;
    metadata: Record<string, unknown>;
    updated_at: string;
  }>;
};

export class ContentCatalogError extends Error {
  readonly code: string;
  readonly cleanupSafe: boolean;

  constructor(code: string, cleanupSafe = true) {
    super(code);
    this.name = "ContentCatalogError";
    this.code = code;
    this.cleanupSafe = cleanupSafe;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isAllowedSupabaseUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
    return (url.protocol === "https:" || (url.protocol === "http:" && local))
      && (url.pathname === "/" || url.pathname === "");
  } catch {
    return false;
  }
}

function jsonByteLength(value: unknown): number {
  try {
    return Buffer.byteLength(JSON.stringify(value), "utf8");
  } catch {
    return Number.POSITIVE_INFINITY;
  }
}

function storageHeaders(config: ContentCatalogConfig): Record<string, string> {
  return {
    apikey: config.serviceRoleKey,
    Authorization: `Bearer ${config.serviceRoleKey}`,
  };
}

function encodedStoragePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

function validDate(value: unknown): value is string {
  return typeof value === "string"
    && value.length >= 20
    && value.length <= 40
    && Number.isFinite(Date.parse(value));
}

export function isContentCatalogClient(value: unknown): value is ContentCatalogClient {
  return typeof value === "string" && CLIENTS.has(value);
}

export function isContentCatalogKind(value: unknown): value is ContentCatalogKind {
  return typeof value === "string" && KINDS.has(value);
}

export function isContentCatalogStatus(value: unknown): value is ContentCatalogStatus {
  return typeof value === "string" && STATUSES.has(value);
}

export function isCatalogUuid(value: unknown): value is string {
  return typeof value === "string" && UUID_PATTERN.test(value);
}

export function contentCatalogConfig(
  getEnv: (name: string) => string | undefined,
): ContentCatalogConfig | null {
  const supabaseUrl = (getEnv("SUPABASE_URL") || "").trim().replace(/\/+$/, "");
  const serviceRoleKey = (getEnv("SUPABASE_SERVICE_ROLE_KEY") || "").trim();
  const workspaceId = (getEnv("CONTENT_STUDIO_WORKSPACE_ID") || "").trim().toLowerCase();
  if (
    !supabaseUrl
    || !serviceRoleKey
    || !UUID_PATTERN.test(workspaceId)
    || !isAllowedSupabaseUrl(supabaseUrl)
  ) return null;
  return { supabaseUrl, serviceRoleKey, workspaceId };
}

export function contentStoragePath(
  workspaceId: unknown,
  clientId: unknown,
  assetId: unknown,
  filename: unknown,
): string | null {
  if (
    typeof workspaceId !== "string"
    || !UUID_PATTERN.test(workspaceId)
    || !isContentCatalogClient(clientId)
    || typeof assetId !== "string"
    || !UUID_PATTERN.test(assetId)
    || typeof filename !== "string"
    || !/^(news-card\.png|lesson_[0-9]{2}\.png)$/.test(filename)
  ) return null;
  return `${workspaceId.toLowerCase()}/${clientId}/${assetId.toLowerCase()}/${filename}`;
}

export function catalogAssetStoragePath(
  workspaceId: unknown,
  clientId: unknown,
  assetId: unknown,
  filename: unknown,
): string | null {
  if (
    typeof workspaceId !== "string"
    || !UUID_PATTERN.test(workspaceId)
    || !isContentCatalogClient(clientId)
    || typeof assetId !== "string"
    || !UUID_PATTERN.test(assetId)
    || typeof filename !== "string"
    || !SAFE_ASSET_FILENAME_PATTERN.test(filename)
    || filename === "."
    || filename === ".."
    || filename.includes("..")
    || filename.includes("\\")
  ) return null;
  return `${workspaceId.toLowerCase()}/${clientId}/${assetId.toLowerCase()}/${filename}`;
}

export function pngDimensions(bytes: ArrayBuffer): { width: number; height: number } | null {
  if (bytes.byteLength < 24) return null;
  const view = new Uint8Array(bytes);
  if (!PNG_SIGNATURE.every((byte, index) => view[index] === byte)) return null;
  if (String.fromCharCode(...view.slice(12, 16)) !== "IHDR") return null;
  const data = new DataView(bytes);
  const width = data.getUint32(16, false);
  const height = data.getUint32(20, false);
  return width > 0 && width <= 10_000 && height > 0 && height <= 10_000
    ? { width, height }
    : null;
}

export async function verifyPrivateContentBucket(
  config: ContentCatalogConfig,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<void> {
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/storage/v1/bucket/${CONTENT_STUDIO_BUCKET}`, {
      headers: storageHeaders(config),
      signal,
    });
  } catch {
    throw new ContentCatalogError("durable_storage_unavailable");
  }
  if (!response.ok) throw new ContentCatalogError("durable_storage_unavailable");
  let bucket: unknown;
  try {
    bucket = await response.json();
  } catch {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  if (!isRecord(bucket) || bucket.id !== CONTENT_STUDIO_BUCKET || bucket.public !== false) {
    throw new ContentCatalogError("durable_storage_bucket_must_be_private");
  }
}

export async function verifyContentStorageScope(
  config: ContentCatalogConfig,
  clientId: ContentCatalogClient,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<void> {
  if (!isContentCatalogClient(clientId)) {
    throw new ContentCatalogError("durable_storage_scope_not_ready");
  }
  const url = new URL(`${config.supabaseUrl}/rest/v1/workspace_clients`);
  url.searchParams.set("workspace_id", `eq.${config.workspaceId}`);
  url.searchParams.set("client_id", `eq.${clientId}`);
  url.searchParams.set("active", "eq.true");
  url.searchParams.set("select", "workspace_id,client_id,active");
  url.searchParams.set("limit", "1");
  let response: Response;
  try {
    response = await fetcher(url, {
      headers: { ...storageHeaders(config), Accept: "application/json" },
      signal,
    });
  } catch {
    throw new ContentCatalogError("durable_storage_unavailable");
  }
  if (!response.ok) throw new ContentCatalogError("durable_storage_scope_not_ready");
  let rows: unknown;
  try {
    rows = await response.json();
  } catch {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  const row = Array.isArray(rows) && rows.length === 1 && isRecord(rows[0]) ? rows[0] : null;
  if (
    row?.workspace_id !== config.workspaceId
    || row?.client_id !== clientId
    || row?.active !== true
  ) throw new ContentCatalogError("durable_storage_scope_not_ready");
}

export async function uploadNewsCard(
  config: ContentCatalogConfig,
  clientId: ContentCatalogClient,
  assetId: string,
  bytes: ArrayBuffer,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(20_000),
): Promise<UploadedNewsCard> {
  const storagePath = contentStoragePath(config.workspaceId, clientId, assetId, "news-card.png");
  const dimensions = pngDimensions(bytes);
  if (!storagePath || !dimensions || bytes.byteLength > MAX_ASSET_BYTES) {
    throw new ContentCatalogError("invalid_generated_png");
  }
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/storage/v1/object/${CONTENT_STUDIO_BUCKET}/${encodedStoragePath(storagePath)}`,
      {
        method: "POST",
        headers: {
          ...storageHeaders(config),
          "Cache-Control": "max-age=31536000",
          "Content-Type": "image/png",
          "x-upsert": "false",
        },
        body: new Uint8Array(bytes),
        signal,
      },
    );
  } catch {
    throw new ContentCatalogError("durable_storage_upload_failed");
  }
  if (!response.ok) throw new ContentCatalogError("durable_storage_upload_failed");
  return {
    assetId: assetId.toLowerCase(),
    assetKind: "png",
    filename: "news-card.png",
    storagePath,
    mimeType: "image/png",
    byteSize: bytes.byteLength,
    sha256: createHash("sha256").update(new Uint8Array(bytes)).digest("hex"),
    width: dimensions.width,
    height: dimensions.height,
    bytes,
  };
}

function validOptionalAssetMetadata(value: {
  byteSize: unknown;
  sha256: unknown;
  width: unknown;
  height: unknown;
}): value is {
  byteSize: number | null;
  sha256: string | null;
  width: number | null;
  height: number | null;
} {
  return (value.byteSize === null || (
    typeof value.byteSize === "number"
    && Number.isSafeInteger(value.byteSize)
    && value.byteSize >= 0
    && value.byteSize <= MAX_ASSET_BYTES
  ))
    && (value.sha256 === null || (
      typeof value.sha256 === "string"
      && SHA256_PATTERN.test(value.sha256)
    ))
    && (value.width === null || (
      typeof value.width === "number"
      && Number.isSafeInteger(value.width)
      && value.width >= 1
      && value.width <= 10_000
    ))
    && (value.height === null || (
      typeof value.height === "number"
      && Number.isSafeInteger(value.height)
      && value.height >= 1
      && value.height <= 10_000
    ));
}

function validStoredLibraryAsset(
  config: ContentCatalogConfig,
  clientId: ContentCatalogClient,
  value: unknown,
): ContentCatalogAsset | null {
  if (!isRecord(value)) return null;
  const assetId = value.asset_id;
  const storagePath = value.storage_path;
  const filename = typeof value.filename === "string"
    ? value.filename
    : typeof storagePath === "string"
      ? storagePath.split("/").at(-1)
      : null;
  const assetKind = value.asset_kind;
  const mimeType = value.mime_type;
  const metadata = {
    byteSize: value.byte_size,
    sha256: value.sha256,
    width: value.width,
    height: value.height,
  };
  if (
    !isCatalogUuid(assetId)
    || typeof assetKind !== "string"
    || !ASSET_KINDS.has(assetKind)
    || typeof filename !== "string"
    || typeof storagePath !== "string"
    || catalogAssetStoragePath(config.workspaceId, clientId, assetId, filename) !== storagePath
    || typeof mimeType !== "string"
    || !ASSET_MIME_TYPES.has(mimeType)
    || value.storage_bucket !== CONTENT_STUDIO_BUCKET
    || !validOptionalAssetMetadata(metadata)
  ) return null;
  return {
    assetId: assetId.toLowerCase(),
    assetKind,
    filename,
    storagePath,
    mimeType,
    byteSize: metadata.byteSize,
    sha256: metadata.sha256,
    width: metadata.width,
    height: metadata.height,
  };
}

function validGeneratedPngAsset(
  config: ContentCatalogConfig,
  clientId: ContentCatalogClient,
  asset: ContentCatalogAsset,
): asset is GeneratedPngAsset {
  return asset.assetKind === "png"
    && asset.filename === "news-card.png"
    && asset.mimeType === "image/png"
    && contentStoragePath(config.workspaceId, clientId, asset.assetId, asset.filename)
      === asset.storagePath
    && typeof asset.byteSize === "number"
    && Number.isSafeInteger(asset.byteSize)
    && asset.byteSize >= 24
    && asset.byteSize <= MAX_ASSET_BYTES
    && typeof asset.sha256 === "string"
    && SHA256_PATTERN.test(asset.sha256)
    && typeof asset.width === "number"
    && Number.isSafeInteger(asset.width)
    && asset.width >= 1
    && asset.width <= 10_000
    && typeof asset.height === "number"
    && Number.isSafeInteger(asset.height)
    && asset.height >= 1
    && asset.height <= 10_000;
}

function validStoredGeneratedPng(
  config: ContentCatalogConfig,
  clientId: ContentCatalogClient,
  value: unknown,
): GeneratedPngAsset | null {
  if (!isRecord(value)) return null;
  const storagePath = value.storage_path;
  const filename = typeof value.filename === "string"
    ? value.filename
    : typeof storagePath === "string"
      ? storagePath.split("/").at(-1)
      : null;
  const asset: ContentCatalogAsset = {
    assetId: typeof value.asset_id === "string" ? value.asset_id.toLowerCase() : "",
    assetKind: value.asset_kind === undefined ? "png" : String(value.asset_kind),
    filename: filename ?? "",
    storagePath: typeof storagePath === "string" ? storagePath : "",
    mimeType: typeof value.mime_type === "string" ? value.mime_type : "",
    byteSize: typeof value.byte_size === "number" ? value.byte_size : null,
    sha256: typeof value.sha256 === "string" ? value.sha256 : null,
    width: typeof value.width === "number" ? value.width : null,
    height: typeof value.height === "number" ? value.height : null,
  };
  if (
    !isCatalogUuid(value.asset_id)
    || !(value.storage_bucket === undefined || value.storage_bucket === CONTENT_STUDIO_BUCKET)
    || !validGeneratedPngAsset(config, clientId, asset)
  ) return null;
  return asset;
}

function rpcAsset(asset: GeneratedPngAsset): Record<string, unknown> {
  return {
    asset_id: asset.assetId,
    filename: asset.filename,
    storage_path: asset.storagePath,
    mime_type: asset.mimeType,
    byte_size: asset.byteSize,
    sha256: asset.sha256,
    width: asset.width,
    height: asset.height,
  };
}

export async function removeContentAssets(
  config: ContentCatalogConfig,
  storagePaths: string[],
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<void> {
  const safePaths = storagePaths.filter((path) => {
    const parts = path.split("/");
    return parts.length === 4
      && parts[0].toLowerCase() === config.workspaceId
      && contentStoragePath(parts[0], parts[1], parts[2], parts[3]) === path;
  });
  if (!safePaths.length) return;
  try {
    await fetcher(`${config.supabaseUrl}/storage/v1/object/${CONTENT_STUDIO_BUCKET}`, {
      method: "DELETE",
      headers: { ...storageHeaders(config), "Content-Type": "application/json" },
      body: JSON.stringify({ prefixes: safePaths }),
      signal,
    });
  } catch {
    // Best-effort rollback; preserve the caller's original catalog error.
  }
}

export async function downloadCatalogPng(
  config: ContentCatalogConfig,
  clientId: ContentCatalogClient,
  asset: ContentCatalogAsset,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(20_000),
): Promise<ArrayBuffer> {
  if (!validGeneratedPngAsset(config, clientId, asset)) {
    throw new ContentCatalogError("invalid_durable_asset_path");
  }
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/storage/v1/object/${CONTENT_STUDIO_BUCKET}/${encodedStoragePath(asset.storagePath)}`,
      { headers: storageHeaders(config), signal },
    );
  } catch {
    throw new ContentCatalogError("durable_asset_unavailable");
  }
  if (!response.ok) throw new ContentCatalogError("durable_asset_unavailable");
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_ASSET_BYTES) {
    throw new ContentCatalogError("durable_asset_invalid");
  }
  const bytes = await response.arrayBuffer();
  const dimensions = pngDimensions(bytes);
  const hash = createHash("sha256").update(new Uint8Array(bytes)).digest("hex");
  if (
    bytes.byteLength !== asset.byteSize
    || !dimensions
    || dimensions.width !== asset.width
    || dimensions.height !== asset.height
    || hash !== asset.sha256
  ) throw new ContentCatalogError("durable_asset_invalid");
  return bytes;
}

export async function downloadContentAsset(
  config: ContentCatalogConfig,
  storagePath: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(20_000),
): Promise<ArrayBuffer> {
  const parts = storagePath.split("/");
  if (
    parts.length !== 4
    || parts[0].toLowerCase() !== config.workspaceId
    || catalogAssetStoragePath(parts[0], parts[1], parts[2], parts[3]) !== storagePath
  ) throw new ContentCatalogError("invalid_durable_asset_path");
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/storage/v1/object/${CONTENT_STUDIO_BUCKET}/${encodedStoragePath(storagePath)}`,
      { headers: storageHeaders(config), signal },
    );
  } catch {
    throw new ContentCatalogError("durable_asset_unavailable");
  }
  if (!response.ok) throw new ContentCatalogError("durable_asset_unavailable");
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > MAX_ASSET_BYTES) {
    throw new ContentCatalogError("durable_asset_invalid");
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > MAX_ASSET_BYTES) {
    throw new ContentCatalogError("durable_asset_invalid");
  }
  return bytes;
}

export async function createContentAssetSignedUrl(
  config: ContentCatalogConfig,
  clientId: ContentCatalogClient,
  asset: ContentCatalogAsset,
  expiresIn = 180,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<string> {
  if (
    !Number.isSafeInteger(expiresIn)
    || expiresIn < 10
    || expiresIn > SIGNED_URL_MAX_SECONDS
    || catalogAssetStoragePath(config.workspaceId, clientId, asset.assetId, asset.filename)
      !== asset.storagePath
  ) throw new ContentCatalogError("invalid_durable_asset_path");
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/storage/v1/object/sign/${CONTENT_STUDIO_BUCKET}/${encodedStoragePath(asset.storagePath)}`,
      {
        method: "POST",
        headers: { ...storageHeaders(config), "Content-Type": "application/json" },
        body: JSON.stringify({ expiresIn }),
        signal,
      },
    );
  } catch {
    throw new ContentCatalogError("durable_storage_unavailable");
  }
  if (!response.ok) throw new ContentCatalogError("durable_asset_unavailable");
  let signedPath: unknown;
  try {
    const value = await response.json() as { signedURL?: unknown; signedUrl?: unknown };
    signedPath = value.signedURL ?? value.signedUrl;
  } catch {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  if (typeof signedPath !== "string" || !signedPath.startsWith("/object/sign/")) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  let signedUrl: URL;
  let decodedPathname: string;
  try {
    signedUrl = new URL(`${config.supabaseUrl}/storage/v1${signedPath}`);
    decodedPathname = decodeURIComponent(signedUrl.pathname);
  } catch {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  const expectedPathname = `/storage/v1/object/sign/${CONTENT_STUDIO_BUCKET}/${asset.storagePath}`;
  if (
    signedUrl.origin !== new URL(config.supabaseUrl).origin
    || decodedPathname !== expectedPathname
    || !signedUrl.searchParams.get("token")
  ) throw new ContentCatalogError("durable_storage_invalid_response");
  return signedUrl.toString();
}

export const contentAssetSignedUrl = createContentAssetSignedUrl;

export async function recordGeneratedContent(
  config: ContentCatalogConfig,
  input: {
    requestId: string;
    clientId: ContentCatalogClient;
    contentKind: "daily_news" | "article";
    title: string;
    content: Record<string, unknown>;
    channelCopy: Record<string, unknown>;
    generationMeta: Record<string, unknown>;
    asset?: ContentCatalogAsset | null;
    promptVersion?: string;
  },
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<ContentCatalogRecord> {
  const requestHash = input.content.request_hash;
  if (
    !isCatalogUuid(input.requestId)
    || !isContentCatalogClient(input.clientId)
    || !GENERATED_KINDS.has(input.contentKind)
    || !input.title.trim()
    || input.title.trim().length > 200
    || !isRecord(input.content)
    || !isRecord(input.channelCopy)
    || !isRecord(input.generationMeta)
    || typeof requestHash !== "string"
    || !SHA256_PATTERN.test(requestHash)
    || requestHash !== input.generationMeta.request_hash
    || jsonByteLength(input.content) > MAX_CONTENT_BYTES
    || jsonByteLength(input.channelCopy) > MAX_METADATA_BYTES
    || jsonByteLength(input.generationMeta) > MAX_METADATA_BYTES
    || (input.contentKind === "daily_news") !== Boolean(input.asset)
  ) throw new ContentCatalogError("invalid_content_catalog_payload");
  const asset = input.asset ?? null;
  if (asset && !validGeneratedPngAsset(config, input.clientId, asset)) {
    throw new ContentCatalogError("invalid_content_catalog_payload");
  }

  const body = JSON.stringify({
    target_content_item_id: input.requestId.toLowerCase(),
    target_workspace_id: config.workspaceId,
    target_client_id: input.clientId,
    target_content_kind: input.contentKind,
    target_title: input.title.trim(),
    target_content: input.content,
    target_channel_copy: input.channelCopy,
    target_generation_meta: input.generationMeta,
    target_asset: asset ? rpcAsset(asset) : null,
    target_prompt_version: input.promptVersion || "content-studio@1",
  });
  let outcomeMayHaveCommitted = false;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let response: Response;
    try {
      response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/record_generated_content`, {
        method: "POST",
        headers: { ...storageHeaders(config), "Content-Type": "application/json" },
        body,
        signal,
      });
    } catch {
      outcomeMayHaveCommitted = true;
      continue;
    }
    if (!response.ok) {
      if (response.status >= 500) {
        outcomeMayHaveCommitted = true;
        continue;
      }
      if (outcomeMayHaveCommitted) continue;
      if (response.status === 409) throw new ContentCatalogError("content_idempotency_conflict");
      throw new ContentCatalogError("durable_catalog_write_failed");
    }
    outcomeMayHaveCommitted = true;
    let result: unknown;
    try {
      result = await response.json();
    } catch {
      continue;
    }
    if (!isRecord(result)) continue;
    const contentVersionId = result.content_version_id;
    const assetIds = result.asset_ids;
    const expectedAssets = input.asset ? 1 : 0;
    if (
      result.content_item_id === input.requestId.toLowerCase()
      && isCatalogUuid(contentVersionId)
      && Array.isArray(assetIds)
      && assetIds.length === expectedAssets
      && assetIds.every(isCatalogUuid)
      && (!input.asset || assetIds[0] === input.asset.assetId)
    ) return {
      contentItemId: result.content_item_id,
      contentVersionId,
      assetIds: assetIds as string[],
    };
  }
  throw new ContentCatalogError("durable_catalog_result_unknown", false);
}

export async function findGeneratedContent(
  config: ContentCatalogConfig,
  requestId: string,
  clientId: ContentCatalogClient,
  contentKind: "daily_news" | "article",
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<GeneratedContentLookup | null> {
  if (
    !isCatalogUuid(requestId)
    || !isContentCatalogClient(clientId)
    || !GENERATED_KINDS.has(contentKind)
  ) throw new ContentCatalogError("invalid_content_idempotency_key");
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/get_generated_content`, {
      method: "POST",
      headers: { ...storageHeaders(config), "Content-Type": "application/json" },
      body: JSON.stringify({
        target_content_item_id: requestId.toLowerCase(),
        target_workspace_id: config.workspaceId,
        target_client_id: clientId,
        target_content_kind: contentKind,
      }),
      signal,
    });
  } catch {
    throw new ContentCatalogError("durable_catalog_lookup_failed");
  }
  if (!response.ok) throw new ContentCatalogError("durable_catalog_lookup_failed");
  let result: unknown;
  try {
    result = await response.json();
  } catch {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  if (result === null) return null;
  if (!isRecord(result)) throw new ContentCatalogError("durable_storage_invalid_response");
  const contentVersionId = result.content_version_id;
  const title = result.title;
  const content = result.content;
  const channelCopy = result.channel_copy;
  const generationMeta = result.generation_meta;
  const rawAssets = Array.isArray(result.assets)
    ? result.assets
    : result.asset === null || result.asset === undefined
      ? []
      : [result.asset];
  if (
    result.content_item_id !== requestId.toLowerCase()
    || !isCatalogUuid(contentVersionId)
    || result.client_id !== clientId
    || result.content_kind !== contentKind
    || !isContentCatalogStatus(result.status)
    || typeof title !== "string"
    || !title.trim()
    || !isRecord(content)
    || !isRecord(channelCopy)
    || !isRecord(generationMeta)
    || typeof content.request_hash !== "string"
    || !SHA256_PATTERN.test(content.request_hash)
    || content.request_hash !== generationMeta.request_hash
    || !Array.isArray(rawAssets)
  ) throw new ContentCatalogError("durable_storage_invalid_response");
  const assets = rawAssets.map((asset) => validStoredGeneratedPng(config, clientId, asset));
  if (
    assets.some((asset) => asset === null)
    || (contentKind === "daily_news" && (assets.length !== 1 || assets[0]?.filename !== "news-card.png"))
    || (contentKind === "article" && assets.length !== 0)
  ) throw new ContentCatalogError("durable_storage_invalid_response");
  return {
    contentItemId: requestId.toLowerCase(),
    contentVersionId,
    title: title.trim(),
    content,
    channelCopy,
    generationMeta,
    assets: assets as GeneratedPngAsset[],
  };
}

function safePublicJson(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) return {};
  const denied = /^(storage_path|service_role_key|api_secret|authorization|credential_ref|raw_payload|password|secret|token|access_token|refresh_token|.*_secret|.*_token)$/i;
  const walk = (input: unknown, depth: number): unknown => {
    if (depth > 12) return null;
    if (Array.isArray(input)) return input.slice(0, 500).map((item) => walk(item, depth + 1));
    if (!isRecord(input)) return input;
    const output: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(input)) {
      if (!denied.test(key)) output[key] = walk(item, depth + 1);
    }
    return output;
  };
  return walk(value, 0) as Record<string, unknown>;
}

async function publicAsset(
  config: ContentCatalogConfig,
  clientId: ContentCatalogClient,
  asset: ContentCatalogAsset,
  expiresIn: number,
  fetcher: typeof fetch,
): Promise<PublicCatalogAsset> {
  return {
    asset_id: asset.assetId,
    asset_kind: asset.assetKind,
    filename: asset.filename,
    mime_type: asset.mimeType,
    byte_size: asset.byteSize,
    sha256: asset.sha256,
    width: asset.width,
    height: asset.height,
    url: await createContentAssetSignedUrl(config, clientId, asset, expiresIn, fetcher),
    expires_in: expiresIn,
  };
}

function listItem(value: unknown): ContentLibraryListItem | null {
  if (!isRecord(value)) return null;
  const contentItemId = value.content_item_id ?? value.id;
  const contentVersionId = value.content_version_id;
  const primaryAssetId = value.primary_asset_id;
  const assetCount = value.asset_count;
  if (
    !isCatalogUuid(contentItemId)
    || !isCatalogUuid(contentVersionId)
    || !isContentCatalogClient(value.client_id)
    || !isContentCatalogKind(value.content_kind)
    || typeof value.title !== "string"
    || !isContentCatalogStatus(value.status)
    || !(primaryAssetId === null || isCatalogUuid(primaryAssetId))
    || !validDate(value.created_at)
    || !validDate(value.updated_at)
    || !Number.isSafeInteger(assetCount)
    || Number(assetCount) < 0
    || Number(assetCount) > 12
    || typeof value.mock_mode !== "boolean"
  ) return null;
  return {
    content_item_id: contentItemId.toLowerCase(),
    content_version_id: contentVersionId.toLowerCase(),
    client_id: value.client_id,
    content_kind: value.content_kind,
    title: value.title.trim().slice(0, 200),
    status: value.status,
    created_at: value.created_at,
    updated_at: value.updated_at,
    asset_count: Number(assetCount),
    primary_asset_id: primaryAssetId ? primaryAssetId.toLowerCase() : null,
    mock_mode: value.mock_mode,
  };
}

export async function listContentLibrary(
  config: ContentCatalogConfig,
  filters: {
    clientId?: ContentCatalogClient | null;
    contentKind?: ContentCatalogKind | null;
    status?: ContentCatalogStatus | null;
    limit?: number;
    beforeCreatedAt?: string | null;
    beforeId?: string | null;
    signedUrlSeconds?: number;
  } = {},
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<ContentLibraryPage> {
  const limit = filters.limit ?? 24;
  const beforeCreatedAt = filters.beforeCreatedAt ?? null;
  const beforeId = filters.beforeId ?? null;
  if (
    (filters.clientId != null && !isContentCatalogClient(filters.clientId))
    || (filters.contentKind != null && !isContentCatalogKind(filters.contentKind))
    || (filters.status != null && !isContentCatalogStatus(filters.status))
    || !Number.isSafeInteger(limit)
    || limit < 1
    || limit > MAX_LIBRARY_LIMIT
    || ((beforeCreatedAt === null) !== (beforeId === null))
    || (beforeCreatedAt !== null && !validDate(beforeCreatedAt))
    || (beforeId !== null && !isCatalogUuid(beforeId))
  ) throw new ContentCatalogError("invalid_library_filters");
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/list_content_library`, {
      method: "POST",
      headers: { ...storageHeaders(config), "Content-Type": "application/json" },
      body: JSON.stringify({
        target_workspace_id: config.workspaceId,
        target_client_id: filters.clientId ?? null,
        target_content_kind: filters.contentKind ?? null,
        target_status: filters.status ?? null,
        target_limit: limit,
        target_before_created_at: beforeCreatedAt,
        target_before_id: beforeId,
      }),
      signal,
    });
  } catch {
    throw new ContentCatalogError("durable_library_unavailable");
  }
  if (!response.ok) throw new ContentCatalogError("durable_library_unavailable");
  let result: unknown;
  try {
    result = await response.json();
  } catch {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  if (!isRecord(result) || !Array.isArray(result.items)) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  const expiresIn = filters.signedUrlSeconds ?? 180;
  if (!Number.isSafeInteger(expiresIn) || expiresIn < 10 || expiresIn > SIGNED_URL_MAX_SECONDS) {
    throw new ContentCatalogError("invalid_library_filters");
  }
  const items: ContentLibraryListItem[] = [];
  for (const raw of result.items) {
    const parsed = listItem(raw);
    if (!parsed) throw new ContentCatalogError("durable_storage_invalid_response");
    items.push(parsed);
  }
  const rawCursor = result.next_cursor;
  let nextCursor: ContentLibraryCursor | null = null;
  if (rawCursor !== null) {
    if (
      !isRecord(rawCursor)
      || !validDate(rawCursor.created_at)
      || !isCatalogUuid(rawCursor.content_item_id ?? rawCursor.id)
    ) throw new ContentCatalogError("durable_storage_invalid_response");
    nextCursor = {
      created_at: rawCursor.created_at,
      id: String(rawCursor.content_item_id ?? rawCursor.id).toLowerCase(),
    };
  }
  return { items, next_cursor: nextCursor };
}

function validFigmaLink(value: unknown): ContentLibraryDetail["figma_links"][number] | null {
  if (!isRecord(value)) return null;
  const rawFileKey = typeof value.file_key === "string" ? value.file_key : "";
  const rawNodeId = typeof value.node_id === "string" ? value.node_id : "";
  const fileKey = rawFileKey.trim();
  const nodeId = rawNodeId.trim();
  if (
    !isCatalogUuid(value.figma_link_id ?? value.id)
    || !fileKey
    || fileKey.length > 200
    || CONTROL_CHARACTER_PATTERN.test(rawFileKey)
    || !nodeId
    || nodeId.length > 200
    || CONTROL_CHARACTER_PATTERN.test(rawNodeId)
    || !(value.page_name === null || typeof value.page_name === "string")
    || !(value.section_name === null || typeof value.section_name === "string")
    || typeof value.sync_status !== "string"
    || !new Set(["linked", "outdated", "synced", "error"]).has(value.sync_status)
    || !(value.last_synced_at === null || validDate(value.last_synced_at))
    || !isRecord(value.metadata)
    || !validDate(value.updated_at)
  ) return null;
  return {
    figma_link_id: String(value.figma_link_id ?? value.id).toLowerCase(),
    file_key: fileKey,
    node_id: nodeId,
    page_name: value.page_name as string | null,
    section_name: value.section_name as string | null,
    sync_status: value.sync_status,
    last_synced_at: value.last_synced_at as string | null,
    metadata: safePublicJson(value.metadata),
    updated_at: value.updated_at,
  };
}

export async function getContentLibraryItem(
  config: ContentCatalogConfig,
  contentItemId: string,
  fetcher: typeof fetch = fetch,
  signedUrlSeconds = 180,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<ContentLibraryDetail | null> {
  if (
    !isCatalogUuid(contentItemId)
    || !Number.isSafeInteger(signedUrlSeconds)
    || signedUrlSeconds < 10
    || signedUrlSeconds > SIGNED_URL_MAX_SECONDS
  ) throw new ContentCatalogError("invalid_library_item_id");
  let response: Response;
  try {
    response = await fetcher(`${config.supabaseUrl}/rest/v1/rpc/get_content_library_item`, {
      method: "POST",
      headers: { ...storageHeaders(config), "Content-Type": "application/json" },
      body: JSON.stringify({
        target_workspace_id: config.workspaceId,
        target_content_item_id: contentItemId.toLowerCase(),
      }),
      signal,
    });
  } catch {
    throw new ContentCatalogError("durable_library_unavailable");
  }
  if (!response.ok) throw new ContentCatalogError("durable_library_unavailable");
  let result: unknown;
  try {
    result = await response.json();
  } catch {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  if (result === null) return null;
  if (!isRecord(result)) throw new ContentCatalogError("durable_storage_invalid_response");
  const version = isRecord(result.current_version)
    ? result.current_version
    : isRecord(result.version)
      ? result.version
      : result;
  if (
    result.content_item_id !== contentItemId.toLowerCase()
    || !isContentCatalogClient(result.client_id)
    || !isContentCatalogKind(result.content_kind)
    || typeof result.title !== "string"
    || !isContentCatalogStatus(result.status)
    || !isCatalogUuid(result.content_version_id ?? result.current_version_id)
    || !validDate(result.created_at)
    || !validDate(result.updated_at)
    || !version
    || !isCatalogUuid(version.content_version_id ?? version.id ?? result.content_version_id)
    || !Number.isSafeInteger(version.version_number)
    || Number(version.version_number) < 1
    || typeof version.prompt_version !== "string"
    || typeof version.locale !== "string"
    || typeof version.title !== "string"
    || !isRecord(version.content)
    || !isRecord(version.channel_copy)
    || !isRecord(version.deliverables)
    || !isRecord(version.qa)
    || !isRecord(version.generation_meta)
    || !validDate(version.created_at)
    || !Array.isArray(result.assets)
    || !Array.isArray(result.figma_links)
  ) throw new ContentCatalogError("durable_storage_invalid_response");
  const assets: PublicCatalogAsset[] = [];
  for (const rawAsset of result.assets) {
    const asset = validStoredLibraryAsset(config, result.client_id, rawAsset);
    if (!asset) throw new ContentCatalogError("durable_storage_invalid_response");
    assets.push(await publicAsset(config, result.client_id, asset, signedUrlSeconds, fetcher));
  }
  const figmaLinks = result.figma_links.map(validFigmaLink);
  if (figmaLinks.some((link) => link === null)) {
    throw new ContentCatalogError("durable_storage_invalid_response");
  }
  return {
    content_item_id: contentItemId.toLowerCase(),
    client_id: result.client_id,
    content_kind: result.content_kind,
    title: result.title.trim().slice(0, 200),
    status: result.status,
    current_version_id: String(result.content_version_id ?? result.current_version_id).toLowerCase(),
    created_at: result.created_at,
    updated_at: result.updated_at,
    current_version: {
      content_version_id: String(
        version.content_version_id ?? version.id ?? result.content_version_id,
      ).toLowerCase(),
      version_number: Number(version.version_number),
      prompt_version: version.prompt_version,
      locale: version.locale,
      title: version.title,
      content: safePublicJson(version.content),
      channel_copy: safePublicJson(version.channel_copy),
      deliverables: safePublicJson(version.deliverables),
      qa: safePublicJson(version.qa),
      generation_meta: safePublicJson(version.generation_meta),
      created_at: version.created_at,
    },
    assets,
    figma_links: figmaLinks as ContentLibraryDetail["figma_links"],
  };
}

export { MAX_LIBRARY_LIMIT, SIGNED_URL_MAX_SECONDS };
