import assert from "node:assert/strict";
import test from "node:test";
import libraryHandler from "../netlify/functions/library.mts";
import libraryItemHandler from "../netlify/functions/library-item.mts";
import {
  getContentLibraryItem,
  listContentLibrary,
} from "../netlify/functions/_shared/content-catalog.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const ITEM_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const ASSET_ID = "44444444-4444-4444-8444-444444444444";
const FIGMA_ID = "55555555-5555-4555-8555-555555555555";
const SVG_ASSET_ID = "66666666-6666-4666-8666-666666666666";
const DOCUMENT_ASSET_ID = "77777777-7777-4777-8777-777777777777";
const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const CREATED_AT = "2026-07-22T12:00:00.000Z";
const UPDATED_AT = "2026-07-22T12:01:00.000Z";

function catalogConfig() {
  return {
    supabaseUrl: "https://project.supabase.co",
    serviceRoleKey: "server-only-service-role",
    workspaceId: WORKSPACE_ID,
  };
}

function listRpcResult() {
  return {
    items: [{
      content_item_id: ITEM_ID,
      content_version_id: VERSION_ID,
      client_id: "squid",
      content_kind: "daily_news",
      title: "Squid 한국어 뉴스",
      status: "needs_review",
      created_at: CREATED_AT,
      updated_at: UPDATED_AT,
      asset_count: 1,
      primary_asset_id: ASSET_ID,
      mock_mode: false,
    }],
    next_cursor: { created_at: CREATED_AT, content_item_id: ITEM_ID },
  };
}

function detailRpcResult() {
  return {
    content_item_id: ITEM_ID,
    content_version_id: VERSION_ID,
    client_id: "squid",
    content_kind: "daily_news",
    title: "Squid 한국어 뉴스",
    status: "needs_review",
    created_at: CREATED_AT,
    updated_at: UPDATED_AT,
    version_number: 1,
    prompt_version: "content-studio@1",
    locale: "ko-KR",
    content: {
      headline: "한국어 뉴스",
      storage_path: "must-not-leak",
      nested: { api_secret: "must-not-leak", safe: true },
    },
    channel_copy: { telegram: "공지", x: "포스트" },
    deliverables: { png: true },
    qa: {},
    generation_meta: { mock_mode: false },
    assets: [{
      asset_id: ASSET_ID,
      asset_kind: "png",
      storage_bucket: "content-studio",
      storage_path: `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`,
      mime_type: "image/png",
      byte_size: 25,
      sha256: "a".repeat(64),
      width: 1080,
      height: 1080,
      metadata: {},
      created_at: CREATED_AT,
    }],
    figma_links: [{
      figma_link_id: FIGMA_ID,
      file_key: "abcdefgh1234",
      node_id: "1:2",
      page_name: "Content",
      section_name: null,
      sync_status: "linked",
      last_synced_at: null,
      metadata: {},
      updated_at: UPDATED_AT,
    }],
  };
}

async function withNetlifyEnvironment(
  values: Record<string, string | undefined>,
  run: () => Promise<void>,
): Promise<void> {
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: { env: { get: (name: string) => values[name] } },
  });
  try {
    await run();
  } finally {
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

test("library list RPC is fixed to the configured workspace and allowlisted filters", async () => {
  let requestBody: Record<string, unknown> = {};
  const page = await listContentLibrary(catalogConfig(), {
    clientId: "squid",
    contentKind: "daily_news",
    status: "needs_review",
    limit: 12,
  }, async (input, init) => {
    const request = new Request(input, init);
    assert.equal(request.url, "https://project.supabase.co/rest/v1/rpc/list_content_library");
    assert.equal(request.headers.get("authorization"), "Bearer server-only-service-role");
    requestBody = JSON.parse(String(init?.body));
    return Response.json(listRpcResult());
  });
  assert.equal(page.items[0].content_item_id, ITEM_ID);
  assert.equal(page.items[0].primary_asset_id, ASSET_ID);
  assert.deepEqual(page.next_cursor, { created_at: CREATED_AT, id: ITEM_ID });
  assert.deepEqual(requestBody, {
    target_workspace_id: WORKSPACE_ID,
    target_client_id: "squid",
    target_content_kind: "daily_news",
    target_status: "needs_review",
    target_limit: 12,
    target_before_created_at: null,
    target_before_id: null,
  });
  await assert.rejects(
    () => listContentLibrary(catalogConfig(), { clientId: "attacker" as never }),
    (error: unknown) => (error as { code?: unknown }).code === "invalid_library_filters",
  );
});

test("library detail replaces raw paths with <=5 minute signed URLs and scrubs secrets", async () => {
  const calls: string[] = [];
  const item = await getContentLibraryItem(catalogConfig(), ITEM_ID, async (input, init) => {
    const request = new Request(input, init);
    calls.push(request.url);
    if (request.url.endsWith("/rest/v1/rpc/get_content_library_item")) {
      assert.deepEqual(JSON.parse(String(init?.body)), {
        target_workspace_id: WORKSPACE_ID,
        target_content_item_id: ITEM_ID,
      });
      return Response.json(detailRpcResult());
    }
    assert.equal(JSON.parse(String(init?.body)).expiresIn, 180);
    return Response.json({
      signedURL: `/object/sign/content-studio/${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png?token=short-lived`,
    });
  });
  assert.equal(item?.assets[0].expires_in, 180);
  assert.match(item?.assets[0].url || "", /token=short-lived/);
  const serialized = JSON.stringify(item);
  assert.doesNotMatch(serialized, /storage_path/);
  assert.doesNotMatch(serialized, /must-not-leak/);
  assert.doesNotMatch(serialized, /service-role/);
  assert.equal(item?.current_version.content.nested &&
    (item.current_version.content.nested as Record<string, unknown>).safe, true);
  assert.equal(item?.figma_links[0].figma_link_id, FIGMA_ID);
  assert.equal(calls.length, 2);
});

test("library detail accepts generic SVG and document assets with nullable metadata", async () => {
  const result = detailRpcResult();
  result.assets = [{
    asset_id: SVG_ASSET_ID,
    asset_kind: "svg",
    storage_bucket: "content-studio",
    storage_path: `${WORKSPACE_ID}/squid/${SVG_ASSET_ID}/editable-banner.svg`,
    mime_type: "image/svg+xml",
    byte_size: null,
    sha256: null,
    width: null,
    height: null,
    metadata: {},
    created_at: CREATED_AT,
  }, {
    asset_id: DOCUMENT_ASSET_ID,
    asset_kind: "document",
    storage_bucket: "content-studio",
    storage_path: `${WORKSPACE_ID}/squid/${DOCUMENT_ASSET_ID}/content-brief.pdf`,
    mime_type: "application/pdf",
    byte_size: 1234,
    sha256: "c".repeat(64),
    width: null,
    height: null,
    metadata: {},
    created_at: CREATED_AT,
  }];
  result.figma_links[0].file_key = " k ";
  result.figma_links[0].node_id = " 1 ";

  const item = await getContentLibraryItem(catalogConfig(), ITEM_ID, async (input) => {
    const request = new Request(input);
    if (request.url.endsWith("/rest/v1/rpc/get_content_library_item")) {
      return Response.json(result);
    }
    const encodedPath = new URL(request.url).pathname.split("/object/sign/content-studio/")[1];
    assert.ok(encodedPath);
    return Response.json({
      signedURL: `/object/sign/content-studio/${encodedPath}?token=generic-asset`,
    });
  });

  assert.equal(item?.assets.length, 2);
  assert.deepEqual(item?.assets.map((asset) => ({
    kind: asset.asset_kind,
    mime: asset.mime_type,
    size: asset.byte_size,
    sha256: asset.sha256,
    width: asset.width,
    height: asset.height,
  })), [{
    kind: "svg",
    mime: "image/svg+xml",
    size: null,
    sha256: null,
    width: null,
    height: null,
  }, {
    kind: "document",
    mime: "application/pdf",
    size: 1234,
    sha256: "c".repeat(64),
    width: null,
    height: null,
  }]);
  assert.equal(item?.figma_links[0].file_key, "k");
  assert.equal(item?.figma_links[0].node_id, "1");
});

test("library HTTP endpoints require the HttpOnly studio session and never cache", async () => {
  const cookie = createStudioSessionValue(ACCESS_TOKEN);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/rest/v1/rpc/list_content_library")) {
      return Response.json(listRpcResult());
    }
    if (request.url.endsWith("/rest/v1/rpc/get_content_library_item")) {
      return Response.json(detailRpcResult());
    }
    if (request.url.includes("/storage/v1/object/sign/content-studio/")) {
      return Response.json({
        signedURL: `/object/sign/content-studio/${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png?token=endpoint-signed`,
      });
    }
    throw new Error(`unexpected request ${request.url}`);
  };
  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
    }, async () => {
      const unauthorized = await libraryHandler(new Request("https://console.example/api/library"));
      assert.equal(unauthorized.status, 401);
      assert.equal(unauthorized.headers.get("cache-control"), "no-store");
      assert.equal(unauthorized.headers.get("vary"), "Cookie");

      const invalid = await libraryHandler(new Request(
        "https://console.example/api/library?client=not-a-client",
        { headers: { cookie: `${STUDIO_SESSION_COOKIE}=${cookie}` } },
      ));
      assert.equal(invalid.status, 400);

      const response = await libraryHandler(new Request(
        "https://console.example/api/library?client=squid&kind=daily_news&status=needs_review",
        { headers: { cookie: `${STUDIO_SESSION_COOKIE}=${cookie}` } },
      ));
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("cache-control"), "no-store");
      assert.equal(response.headers.get("vary"), "Cookie");
      const payload = await response.json() as Record<string, any>;
      assert.equal(payload.items[0].content_item_id, ITEM_ID);

      const badItem = await libraryItemHandler(new Request(
        "https://console.example/api/library/not-a-uuid",
        { headers: { cookie: `${STUDIO_SESSION_COOKIE}=${cookie}` } },
      ), { params: { contentId: "not-a-uuid" } } as never);
      assert.equal(badItem.status, 400);
      assert.equal(badItem.headers.get("vary"), "Cookie");

      const detail = await libraryItemHandler(new Request(
        `https://console.example/api/library/${ITEM_ID}`,
        { headers: { cookie: `${STUDIO_SESSION_COOKIE}=${cookie}` } },
      ), { params: { contentId: ITEM_ID } } as never);
      assert.equal(detail.status, 200);
      assert.equal(detail.headers.get("cache-control"), "no-store");
      assert.equal(detail.headers.get("vary"), "Cookie");
      const detailPayload = await detail.json() as Record<string, any>;
      assert.match(detailPayload.assets[0].url, /token=endpoint-signed/);
      assert.doesNotMatch(JSON.stringify(detailPayload), /storage_path/);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
