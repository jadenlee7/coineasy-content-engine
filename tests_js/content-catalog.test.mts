import assert from "node:assert/strict";
import test from "node:test";
import {
  catalogAssetStoragePath,
  contentAssetSignedUrl,
  contentCatalogConfig,
  contentStoragePath,
  ContentCatalogError,
  downloadContentAsset,
  findGeneratedContent,
  recordGeneratedContent,
  uploadNewsCard,
  verifyContentStorageScope,
  verifyPrivateContentBucket,
  type ContentCatalogAsset,
} from "../netlify/functions/_shared/content-catalog.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const REQUEST_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const ASSET_ID = "44444444-4444-4444-8444-444444444444";
const REQUEST_HASH = "a".repeat(64);

function minimalPng(width = 1080, height = 1080): Uint8Array {
  const bytes = new Uint8Array(25);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width, false);
  view.setUint32(20, height, false);
  return bytes;
}

function config() {
  return contentCatalogConfig((name) => ({
    SUPABASE_URL: "https://project.supabase.co/",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name])!;
}

function catalogAsset(): ContentCatalogAsset {
  return {
    assetId: ASSET_ID,
    assetKind: "png",
    filename: "news-card.png",
    storagePath: `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`,
    mimeType: "image/png",
    byteSize: 25,
    sha256: "b".repeat(64),
    width: 1080,
    height: 1080,
  };
}

test("catalog config and immutable storage paths are workspace/client scoped", () => {
  assert.deepEqual(config(), {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
  });
  assert.equal(contentCatalogConfig(() => undefined), null);
  assert.equal(contentCatalogConfig((name) => ({
    SUPABASE_URL: "http://project.supabase.co",
    SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
    CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
  })[name]), null);
  for (const unsafeUrl of [
    "https://attacker.example",
    "https://project.supabase.co.attacker.example",
    "https://user:password@project.supabase.co",
    "https://project.supabase.co:8443",
    "https://project.supabase.co?redirect=https://attacker.example",
  ]) {
    assert.equal(contentCatalogConfig((name) => ({
      SUPABASE_URL: unsafeUrl,
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    })[name]), null);
  }
  assert.equal(
    contentStoragePath(WORKSPACE_ID, "squid", ASSET_ID, "news-card.png"),
    `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`,
  );
  assert.equal(contentStoragePath(WORKSPACE_ID, "unknown", ASSET_ID, "news-card.png"), null);
  assert.equal(contentStoragePath(WORKSPACE_ID, "squid", ASSET_ID, "../secret"), null);
  assert.equal(
    catalogAssetStoragePath(WORKSPACE_ID, "squid", ASSET_ID, "source-banner.svg"),
    `${WORKSPACE_ID}/squid/${ASSET_ID}/source-banner.svg`,
  );
  assert.equal(catalogAssetStoragePath(WORKSPACE_ID, "squid", ASSET_ID, "../secret.svg"), null);
  assert.equal(catalogAssetStoragePath(WORKSPACE_ID, "squid", ASSET_ID, "bad\\name.svg"), null);
  assert.equal(catalogAssetStoragePath(WORKSPACE_ID, "squid", ASSET_ID, "space name.svg"), null);
});

test("news assets validate private storage, active scope, PNG, and no-upsert upload", async () => {
  const calls: Request[] = [];
  const fetcher = async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
    const request = new Request(input, init);
    calls.push(request);
    if (request.url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (request.url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    return Response.json({ Key: "stored" });
  };
  await verifyPrivateContentBucket(config(), fetcher);
  await verifyContentStorageScope(config(), "squid", fetcher);
  const stored = await uploadNewsCard(config(), "squid", ASSET_ID, minimalPng().buffer, fetcher);
  assert.equal(stored.storagePath, `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`);
  assert.equal(stored.byteSize, 25);
  assert.equal(stored.width, 1080);
  assert.equal(stored.height, 1080);
  assert.match(stored.sha256, /^[a-f0-9]{64}$/);
  const upload = calls.at(-1)!;
  assert.equal(upload.headers.get("authorization"), "Bearer server-only-service-role");
  assert.equal(upload.headers.get("x-upsert"), "false");
  assert.equal(upload.headers.get("content-type"), "image/png");
  await assert.rejects(
    () => uploadNewsCard(config(), "squid", ASSET_ID, new Uint8Array([1, 2, 3]).buffer, fetcher),
    (error: unknown) => (error as { code?: unknown }).code === "invalid_generated_png",
  );
});

test("news card upload reuses only byte-identical deterministic storage objects", async () => {
  let calls = 0;
  const bytes = minimalPng().buffer;
  const stored = await uploadNewsCard(config(), "origintrail", ASSET_ID, bytes, async () => {
    calls += 1;
    if (calls === 1) return new Response(null, { status: 409 });
    return new Response(bytes, { status: 200 });
  });
  assert.equal(calls, 2);
  assert.equal(stored.assetId, ASSET_ID);

  calls = 0;
  await assert.rejects(
    () => uploadNewsCard(config(), "origintrail", ASSET_ID, bytes, async () => {
      calls += 1;
      if (calls === 1) return new Response(null, { status: 409 });
      return new Response(new Uint8Array([1, 2, 3]), { status: 200 });
    }),
    (error: unknown) => error instanceof ContentCatalogError
      && error.code === "content_idempotency_conflict",
  );
});

test("generated content RPC binds request hash and maps a singular durable news asset", async () => {
  const asset = catalogAsset();
  let recordBody: Record<string, any> = {};
  const recorded = await recordGeneratedContent(config(), {
    requestId: REQUEST_ID,
    clientId: "squid",
    contentKind: "daily_news",
    title: "한국어 뉴스",
    content: { request_hash: REQUEST_HASH, spec: { headline: "한국어 뉴스" } },
    channelCopy: { telegram: "공지", x: "포스트" },
    generationMeta: { request_hash: REQUEST_HASH, mock_mode: false },
    asset,
  }, async (input, init) => {
    assert.equal(String(input), "https://project.supabase.co/rest/v1/rpc/record_generated_content");
    recordBody = JSON.parse(String(init?.body));
    return Response.json({
      content_item_id: REQUEST_ID,
      content_version_id: VERSION_ID,
      asset_ids: [ASSET_ID],
    });
  });
  assert.deepEqual(recorded, {
    contentItemId: REQUEST_ID,
    contentVersionId: VERSION_ID,
    assetIds: [ASSET_ID],
  });
  assert.equal(recordBody.target_workspace_id, WORKSPACE_ID);
  assert.equal(recordBody.target_content_kind, "daily_news");
  assert.equal(recordBody.target_asset.asset_id, ASSET_ID);
  assert.equal(recordBody.target_asset.storage_path, asset.storagePath);
  assert.equal(recordBody.target_asset.asset_kind, undefined);

  let lookupBody: Record<string, unknown> = {};
  const found = await findGeneratedContent(
    config(),
    REQUEST_ID,
    "squid",
    "daily_news",
    async (_input, init) => {
      lookupBody = JSON.parse(String(init?.body));
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        client_id: "squid",
        content_kind: "daily_news",
        status: "needs_review",
        title: "한국어 뉴스",
        content: { request_hash: REQUEST_HASH, spec: { headline: "한국어 뉴스" } },
        channel_copy: { telegram: "공지", x: "포스트" },
        generation_meta: { request_hash: REQUEST_HASH, mock_mode: false },
        asset: {
          asset_id: ASSET_ID,
          filename: "news-card.png",
          storage_path: asset.storagePath,
          mime_type: "image/png",
          byte_size: 25,
          sha256: "b".repeat(64),
          width: 1080,
          height: 1080,
        },
      });
    },
  );
  assert.equal(found?.assets[0].storagePath, asset.storagePath);
  assert.equal(found?.assets[0].assetKind, "png");
  assert.deepEqual(lookupBody, {
    target_content_item_id: REQUEST_ID,
    target_workspace_id: WORKSPACE_ID,
    target_client_id: "squid",
    target_content_kind: "daily_news",
  });
});

test("ambiguous catalog writes preserve uploaded objects for idempotent reconciliation", async () => {
  let attempts = 0;
  await assert.rejects(
    () => recordGeneratedContent(config(), {
      requestId: REQUEST_ID,
      clientId: "yellow",
      contentKind: "article",
      title: "아티클",
      content: { request_hash: REQUEST_HASH },
      channelCopy: {},
      generationMeta: { request_hash: REQUEST_HASH },
    }, async () => {
      attempts += 1;
      return new Response("not-json", { status: 200 });
    }),
    (error: unknown) => {
      assert.equal(attempts, 2);
      assert.equal((error as { code?: unknown }).code, "durable_catalog_result_unknown");
      assert.equal((error as { cleanupSafe?: unknown }).cleanupSafe, false);
      return true;
    },
  );
});

test("server replay and browser previews use scoped storage access only", async () => {
  const asset = catalogAsset();
  let signedBody = "";
  const signed = await contentAssetSignedUrl(config(), "squid", asset, 300, async (_input, init) => {
    signedBody = String(init?.body);
    return Response.json({
      signedURL: `/object/sign/content-studio/${asset.storagePath}?token=short-lived`,
    });
  });
  assert.equal(JSON.parse(signedBody).expiresIn, 300);
  assert.equal(
    signed,
    `https://project.supabase.co/storage/v1/object/sign/content-studio/${asset.storagePath}?token=short-lived`,
  );
  await assert.rejects(
    () => contentAssetSignedUrl(config(), "squid", asset, 301, async () => Response.json({})),
    (error: unknown) => (error as { code?: unknown }).code === "invalid_durable_asset_path",
  );
  await assert.rejects(
    () => contentAssetSignedUrl(config(), "squid", asset, 180, async () => Response.json({
      signedURL: `/object/sign/content-studio/${WORKSPACE_ID}/squid/55555555-5555-4555-8555-555555555555/news-card.png?token=wrong-object`,
    })),
    (error: unknown) => (error as { code?: unknown }).code === "durable_storage_invalid_response",
  );

  const bytes = await downloadContentAsset(config(), asset.storagePath, async (input, init) => {
    const request = new Request(input, init);
    assert.equal(request.headers.get("authorization"), "Bearer server-only-service-role");
    return new Response(minimalPng(), { headers: { "content-length": "25" } });
  });
  assert.equal(bytes.byteLength, 25);
  await assert.rejects(
    () => downloadContentAsset(config(), `${WORKSPACE_ID}/squid/${ASSET_ID}/../secret`),
    (error: unknown) => (error as { code?: unknown }).code === "invalid_durable_asset_path",
  );
});
