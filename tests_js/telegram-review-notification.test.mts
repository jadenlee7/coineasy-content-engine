import assert from "node:assert/strict";
import test from "node:test";
import reviewNotificationHandler from "../netlify/functions/library-review-notification.mts";
import type { ContentLibraryDetail } from "../netlify/functions/_shared/content-catalog.mts";
import {
  buildTelegramReviewCaption,
  buildTelegramReviewMessage,
  sendTelegramReviewNotification,
  telegramReviewRelayConfig,
  telegramReviewUrl,
} from "../netlify/functions/_shared/telegram-review-notification.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const ITEM_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const ACCESS_TOKEN = "test-studio-access-token-32-bytes";
const API_SECRET = "test-admin-api-secret-32-bytes";
const RAILWAY_URL = "https://content-engine.example";
const CREATED_AT = "2026-07-31T09:00:00.000Z";
const PNG_BYTES = new Uint8Array([
  0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x00,
]);

function detail(): ContentLibraryDetail {
  return {
    content_item_id: ITEM_ID,
    client_id: "squid",
    content_kind: "article",
    title: "Squid 한국 GTM 아티클",
    status: "needs_review",
    current_version_id: VERSION_ID,
    created_at: CREATED_AT,
    updated_at: CREATED_AT,
    current_version: {
      content_version_id: VERSION_ID,
      version_number: 1,
      prompt_version: "article@4",
      locale: "ko-KR",
      title: "Squid 한국 GTM 아티클",
      content: {
        lead: "저장된 리드",
        source: { submitted_content: "DM에 노출하면 안 되는 긴 원문" },
      },
      channel_copy: {
        telegram: "한국 사용자가 확인할 핵심 내용입니다.\n\n#Squid #크로스체인",
        x: "X 문구",
      },
      deliverables: {},
      qa: {},
      generation_meta: { mock_mode: false },
      created_at: CREATED_AT,
    },
    assets: [],
    figma_links: [],
  };
}

function rpcDetailResult() {
  const value = detail();
  return {
    content_item_id: value.content_item_id,
    content_version_id: value.current_version_id,
    client_id: value.client_id,
    content_kind: value.content_kind,
    title: value.title,
    status: value.status,
    created_at: value.created_at,
    updated_at: value.updated_at,
    version_number: value.current_version.version_number,
    prompt_version: value.current_version.prompt_version,
    locale: value.current_version.locale,
    content: value.current_version.content,
    channel_copy: value.current_version.channel_copy,
    deliverables: value.current_version.deliverables,
    qa: value.current_version.qa,
    generation_meta: value.current_version.generation_meta,
    assets: [],
    figma_links: [],
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

test("Telegram review copy is concise, branded, and excludes raw source text", () => {
  const item = detail();
  const caption = buildTelegramReviewCaption(item);
  const message = buildTelegramReviewMessage(item);
  assert.match(caption, /검토 요청 · Squid/);
  assert.match(caption, /미승인 검토용 · 전달\/게시 금지/);
  assert.match(caption, /아티클 · 승인 전 확인/);
  assert.match(message, /검토용 Telegram 문구/);
  assert.match(message, /미승인 검토용 · 전달\/게시 금지/);
  assert.match(message, /한국 사용자가 확인할 핵심 내용/);
  assert.match(message, /이중 사실 확인 승인 전에는 전달하거나 게시하지 마세요/);
  assert.doesNotMatch(message, /DM에 노출하면 안 되는 긴 원문/);
  assert.equal(
    telegramReviewUrl("https://console.example/ignored", ITEM_ID),
    `https://console.example/?view=library&content=${ITEM_ID}`,
  );
});

test("Telegram review relay reuses the existing authenticated Railway boundary", () => {
  assert.deepEqual(telegramReviewRelayConfig((name) => ({
    RAILWAY_API_URL: RAILWAY_URL,
    API_SECRET,
  })[name]), {
    railwayUrl: RAILWAY_URL,
    apiSecret: API_SECRET,
  });
  assert.equal(telegramReviewRelayConfig((name) => ({
    RAILWAY_API_URL: "https://content-engine.example/unsafe/path",
    API_SECRET,
  })[name]), null);
});

test("Telegram relay sends the banner and authenticated review link without bot secrets", async () => {
  const calls: Request[] = [];
  const result = await sendTelegramReviewNotification(
    { railwayUrl: RAILWAY_URL, apiSecret: API_SECRET },
    detail(),
    "https://coineasy-newscard.netlify.app",
    new Blob([PNG_BYTES], { type: "image/png" }),
    async (input, init) => {
      const request = new Request(input, init);
      calls.push(request);
      return Response.json({ sent: true, photo_sent: true, text_sent: true });
    },
  );
  assert.deepEqual(result, { sent: true, photoSent: true, textSent: true });
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /\/review-notifications\/telegram$/);
  assert.equal(calls[0].headers.get("x-api-key"), API_SECRET);
  const message = await calls[0].json() as Record<string, any>;
  assert.equal(message.client_id, "squid");
  assert.match(message.caption_html, /Squid/);
  assert.match(message.image_data_url, /^data:image\/png;base64,/);
  assert.equal(message.image_url, "");
  assert.equal(
    message.review_url,
    `https://coineasy-newscard.netlify.app/?view=library&content=${ITEM_ID}`,
  );
});

test("review notification route requires Studio auth and uses the stored current version", async () => {
  const originalFetch = globalThis.fetch;
  const relayCalls: Request[] = [];
  globalThis.fetch = async (input, init) => {
    const request = new Request(input, init);
    if (request.url.endsWith("/rpc/get_content_library_item")) {
      return Response.json(rpcDetailResult());
    }
    if (request.url.endsWith("/review-notifications/telegram")) {
      relayCalls.push(request);
      return Response.json({ sent: true, photo_sent: true, text_sent: true });
    }
    throw new Error(`unexpected request ${request.url}`);
  };

  try {
    await withNetlifyEnvironment({
      STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
      SUPABASE_URL: "https://project.supabase.co",
      SUPABASE_SERVICE_ROLE_KEY: "server-only-service-role",
      CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
      RAILWAY_API_URL: RAILWAY_URL,
      API_SECRET,
    }, async () => {
      const unauthorized = await reviewNotificationHandler(
        new Request(`https://console.example/api/library/${ITEM_ID}/review-notification`, {
          method: "POST",
          body: new FormData(),
        }),
        { params: { contentId: ITEM_ID }, site: { url: "https://coineasy-newscard.netlify.app" } } as never,
      );
      assert.equal(unauthorized.status, 401);

      const form = new FormData();
      form.set("content_version_id", VERSION_ID);
      form.set("banner", new Blob([PNG_BYTES], { type: "image/png" }), "banner.png");
      const authorized = await reviewNotificationHandler(
        new Request(`https://console.example/api/library/${ITEM_ID}/review-notification`, {
          method: "POST",
          headers: {
            Cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
          },
          body: form,
        }),
        { params: { contentId: ITEM_ID }, site: { url: "https://coineasy-newscard.netlify.app" } } as never,
      );
      assert.equal(authorized.status, 200);
      const body = await authorized.json() as Record<string, unknown>;
      assert.deepEqual(body, {
        sent: true,
        photo_sent: true,
        text_sent: true,
      });
      assert.equal(relayCalls.length, 1);
      assert.equal(relayCalls[0].headers.get("x-api-key"), API_SECRET);
      assert.doesNotMatch(JSON.stringify(body), new RegExp(API_SECRET));
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
