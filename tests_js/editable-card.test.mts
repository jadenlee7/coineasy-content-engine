import assert from "node:assert/strict";
import test from "node:test";

import editableCardHandler, {
  CLEANED_SOURCE_IMAGE_MAX_BYTES,
} from "../netlify/functions/editable-card.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const TRANSLATED_SQUID_REQUEST = {
  template_style: "remix",
  source_visual_file: "squid/news_1784567890/source_visual_cleaned.jpg",
  spec: {
    source_text_visible: true,
    translation_regions: [{ text: "번역" }],
  },
};

type GeneratedImageResponse = () => Response;

async function requestEditableCard(generatedImageResponse: GeneratedImageResponse): Promise<Response> {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  let generatedRequestApiKey = "";

  globalThis.fetch = async (input, init) => {
    const url = String(input);
    if (url.includes("/files/squid/news_1784567890/source_visual_cleaned.jpg")) {
      generatedRequestApiKey = new Headers(init?.headers).get("X-API-Key") || "";
      return generatedImageResponse();
    }
    return new Response(new Uint8Array([1]), {
      status: 200,
      headers: { "content-type": "image/png", "content-length": "1" },
    });
  };
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          if (name === "API_SECRET") return "test-secret";
          if (name === "RAILWAY_API_URL") return "https://railway.example";
          if (name === "STUDIO_ACCESS_TOKEN") return "editable-studio-access-token";
          return undefined;
        },
      },
    },
  });

  try {
    const response = await editableCardHandler(new Request(
      "https://console.example/api/editable-card/squid",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue("editable-studio-access-token")}`,
        },
        body: JSON.stringify(TRANSLATED_SQUID_REQUEST),
      },
    ), {
      params: { clientId: "squid" },
      site: { url: "https://console.example" },
    } as never);
    assert.equal(generatedRequestApiKey, "test-secret");
    return response;
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) {
      Object.defineProperty(globalThis, "Netlify", originalNetlify);
    } else {
      Reflect.deleteProperty(globalThis, "Netlify");
    }
  }
}

async function requestRemixCard(
  body: Record<string, unknown>,
  sourceImageResponse?: GeneratedImageResponse,
): Promise<Response> {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");

  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.startsWith("https://pbs.twimg.com/")) {
      return sourceImageResponse
        ? sourceImageResponse()
        : new Response("missing", { status: 404 });
    }
    return new Response(new Uint8Array([1]), {
      status: 200,
      headers: { "content-type": "image/png", "content-length": "1" },
    });
  };
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          if (name === "STUDIO_ACCESS_TOKEN") return "editable-studio-access-token";
          return undefined;
        },
      },
    },
  });

  try {
    return await editableCardHandler(new Request(
      "https://console.example/api/editable-card/yellow",
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue("editable-studio-access-token")}`,
        },
        body: JSON.stringify(body),
      },
    ), {
      params: { clientId: "yellow" },
      site: { url: "https://console.example" },
    } as never);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) {
      Object.defineProperty(globalThis, "Netlify", originalNetlify);
    } else {
      Reflect.deleteProperty(globalThis, "Netlify");
    }
  }
}

test("keeps a base64-embedded cleaned Squid visual below Netlify's 6 MB response limit", () => {
  const netlifyResponseLimitBytes = 6_000_000;
  const base64SourceBytes = Math.ceil(CLEANED_SOURCE_IMAGE_MAX_BYTES / 3) * 4;
  const remainingSvgHeadroomBytes = netlifyResponseLimitBytes - base64SourceBytes;

  assert.equal(CLEANED_SOURCE_IMAGE_MAX_BYTES, 3_000_000);
  assert.ok(base64SourceBytes < netlifyResponseLimitBytes);
  assert.ok(remainingSvgHeadroomBytes >= 2_000_000);
});

test("rejects a cleaned Squid JPEG whose declared size exceeds the SVG-safe cap", async () => {
  const response = await requestEditableCard(() => new Response(new Uint8Array(), {
    status: 200,
    headers: {
      "content-type": "image/jpeg",
      "content-length": String(CLEANED_SOURCE_IMAGE_MAX_BYTES + 1),
    },
  }));

  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "cleaned_source_unavailable" });
});

test("fails closed when a regular remix has no durable source image", async () => {
  const response = await requestRemixCard({
    template_style: "remix",
    spec: { headline: "원본 이미지가 필요한 카드" },
  });

  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), { error: "source_image_required" });
});

test("fails closed when a regular remix source image can no longer be fetched", async () => {
  const response = await requestRemixCard({
    template_style: "remix",
    source_image_url: "https://pbs.twimg.com/media/source.jpg",
    spec: { headline: "원본 이미지가 필요한 카드" },
  });

  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "source_image_unavailable" });
});

test("embeds an available regular remix source image", async () => {
  const response = await requestRemixCard({
    template_style: "remix",
    source_image_url: "https://pbs.twimg.com/media/source.jpg",
    spec: { headline: "원본 이미지를 반영한 카드" },
  }, () => new Response(new Uint8Array([1, 2, 3]), {
    status: 200,
    headers: { "content-type": "image/jpeg", "content-length": "3" },
  }));

  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") || "", /image\/svg\+xml/);
  assert.match(await response.text(), /data:image\/jpeg;base64,AQID/);
});

test("rejects a cleaned Squid JPEG whose streamed bytes exceed the SVG-safe cap", async () => {
  const response = await requestEditableCard(() => new Response(
    new Uint8Array(CLEANED_SOURCE_IMAGE_MAX_BYTES + 1),
    {
      status: 200,
      headers: { "content-type": "image/jpeg" },
    },
  ));

  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "cleaned_source_unavailable" });
});
