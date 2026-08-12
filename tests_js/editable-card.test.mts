import assert from "node:assert/strict";
import test from "node:test";

import editableCardHandler, {
  CLEANED_SOURCE_IMAGE_MAX_BYTES,
  requiredOfficialLogoVariant,
} from "../netlify/functions/editable-card.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const SQUID_GENERATED_PROFILE = {
  template_version: "squid-generated-gtm@5",
  visual_design_profile_id: "squid/full-bleed-character-type",
  visual_design_profile_version: 2,
} as const;

const TRANSLATED_SQUID_REQUEST = {
  template_style: "remix",
  source_visual_file: "squid/news_1784567890/source_visual_cleaned.jpg",
  spec: {
    source_text_visible: true,
    translation_regions: [{ text: "번역" }],
  },
};

test("does not fetch a publisher logo for generated Squid v5", () => {
  for (const family of [
    "editorial_big_type",
    "milestone_metric",
    "status_progress",
    "product_proof",
  ]) {
    assert.equal(
      requiredOfficialLogoVariant("squid", "classic", {
        render_strategy: "generated_gtm",
        creative_family: family,
        ...SQUID_GENERATED_PROFILE,
      }),
      null,
    );
  }
  // Field-absent legacy replay retains its original logo contract.
  assert.equal(requiredOfficialLogoVariant("squid", "classic", {}), "light");
});

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
  officialLogoResponse?: GeneratedImageResponse,
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
    if (url.includes("/assets/brands/") && officialLogoResponse) {
      return officialLogoResponse();
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

test("embeds the official Squid world in a classic editable card", async () => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const requested: string[] = [];
  globalThis.fetch = async input => {
    requested.push(String(input));
    return new Response(new Uint8Array([1, 2, 3]), {
      status: 200,
      headers: { "content-type": "image/png", "content-length": "3" },
    });
  };
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          return name === "STUDIO_ACCESS_TOKEN" ? "editable-studio-access-token" : undefined;
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
        body: JSON.stringify({
          template_style: "classic",
          spec: {
            label: "CANTON × SQUID",
            headline: "Canton, 아직 안 가봤나요?",
            body_lines: ["Squid로는 쉬워요"],
          },
        }),
      },
    ), {
      params: { clientId: "squid" },
      site: { url: "https://console.example" },
    } as never);

    assert.equal(response.status, 200);
    assert.deepEqual(requested.sort(), [
      "https://console.example/assets/brands/squid-form-language-purple.png",
      "https://console.example/assets/brands/squid-light.png",
      "https://console.example/assets/brands/squid-squib-bubbles.png",
      "https://console.example/assets/brands/squid-squib-token-juggle.png",
    ]);
    const svg = await response.text();
    assert.match(svg, /id="Squid-Figma-Daily-News"/);
    assert.match(svg, /id="Squid-Official-SQUIB"/);
    assert.match(svg, /data:image\/png;base64,AQID/);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
});

test("fetches the reviewed Squid asset pack without rendering publisher chrome", async () => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  const requested: string[] = [];
  globalThis.fetch = async input => {
    requested.push(String(input));
    return new Response(new Uint8Array([1, 2, 3]), {
      status: 200,
      headers: { "content-type": "image/png", "content-length": "3" },
    });
  };
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          return name === "STUDIO_ACCESS_TOKEN" ? "editable-studio-access-token" : undefined;
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
        body: JSON.stringify({
          template_style: "classic",
          spec: {
            label: "SQUID MILESTONE",
            headline: "새로운 이정표에 도달했어요",
            body_lines: ["공식 원문에서 확인한 기록이에요"],
            render_strategy: "generated_gtm",
            creative_family: "milestone_metric",
            visual_metric: "5M",
            ...SQUID_GENERATED_PROFILE,
          },
        }),
      },
    ), {
      params: { clientId: "squid" },
      site: { url: "https://console.example" },
    } as never);

    assert.equal(response.status, 200);
    assert.deepEqual(requested.sort(), [
      "https://console.example/assets/brands/squid-form-language-purple.png",
      "https://console.example/assets/brands/squid-squib-bubbles.png",
      "https://console.example/assets/brands/squid-squib-token-juggle.png",
    ]);
    const svg = await response.text();
    assert.match(svg, /id="Squid-Generated-Milestone-Metric"/);
    assert.match(svg, /id="Squid-Official-Form-Language"/);
    assert.match(svg, /id="Squid-Official-SQUIB"/);
    assert.doesNotMatch(svg, /data-logo-variant|Brand-Logo/);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
});

test("rejects stale generated Squid specs before fetching any assets", async () => {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
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
          return name === "STUDIO_ACCESS_TOKEN" ? "editable-studio-access-token" : undefined;
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
        body: JSON.stringify({
          template_style: "classic",
          spec: {
            headline: "과거 저장본",
            render_strategy: "generated_gtm",
            creative_family: "editorial_big_type",
            template_version: "squid-generated-gtm@3",
            visual_design_profile_id: "squid/figma-korea-stage",
            visual_design_profile_version: 1,
          },
        }),
      },
    ), {
      params: { clientId: "squid" },
      site: { url: "https://console.example" },
    } as never);

    assert.equal(response.status, 409);
    assert.deepEqual(await response.json(), { error: "unsupported_squid_generated_profile" });
    assert.equal(fetchCount, 0);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
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

test("fails closed when a regular remix source image request throws", async () => {
  const response = await requestRemixCard({
    template_style: "remix",
    source_image_url: "https://pbs.twimg.com/media/source.jpg",
    spec: { headline: "원본 이미지가 필요한 카드" },
  }, () => {
    throw new TypeError("network unavailable");
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

test("fails closed when a template-required official logo is unavailable", async () => {
  const response = await requestRemixCard({
    template_style: "classic",
    spec: {
      theme: "yellow",
      headline: "공식 로고가 필요한 카드",
    },
  }, undefined, () => new Response("missing", { status: 404 }));

  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "official_logo_unavailable" });
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

test("fails closed when the cleaned Squid source has expired", async () => {
  const response = await requestEditableCard(() => new Response("expired", {
    status: 404,
    headers: { "content-type": "application/json" },
  }));

  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "cleaned_source_unavailable" });
});
