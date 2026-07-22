const TUTORIAL_STORAGE_BUCKET = "content-studio";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

export type TutorialStorageConfig = {
  supabaseUrl: string;
  serviceRoleKey: string;
  workspaceId: string;
};

export type StoredTutorialSlide = {
  objectId: string;
  storagePath: string;
  byteSize: number;
};

export type TutorialCatalogSlide = {
  number: number;
  filename: string;
  storage_path: string;
  byte_size: number;
  sha256: string;
  width: number;
  height: number;
};

export type TutorialCatalogRecord = {
  contentItemId: string;
  contentVersionId: string;
  assetIds: string[];
};

export type CatalogedTutorialSlide = {
  assetId: string;
  number: number;
  filename: string;
  storagePath: string;
};

export type TutorialCatalogLookup = {
  contentItemId: string;
  contentVersionId: string;
  title: string;
  content: Record<string, unknown>;
  generationMeta: Record<string, unknown>;
  slides: CatalogedTutorialSlide[];
};

export class TutorialStorageError extends Error {
  readonly code: string;
  readonly cleanupSafe: boolean;

  constructor(code: string, cleanupSafe = true) {
    super(code);
    this.name = "TutorialStorageError";
    this.code = code;
    this.cleanupSafe = cleanupSafe;
  }
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

export function tutorialStorageConfig(
  getEnv: (name: string) => string | undefined,
): TutorialStorageConfig | null {
  const supabaseUrl = (getEnv("SUPABASE_URL") || "").trim().replace(/\/+$/, "");
  const serviceRoleKey = (getEnv("SUPABASE_SERVICE_ROLE_KEY") || "").trim();
  const workspaceId = (getEnv("CONTENT_STUDIO_WORKSPACE_ID") || "").trim().toLowerCase();
  if (
    !supabaseUrl
    || !serviceRoleKey
    || !UUID_PATTERN.test(workspaceId)
    || !isAllowedSupabaseUrl(supabaseUrl)
  ) {
    return null;
  }
  return { supabaseUrl, serviceRoleKey, workspaceId };
}

function storageHeaders(config: TutorialStorageConfig): Record<string, string> {
  return {
    apikey: config.serviceRoleKey,
    Authorization: `Bearer ${config.serviceRoleKey}`,
  };
}

function encodedStoragePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

export function tutorialStoragePath(
  workspaceId: unknown,
  clientId: unknown,
  objectId: unknown,
  fileName: unknown,
): string | null {
  if (
    typeof workspaceId !== "string"
    || typeof clientId !== "string"
    || typeof objectId !== "string"
    || typeof fileName !== "string"
    || !UUID_PATTERN.test(workspaceId)
    || !/^(yellow|squid)$/.test(clientId)
    || !UUID_PATTERN.test(objectId)
    || !/^lesson_[0-9]{2}\.png$/.test(fileName)
  ) {
    return null;
  }
  return `${workspaceId.toLowerCase()}/${clientId}/${objectId.toLowerCase()}/${fileName}`;
}

export function isPng(bytes: ArrayBuffer): boolean {
  const view = new Uint8Array(bytes);
  return PNG_SIGNATURE.every((byte, index) => view[index] === byte);
}

export function pngDimensions(bytes: ArrayBuffer): { width: number; height: number } | null {
  if (!isPng(bytes) || bytes.byteLength < 24) return null;
  const view = new Uint8Array(bytes);
  if (String.fromCharCode(...view.slice(12, 16)) !== "IHDR") return null;
  const data = new DataView(bytes);
  const width = data.getUint32(16, false);
  const height = data.getUint32(20, false);
  return width > 0 && width <= 10_000 && height > 0 && height <= 10_000
    ? { width, height }
    : null;
}

export async function verifyPrivateTutorialBucket(
  config: TutorialStorageConfig,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<void> {
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/storage/v1/bucket/${TUTORIAL_STORAGE_BUCKET}`,
      {
        headers: storageHeaders(config),
        signal,
      },
    );
  } catch {
    throw new TutorialStorageError("durable_storage_unavailable");
  }
  if (!response.ok) {
    throw new TutorialStorageError("durable_storage_unavailable");
  }
  let bucket: { id?: unknown; public?: unknown };
  try {
    bucket = await response.json() as { id?: unknown; public?: unknown };
  } catch {
    throw new TutorialStorageError("durable_storage_invalid_response");
  }
  if (bucket.id !== TUTORIAL_STORAGE_BUCKET || bucket.public !== false) {
    throw new TutorialStorageError("durable_storage_bucket_must_be_private");
  }
}

export async function verifyTutorialStorageScope(
  config: TutorialStorageConfig,
  clientId: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<void> {
  if (!/^(yellow|squid)$/.test(clientId)) {
    throw new TutorialStorageError("durable_storage_scope_not_ready");
  }
  const url = new URL(`${config.supabaseUrl}/rest/v1/workspace_clients`);
  url.searchParams.set("workspace_id", `eq.${config.workspaceId}`);
  url.searchParams.set("client_id", `eq.${clientId}`);
  url.searchParams.set("select", "workspace_id,client_id");
  url.searchParams.set("limit", "1");
  let response: Response;
  try {
    response = await fetcher(url, {
      headers: {
        ...storageHeaders(config),
        Accept: "application/json",
      },
      signal,
    });
  } catch {
    throw new TutorialStorageError("durable_storage_unavailable");
  }
  if (!response.ok) throw new TutorialStorageError("durable_storage_scope_not_ready");
  let rows: unknown;
  try {
    rows = await response.json();
  } catch {
    throw new TutorialStorageError("durable_storage_invalid_response");
  }
  const row = Array.isArray(rows) && rows.length === 1
    && typeof rows[0] === "object" && rows[0] !== null
    ? rows[0] as { workspace_id?: unknown; client_id?: unknown }
    : null;
  if (
    row?.workspace_id !== config.workspaceId
    || row?.client_id !== clientId
  ) {
    throw new TutorialStorageError("durable_storage_scope_not_ready");
  }
}

export async function uploadTutorialSlide(
  config: TutorialStorageConfig,
  clientId: string,
  objectId: string,
  fileName: string,
  bytes: ArrayBuffer,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(20_000),
): Promise<StoredTutorialSlide> {
  const storagePath = tutorialStoragePath(
    config.workspaceId,
    clientId,
    objectId,
    fileName,
  );
  if (!storagePath) throw new TutorialStorageError("invalid_durable_slide_path");
  if (!isPng(bytes)) throw new TutorialStorageError("invalid_generated_png");

  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/storage/v1/object/${TUTORIAL_STORAGE_BUCKET}/${encodedStoragePath(storagePath)}`,
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
    throw new TutorialStorageError("durable_storage_upload_failed");
  }
  if (!response.ok) {
    throw new TutorialStorageError("durable_storage_upload_failed");
  }
  return { objectId: objectId.toLowerCase(), storagePath, byteSize: bytes.byteLength };
}

export async function removeTutorialSlides(
  config: TutorialStorageConfig,
  storagePaths: string[],
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<void> {
  const safePaths = storagePaths.filter((storagePath) => {
    const parts = storagePath.split("/");
    return parts.length === 4
      && parts[0].toLowerCase() === config.workspaceId
      && tutorialStoragePath(parts[0], parts[1], parts[2], parts[3]) === storagePath;
  });
  if (!safePaths.length) return;
  try {
    await fetcher(
      `${config.supabaseUrl}/storage/v1/object/${TUTORIAL_STORAGE_BUCKET}`,
      {
        method: "DELETE",
        headers: {
          ...storageHeaders(config),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ prefixes: safePaths }),
        signal,
      },
    );
  } catch {
    // Best-effort rollback. The caller still returns the original upload error.
  }
}

export async function createTutorialSlideSignedUrl(
  config: TutorialStorageConfig,
  storagePath: string,
  expiresIn = 60,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  const pathParts = storagePath.split("/");
  if (
    pathParts.length !== 4
    || pathParts[0].toLowerCase() !== config.workspaceId
    || !tutorialStoragePath(pathParts[0], pathParts[1], pathParts[2], pathParts[3])
    || !Number.isSafeInteger(expiresIn)
    || expiresIn < 10
    || expiresIn > 300
  ) {
    throw new TutorialStorageError("invalid_durable_slide_path");
  }

  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/storage/v1/object/sign/${TUTORIAL_STORAGE_BUCKET}/${encodedStoragePath(storagePath)}`,
      {
        method: "POST",
        headers: {
          ...storageHeaders(config),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ expiresIn }),
        signal: AbortSignal.timeout(10_000),
      },
    );
  } catch {
    throw new TutorialStorageError("durable_storage_unavailable");
  }
  if (!response.ok) throw new TutorialStorageError("durable_slide_unavailable");

  let signedPath: unknown;
  try {
    const data = await response.json() as { signedURL?: unknown };
    signedPath = data.signedURL;
  } catch {
    throw new TutorialStorageError("durable_storage_invalid_response");
  }
  if (typeof signedPath !== "string" || !signedPath.startsWith("/object/sign/")) {
    throw new TutorialStorageError("durable_storage_invalid_response");
  }

  const signedUrl = new URL(`${config.supabaseUrl}/storage/v1${signedPath}`);
  const expectedOrigin = new URL(config.supabaseUrl).origin;
  if (
    signedUrl.origin !== expectedOrigin
    || !signedUrl.pathname.startsWith(`/storage/v1/object/sign/${TUTORIAL_STORAGE_BUCKET}/`)
    || !signedUrl.searchParams.get("token")
  ) {
    throw new TutorialStorageError("durable_storage_invalid_response");
  }
  return signedUrl.toString();
}

export async function recordTutorialGeneration(
  config: TutorialStorageConfig,
  input: {
    requestId: string;
    clientId: string;
    title: string;
    content: Record<string, unknown>;
    generationMeta: Record<string, unknown>;
    slides: TutorialCatalogSlide[];
    promptVersion?: string;
  },
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<TutorialCatalogRecord> {
  const contentRequestHash = input.content.request_hash;
  const generationRequestHash = input.generationMeta.request_hash;
  if (
    !UUID_PATTERN.test(input.requestId)
    || !/^(yellow|squid)$/.test(input.clientId)
    || !input.title.trim()
    || input.title.length > 200
    || !Array.isArray(input.slides)
    || input.slides.length < 1
    || input.slides.length > 12
    || typeof contentRequestHash !== "string"
    || !SHA256_PATTERN.test(contentRequestHash)
    || contentRequestHash !== generationRequestHash
  ) {
    throw new TutorialStorageError("invalid_tutorial_catalog_payload");
  }

  const requestBody = JSON.stringify({
    target_content_item_id: input.requestId.toLowerCase(),
    target_workspace_id: config.workspaceId,
    target_client_id: input.clientId,
    target_title: input.title.trim(),
    target_content: input.content,
    target_generation_meta: input.generationMeta,
    target_slides: input.slides,
    target_prompt_version: input.promptVersion || "edu-carousel@1",
  });
  let outcomeMayHaveCommitted = false;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    let response: Response;
    try {
      response = await fetcher(
        `${config.supabaseUrl}/rest/v1/rpc/record_tutorial_generation`,
        {
          method: "POST",
          headers: {
            ...storageHeaders(config),
            "Content-Type": "application/json",
          },
          body: requestBody,
          signal,
        },
      );
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
      if (response.status === 409) {
        throw new TutorialStorageError("tutorial_idempotency_conflict");
      }
      throw new TutorialStorageError("durable_catalog_write_failed");
    }

    outcomeMayHaveCommitted = true;
    let result: unknown;
    try {
      result = await response.json();
    } catch {
      continue;
    }
    const record = result && typeof result === "object"
      ? result as { content_item_id?: unknown; content_version_id?: unknown; asset_ids?: unknown }
      : null;
    if (
      record
      && record.content_item_id === input.requestId.toLowerCase()
      && typeof record.content_version_id === "string"
      && UUID_PATTERN.test(record.content_version_id)
      && Array.isArray(record.asset_ids)
      && record.asset_ids.length === input.slides.length
      && record.asset_ids.every(
        (value) => typeof value === "string" && UUID_PATTERN.test(value),
      )
    ) {
      return {
        contentItemId: record.content_item_id,
        contentVersionId: record.content_version_id,
        assetIds: record.asset_ids as string[],
      };
    }
  }
  // A timed-out or malformed success response can arrive after PostgreSQL
  // committed. Keep uploaded objects so a committed catalog never points at
  // deleted files; the request ID makes a later reconciliation retry safe.
  throw new TutorialStorageError("durable_catalog_result_unknown", false);
}

export async function findTutorialGeneration(
  config: TutorialStorageConfig,
  requestId: string,
  clientId: string,
  fetcher: typeof fetch = fetch,
  signal: AbortSignal = AbortSignal.timeout(10_000),
): Promise<TutorialCatalogLookup | null> {
  if (!UUID_PATTERN.test(requestId) || !/^(yellow|squid)$/.test(clientId)) {
    throw new TutorialStorageError("invalid_tutorial_idempotency_key");
  }
  let response: Response;
  try {
    response = await fetcher(
      `${config.supabaseUrl}/rest/v1/rpc/get_tutorial_generation`,
      {
        method: "POST",
        headers: {
          ...storageHeaders(config),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          target_content_item_id: requestId.toLowerCase(),
          target_workspace_id: config.workspaceId,
          target_client_id: clientId,
        }),
        signal,
      },
    );
  } catch {
    throw new TutorialStorageError("durable_catalog_lookup_failed");
  }
  if (!response.ok) {
    throw new TutorialStorageError("durable_catalog_lookup_failed");
  }

  let result: unknown;
  try {
    result = await response.json();
  } catch {
    throw new TutorialStorageError("durable_storage_invalid_response");
  }
  if (result === null) return null;
  const record = result && typeof result === "object"
    ? result as {
      content_item_id?: unknown;
      content_version_id?: unknown;
      title?: unknown;
      content?: unknown;
      generation_meta?: unknown;
      slides?: unknown;
    }
    : null;
  if (
    !record
    || record.content_item_id !== requestId.toLowerCase()
    || typeof record.content_version_id !== "string"
    || !UUID_PATTERN.test(record.content_version_id)
    || typeof record.title !== "string"
    || !record.title.trim()
    || !record.content
    || typeof record.content !== "object"
    || Array.isArray(record.content)
    || !record.generation_meta
    || typeof record.generation_meta !== "object"
    || Array.isArray(record.generation_meta)
    || !Array.isArray(record.slides)
    || record.slides.length < 1
    || record.slides.length > 12
  ) {
    throw new TutorialStorageError("durable_storage_invalid_response");
  }

  const slides: CatalogedTutorialSlide[] = [];
  const assetIds = new Set<string>();
  for (let index = 0; index < record.slides.length; index += 1) {
    const value = record.slides[index];
    const slide = value && typeof value === "object"
      ? value as {
        asset_id?: unknown;
        number?: unknown;
        filename?: unknown;
        storage_path?: unknown;
      }
      : null;
    const expectedNumber = index + 1;
    if (
      !slide
      || typeof slide.asset_id !== "string"
      || !UUID_PATTERN.test(slide.asset_id)
      || slide.number !== expectedNumber
      || typeof slide.filename !== "string"
      || slide.filename !== `lesson_${String(expectedNumber).padStart(2, "0")}.png`
      || typeof slide.storage_path !== "string"
      || tutorialStoragePath(
        config.workspaceId,
        clientId,
        slide.asset_id,
        slide.filename,
      ) !== slide.storage_path
      || assetIds.has(slide.asset_id)
    ) {
      throw new TutorialStorageError("durable_storage_invalid_response");
    }
    assetIds.add(slide.asset_id);
    slides.push({
      assetId: slide.asset_id,
      number: expectedNumber,
      filename: slide.filename,
      storagePath: slide.storage_path,
    });
  }

  return {
    contentItemId: record.content_item_id,
    contentVersionId: record.content_version_id,
    title: record.title,
    content: record.content as Record<string, unknown>,
    generationMeta: record.generation_meta as Record<string, unknown>,
    slides,
  };
}

export { TUTORIAL_STORAGE_BUCKET };
