import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import newsCardHandler, {
  deadlineSignal as newsCardDeadlineSignal,
  MAX_NEWS_CARD_BYTES,
  NEWS_BRAND_PROFILE_POLICY_VERSION,
  NEWS_BRAND_PROFILES,
  SQUID_CREATIVE_FAMILY_POLICY_VERSION,
  SQUID_GENERATED_DESIGN_PROFILE_ID,
  SQUID_GENERATED_DESIGN_PROFILE_VERSION,
  SQUID_GENERATED_TEMPLATE_VERSION,
  SQUID_VISUAL_REFERENCE_PACK_VERSION,
  isOfficialClientXStatusUrl,
  isOfficialSquidXStatusUrl,
  newsCardRequestHash,
  normalizedFigmaTemplate,
  storedNewsTemplatePair,
  validNewsTemplatePair,
  validStandardNewsBrandMetadata,
  validSquidCreativeMetadata,
  validSquidNativeOutputSpec,
} from "../netlify/functions/news-card.mts";
import {
  createStudioSessionValue,
  STUDIO_SESSION_COOKIE,
} from "../netlify/functions/_shared/studio-session.mts";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const REQUEST_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";
const ASSET_ID = "44444444-4444-4444-8444-444444444444";
const ACCESS_TOKEN = "news-card-studio-access-token";
const AUTOMATION_TOKEN = "news-card-automation-token-that-is-long-enough";
const SOURCE_IMAGE_SHA256 = "a".repeat(64);
const SOURCE = "Squid shipped a safer cross-chain routing update for integrators.";
const SOURCE_TWEET_ID = "1234567890";
const SOURCE_URL = `https://x.com/squidrouter/status/${SOURCE_TWEET_ID}`;

function validFactCheck(contentKind = "daily_news"): Record<string, unknown> {
  return {
    schema_version: "1.0",
    policy_version: "double-fact-check@1",
    content_kind: contentKind,
    status: "review",
    human_review_required: true,
    input_sha256: "a".repeat(64),
    output_sha256: "b".repeat(64),
    checks: [
      { id: "source_evidence", status: "review", label: "Source evidence", detail: "Human verification required.", metrics: {} },
      { id: "output_claims", status: "pass", label: "Output claims", detail: "Mechanical anchors recorded.", metrics: {} },
    ],
  };
}

function squidCreativeMetadata(
  templateStyle: "classic" | "remix",
  family: "editorial_big_type" | "milestone_metric" | "status_progress" | "product_proof" | "worldbuilding" = "editorial_big_type",
): Record<string, unknown> {
  const references = {
    editorial_big_type: ["https://x.com/squidrouter/status/2079999207956500971"],
    milestone_metric: ["https://x.com/squidrouter/status/2082889008385044897"],
    status_progress: ["https://x.com/squidrouter/status/2080668216792129968"],
    product_proof: [
      "https://x.com/squidrouter/status/2079628218403803481",
      "https://x.com/squidrouter/status/2083266484789514640",
    ],
    worldbuilding: [
      "https://x.com/squidrouter/status/2083583547353501977",
      "https://x.com/squidrouter/status/2073032336384356666",
    ],
  } as const;
  return {
    creative_family: family,
    render_strategy: templateStyle === "remix" ? "source_remix" : "generated_gtm",
    creative_family_policy_version: SQUID_CREATIVE_FAMILY_POLICY_VERSION,
    visual_reference_pack_id: `squid/${family.replaceAll("_", "-")}`,
    visual_reference_pack_version: SQUID_VISUAL_REFERENCE_PACK_VERSION,
    visual_reference_status_urls: references[family],
    visual_automatic: true,
    channel_profile: templateStyle === "remix" ? "source_native" : "x_square",
    brand_tokens_version: "squid-brand-tokens@1",
    template_version: templateStyle === "remix"
      ? "squid-source-remix@1"
      : SQUID_GENERATED_TEMPLATE_VERSION,
    asset_pack_version: templateStyle === "remix"
      ? "official-source-media@1"
      : "squid-local-approved@1",
    font_status: "pretendard_fallback",
    ...(templateStyle === "classic" ? {
      visual_design_profile_id: SQUID_GENERATED_DESIGN_PROFILE_ID,
      visual_design_profile_version: SQUID_GENERATED_DESIGN_PROFILE_VERSION,
    } : {}),
  };
}

function officialSquidSyndicationPayload(
  mediaUrl?: string,
  tweetId = SOURCE_TWEET_ID,
): Record<string, unknown> {
  return {
    id_str: tweetId,
    text: "Official Squid source post with enough provenance text.",
    user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
    ...(mediaUrl ? { photos: [{ url: mediaUrl }] } : {}),
  };
}

function storedSquidRemixLookup(
  requestHash: string,
  source: Record<string, unknown>,
  png = minimalPng(),
  legacySquare = false,
): Record<string, unknown> {
  const pngView = new DataView(png.buffer, png.byteOffset, png.byteLength);
  const width = pngView.getUint32(16, false);
  const height = pngView.getUint32(20, false);
  return {
    content_item_id: REQUEST_ID,
    content_version_id: VERSION_ID,
    client_id: "squid",
    content_kind: "daily_news",
    status: "needs_review",
    title: "Squid 공식 비주얼",
    content: {
      request_hash: requestHash,
      spec: legacySquare
        ? {
          headline: "Squid 공식 비주얼",
          body_lines: ["원문 기반"],
        }
        : {
          headline: "Squid 공식 비주얼",
          body_lines: ["원문 기반"],
          output_policy: "official_source_native_v1",
          source_image_width: 1080,
          source_image_height: 1080,
          output_width: 1080,
          output_height: 1080,
        },
      source,
      render: {
        template_style: "remix",
        requested_template_style: "remix",
        source_image_used: true,
      },
    },
    channel_copy: { telegram: "텔레그램", x: "X" },
    generation_meta: {
      request_hash: requestHash,
      duration_ms: 987,
      mock_mode: false,
      fact_check: validFactCheck(),
    },
    assets: [{
      asset_id: ASSET_ID,
      asset_kind: "png",
      storage_bucket: "content-studio",
      storage_path: `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`,
      filename: "news-card.png",
      mime_type: "image/png",
      byte_size: png.byteLength,
      sha256: createHash("sha256").update(png).digest("hex"),
      width,
      height,
    }],
  };
}

function minimalPng(width = 1080, height = 1080): Uint8Array {
  const bytes = new Uint8Array(25);
  bytes.set([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  bytes.set([0x49, 0x48, 0x44, 0x52], 12);
  const view = new DataView(bytes.buffer);
  view.setUint32(16, width, false);
  view.setUint32(20, height, false);
  bytes[24] = 7;
  return bytes;
}

function requestBody(): Record<string, unknown> {
  return {
    source_content: SOURCE,
    source_type: "tweet",
    source_url: SOURCE_URL,
    mock_mode: false,
    template_style: "classic",
  };
}

function studioRequest(
  body = requestBody(),
  includeIdempotency = true,
  automation = false,
): Request {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    cookie: `${STUDIO_SESSION_COOKIE}=${createStudioSessionValue(ACCESS_TOKEN)}`,
  };
  if (includeIdempotency) headers["Idempotency-Key"] = REQUEST_ID;
  if (automation) headers["X-Studio-Automation-Key"] = AUTOMATION_TOKEN;
  return new Request("https://console.example/api/news-card/squid", {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

async function withEnvironment(
  fetcher: typeof fetch,
  run: () => Promise<void>,
): Promise<void> {
  const originalFetch = globalThis.fetch;
  const originalNetlify = Object.getOwnPropertyDescriptor(globalThis, "Netlify");
  globalThis.fetch = fetcher;
  Object.defineProperty(globalThis, "Netlify", {
    configurable: true,
    value: {
      env: {
        get(name: string): string | undefined {
          return ({
            API_SECRET: "railway-secret",
            RAILWAY_API_URL: "https://railway.example",
            STUDIO_ACCESS_TOKEN: ACCESS_TOKEN,
            STUDIO_AUTOMATION_TOKEN: AUTOMATION_TOKEN,
            SUPABASE_URL: "https://project.supabase.co",
            SUPABASE_SERVICE_ROLE_KEY: "server-only-service-key",
            CONTENT_STUDIO_WORKSPACE_ID: WORKSPACE_ID,
          } as Record<string, string>)[name];
        },
      },
    },
  });
  try {
    await run();
  } finally {
    globalThis.fetch = originalFetch;
    if (originalNetlify) Object.defineProperty(globalThis, "Netlify", originalNetlify);
    else Reflect.deleteProperty(globalThis, "Netlify");
  }
}

test("news card request hash binds every submitted generation input", () => {
  const input = {
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "classic",
  };
  assert.match(newsCardRequestHash(input), /^[a-f0-9]{64}$/);
  const policyHash = createHash("sha256").update(JSON.stringify({
    client_id: input.clientId,
    source_content: input.sourceContent,
    source_type: input.sourceType,
    source_url: input.sourceUrl,
    mock_mode: input.mockMode,
    template_style: input.templateStyle,
    style_references: [],
    style_reference_pack_hash: "",
    creative_family_policy_version: SQUID_CREATIVE_FAMILY_POLICY_VERSION,
    visual_reference_pack_version: SQUID_VISUAL_REFERENCE_PACK_VERSION,
    template_version: SQUID_GENERATED_TEMPLATE_VERSION,
    visual_design_profile_id: SQUID_GENERATED_DESIGN_PROFILE_ID,
    visual_design_profile_version: SQUID_GENERATED_DESIGN_PROFILE_VERSION,
  }), "utf8").digest("hex");
  assert.equal(newsCardRequestHash(input), policyHash);
  assert.equal(newsCardRequestHash({ ...input, sourceImageUrl: "" }), policyHash);
  assert.equal(newsCardRequestHash({ ...input }), newsCardRequestHash(input));
  assert.notEqual(newsCardRequestHash({ ...input, templateStyle: "remix" }), newsCardRequestHash(input));
  assert.notEqual(newsCardRequestHash({ ...input, sourceUrl: `${SOURCE_URL}1` }), newsCardRequestHash(input));
  assert.notEqual(
    newsCardRequestHash({ ...input, sourceImageUrl: "https://pbs.twimg.com/media/source.jpg?name=orig" }),
    newsCardRequestHash(input),
  );

  const nonSquid = { ...input, clientId: "yellow" };
  const yellowProfile = NEWS_BRAND_PROFILES.yellow;
  const brandedHash = createHash("sha256").update(JSON.stringify({
    client_id: nonSquid.clientId,
    source_content: nonSquid.sourceContent,
    source_type: nonSquid.sourceType,
    source_url: nonSquid.sourceUrl,
    mock_mode: nonSquid.mockMode,
    template_style: nonSquid.templateStyle,
    style_references: [],
    style_reference_pack_hash: "",
    brand_profile_policy_version: NEWS_BRAND_PROFILE_POLICY_VERSION,
    brand_tokens_version: yellowProfile.brandTokensVersion,
    template_version: "yellow-news-classic@2",
    asset_pack_version: yellowProfile.assetPackVersion,
    visual_design_profile_id: yellowProfile.designProfileId,
    visual_design_profile_version: yellowProfile.designProfileVersion,
  }), "utf8").digest("hex");
  assert.equal(newsCardRequestHash(nonSquid), brandedHash);
});

test("accepts only the selected client's server-owned news brand profile", () => {
  for (const clientId of ["yellow", "origintrail", "babylon"] as const) {
    const profile = NEWS_BRAND_PROFILES[clientId];
    const spec = {
      brand_profile_policy_version: NEWS_BRAND_PROFILE_POLICY_VERSION,
      render_strategy: "brand_native",
      channel_profile: "x_square",
      brand_tokens_version: profile.brandTokensVersion,
      template_version: `${clientId}-news-classic@${profile.classicTemplateVersion}`,
      asset_pack_version: profile.assetPackVersion,
      visual_design_profile_id: profile.designProfileId,
      visual_design_profile_version: profile.designProfileVersion,
    };
    assert.equal(validStandardNewsBrandMetadata(spec, clientId, "classic"), true);
    assert.equal(validStandardNewsBrandMetadata(spec, "squid", "classic"), false);
    for (const otherClientId of ["yellow", "origintrail", "babylon"] as const) {
      if (otherClientId !== clientId) {
        assert.equal(
          validStandardNewsBrandMetadata(spec, otherClientId, "classic"),
          false,
          `${clientId} metadata must not validate as ${otherClientId}`,
        );
      }
    }
  }

  for (const clientId of ["yellow", "origintrail", "babylon"] as const) {
    const profile = NEWS_BRAND_PROFILES[clientId];
    const remix = {
      brand_profile_policy_version: NEWS_BRAND_PROFILE_POLICY_VERSION,
      render_strategy: "source_remix",
      channel_profile: "x_square",
      brand_tokens_version: profile.brandTokensVersion,
      template_version: `${clientId}-news-remix@1`,
      asset_pack_version: profile.assetPackVersion,
      visual_design_profile_id: profile.designProfileId,
      visual_design_profile_version: profile.designProfileVersion,
    };
    assert.equal(validStandardNewsBrandMetadata(remix, clientId, "remix"), true);
    assert.equal(validStandardNewsBrandMetadata(remix, clientId, "classic"), false);
  }
});

test("recognizes only canonical official Squid X status URLs", () => {
  assert.equal(isOfficialSquidXStatusUrl("https://x.com/squidrouter/status/123?s=46"), true);
  assert.equal(isOfficialSquidXStatusUrl("https://twitter.com/SquidRouter/status/123"), true);
  assert.equal(isOfficialSquidXStatusUrl("https://x.com/partner/status/123"), false);
  assert.equal(isOfficialSquidXStatusUrl("https://example.com/squidrouter/status/123"), false);
});

test("recognizes the exact official Yellow account for source-dominant remix", () => {
  assert.equal(
    isOfficialClientXStatusUrl(
      "https://x.com/Yellow/status/2087177332670750834?s=20",
      "yellow",
    ),
    true,
  );
  assert.equal(
    isOfficialClientXStatusUrl(
      "https://x.com/partner/status/2087177332670750834",
      "yellow",
    ),
    false,
  );
  assert.equal(
    isOfficialClientXStatusUrl(
      "https://x.com/Yellow/status/2087177332670750834",
      "squid",
    ),
    false,
  );
});

test("recognizes only the exact OriginTrail and Babylon official status accounts", () => {
  assert.equal(
    isOfficialClientXStatusUrl(
      "https://x.com/origin_trail/status/2078063452996661578?s=20",
      "origintrail",
    ),
    true,
  );
  assert.equal(
    isOfficialClientXStatusUrl(
      "https://x.com/babylonlabs_io/status/2061801513488429361",
      "babylon",
    ),
    true,
  );
  assert.equal(
    isOfficialClientXStatusUrl(
      "https://x.com/babylonlabs_io/status/2061801513488429361",
      "origintrail",
    ),
    false,
  );
  assert.equal(
    isOfficialClientXStatusUrl(
      "https://x.com/OriginTrail/status/2078063452996661578",
      "origintrail",
    ),
    false,
  );
});

test("binds a Squid native-output spec to the exact PNG dimensions", () => {
  const spec = {
    output_policy: "official_source_native_v1",
    source_image_width: 1600,
    source_image_height: 900,
    output_width: 1200,
    output_height: 675,
  };
  assert.equal(validSquidNativeOutputSpec(spec, { width: 1200, height: 675 }), true);
  assert.equal(validSquidNativeOutputSpec(spec, { width: 1080, height: 1080 }), false);
  assert.equal(validSquidNativeOutputSpec({ ...spec, output_policy: "square" }, { width: 1200, height: 675 }), false);
});

test("accepts only a matching server-routed Squid creative family contract", () => {
  const generated = squidCreativeMetadata("classic", "milestone_metric");
  assert.equal(validSquidCreativeMetadata(generated, "classic"), true);
  assert.equal(validSquidCreativeMetadata({
    ...generated,
    visual_metric: "5m",
  }, "classic"), true);
  assert.equal(validSquidCreativeMetadata({
    ...generated,
    render_strategy: "source_remix",
  }, "classic"), false);
  assert.equal(validSquidCreativeMetadata({
    ...generated,
    visual_reference_pack_id: "squid/worldbuilding",
  }, "classic"), false);
  assert.equal(validSquidCreativeMetadata({
    ...generated,
    visual_design_profile_id: "squid/older-profile",
  }, "classic"), false);
  const generatedWithoutDesignProfile = { ...generated };
  delete generatedWithoutDesignProfile.visual_design_profile_id;
  assert.equal(
    validSquidCreativeMetadata(generatedWithoutDesignProfile, "classic"),
    false,
  );
  assert.equal(validSquidCreativeMetadata({
    ...squidCreativeMetadata("classic", "worldbuilding"),
  }, "classic"), false);

  const remix = squidCreativeMetadata("remix", "worldbuilding");
  assert.equal(validSquidCreativeMetadata(remix, "remix"), true);
  assert.equal(validSquidCreativeMetadata({
    ...remix,
    channel_profile: "x_square",
  }, "remix"), false);
  assert.equal(validSquidCreativeMetadata({
    ...remix,
    visual_design_profile_id: SQUID_GENERATED_DESIGN_PROFILE_ID,
    visual_design_profile_version: SQUID_GENERATED_DESIGN_PROFILE_VERSION,
  }, "remix"), false);
});

test("fails closed on inconsistent requested and rendered template families", () => {
  assert.equal(validNewsTemplatePair("squid", "editorial", "editorial", "classic"), true);
  assert.equal(validNewsTemplatePair("squid", "signal", "signal", "classic"), true);
  assert.equal(validNewsTemplatePair("squid", "remix", "remix", "remix"), true);
  assert.equal(validNewsTemplatePair("squid", "remix", "remix", "classic"), false);
  assert.equal(validNewsTemplatePair("yellow", "remix", "remix", "classic"), true);
  assert.equal(validNewsTemplatePair("squid", "editorial", "editorial", "editorial"), false);
  assert.equal(validNewsTemplatePair("squid", "signal", "classic", "classic"), false);
  assert.equal(validNewsTemplatePair("yellow", "editorial", "editorial", "classic"), false);
});

test("stored news card replay requires both explicit compatible template styles", () => {
  assert.deepEqual(
    storedNewsTemplatePair("squid", {
      requested_template_style: "classic",
      template_style: "classic",
    }),
    { requestedTemplateStyle: "classic", actualTemplateStyle: "classic" },
  );
  assert.equal(storedNewsTemplatePair("squid", { template_style: "classic" }), null);
  assert.equal(storedNewsTemplatePair("squid", { requested_template_style: "classic" }), null);
  assert.equal(
    storedNewsTemplatePair("squid", {
      requested_template_style: "remix",
      template_style: "classic",
    }),
    null,
  );
});

test("accepts only the explicitly approved Figma template for the rendered client", () => {
  const approved = {
    registry_schema_version: "1.0",
    file_key: "hsRSASQjEMxl5NMLH9y5Wm",
    file_name: "CoinEasy Management",
    page_name: "Daily content",
    node_id: "1479:1954",
    frame_name: "[KEEP] Banner_Squid_Sample",
    status: "approved",
    version: "2026-07-30.1",
  };
  assert.deepEqual(
    normalizedFigmaTemplate(approved, "squid", "classic"),
    approved,
  );
  assert.equal(normalizedFigmaTemplate(approved, "yellow", "classic"), null);
  assert.equal(normalizedFigmaTemplate(approved, "squid", "remix"), null);
  assert.equal(
    normalizedFigmaTemplate(
      { ...approved, node_id: "1835:1877" },
      "squid",
      "classic",
    ),
    null,
  );
  const approvedYellow = {
    ...approved,
    node_id: "1966:2389",
    frame_name: "[KEEP] Banner_Yellow_Sample",
    version: "2026-08-13.1",
  };
  assert.deepEqual(
    normalizedFigmaTemplate(approvedYellow, "yellow", "classic"),
    approvedYellow,
  );
  assert.equal(
    normalizedFigmaTemplate(
      { ...approvedYellow, version: "2026-08-13.2" },
      "yellow",
      "classic",
    ),
    null,
  );
});

test("news card Railway deadline preserves the persistence reserve", () => {
  const now = Date.now();
  assert.throws(
    () => newsCardDeadlineSignal(now + 18_000, 38_000, 18_000),
    (error: unknown) => error instanceof Error && error.message === "news_card_deadline_exceeded",
  );
  assert.equal(
    newsCardDeadlineSignal(now + 18_500, 38_000, 18_000).aborted,
    false,
  );

  const source = readFileSync(
    new URL("../netlify/functions/news-card.mts", import.meta.url),
    "utf8",
  );
  assert.match(source, /const NEWS_CARD_PERSISTENCE_RESERVE_MS = 18_000;/);
  assert.match(
    source,
    /signal:\s*deadlineSignal\(\s*requestDeadline,\s*RAILWAY_GENERATION_BUDGET_MS,\s*NEWS_CARD_PERSISTENCE_RESERVE_MS,\s*\)/,
  );
});

test("news card generation persists one immutable PNG before returning", async () => {
  const png = minimalPng();
  let railwayCalls = 0;
  let recordBody: Record<string, unknown> = {};
  let uploadUpsert = "";

  await withEnvironment(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/clients/squid/generate/news-card")) {
      railwayCalls += 1;
      return Response.json({
        client_id: "squid",
        content_type: "news_card",
        spec: {
          ...squidCreativeMetadata("classic"),
          label: "업데이트",
          headline: "Squid 라우팅 업데이트",
          body_lines: ["원문에 근거한 업데이트"],
          source_url: SOURCE_URL,
        },
        png_path: "/app/output/squid/news_123/news_card_classic.png",
        template_style: "classic",
        requested_template_style: "classic",
        source_image_used: false,
        source_visual_path: null,
        figma_template: null,
        manifest_path: "/app/output/squid/news_123/manifest.json",
        duration_ms: 1234,
      });
    }
    if (url.endsWith("/files/squid/news_123/news_card_classic.png")) {
      return new Response(png, { headers: { "content-type": "image/png" } });
    }
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") {
      uploadUpsert = new Headers(init.headers).get("x-upsert") || "";
      return Response.json({ Key: "stored" });
    }
    if (url.endsWith("/rest/v1/rpc/record_generated_content")) {
      recordBody = JSON.parse(String(init?.body));
      const asset = recordBody.target_asset as { asset_id?: string };
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        asset_ids: [asset.asset_id],
      });
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest(), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(result.content_item_id, REQUEST_ID);
    assert.equal(result.content_version_id, VERSION_ID);
    assert.equal(result.asset_ids.length, 1);
    assert.equal(result.storage_backend, "supabase");
    assert.equal(result.reused, false);
    assert.equal(result.brand_qa.policy_version, "brand-qa@1");
    assert.equal(result.brand_qa.client_id, "squid");
    assert.equal(result.brand_qa.content_kind, "daily_news");
    assert.equal(result.fact_check.policy_version, "double-fact-check@1");
    assert.equal(result.fact_check.human_review_required, true);
    assert.equal(result.fact_check.checks[1].metrics.artifact_count, 1);
    assert.match(result.image_data_url, /^data:image\/png;base64,/);
    assert.equal(railwayCalls, 1);
    assert.equal(uploadUpsert, "false");
    assert.equal(recordBody.target_content_kind, "daily_news");
    assert.equal(recordBody.target_prompt_version, "news-card@3");
    assert.equal((recordBody.target_generation_meta as Record<string, unknown>).mock_mode, false);
    assert.deepEqual(
      (recordBody.target_generation_meta as Record<string, unknown>).brand_qa,
      result.brand_qa,
    );
    assert.deepEqual(
      (recordBody.target_generation_meta as Record<string, unknown>).fact_check,
      result.fact_check,
    );
    assert.equal(
      (recordBody.target_generation_meta as Record<string, unknown>).figma_template_version,
      null,
    );
    assert.equal(
      (recordBody.target_generation_meta as Record<string, unknown>).creative_family,
      "editorial_big_type",
    );
    assert.deepEqual(
      (recordBody.target_content as Record<string, any>).source,
      {
        submitted_content: SOURCE,
        resolved_content: SOURCE,
        type: "tweet",
        url: SOURCE_URL,
        mode: "provided",
        image_url: "",
        media_status: "not_requested",
      },
    );
    assert.deepEqual(
      (recordBody.target_content as Record<string, any>).render,
      {
        requested_template_style: "classic",
        template_style: "classic",
        source_image_used: false,
        source_visual_file: null,
        figma_template: null,
        creative_family: "editorial_big_type",
        render_strategy: "generated_gtm",
        creative_family_policy_version: SQUID_CREATIVE_FAMILY_POLICY_VERSION,
        visual_reference_pack_id: "squid/editorial-big-type",
        visual_reference_pack_version: SQUID_VISUAL_REFERENCE_PACK_VERSION,
        channel_profile: "x_square",
        brand_tokens_version: "squid-brand-tokens@1",
        template_version: SQUID_GENERATED_TEMPLATE_VERSION,
        asset_pack_version: "squid-local-approved@1",
        font_status: "pretendard_fallback",
        visual_design_profile_id: SQUID_GENERATED_DESIGN_PROFILE_ID,
        visual_design_profile_version: SQUID_GENERATED_DESIGN_PROFILE_VERSION,
      },
    );
  });
});

test("automation pins the exact official Squid X image through generation and storage", async () => {
  const png = minimalPng(1200, 675);
  const submittedImage = "https://pbs.twimg.com/media/official-source.jpg?format=jpg&name=small";
  const pinnedImage = "https://pbs.twimg.com/media/official-source.jpg?format=jpg&name=orig";
  let upstreamBody: Record<string, unknown> = {};
  let recordBody: Record<string, any> = {};
  let syndicationCalls = 0;

  await withEnvironment(async (input, init) => {
    const url = String(input);
    if (url.includes("cdn.syndication.twimg.com")) {
      syndicationCalls += 1;
      return Response.json(officialSquidSyndicationPayload(submittedImage));
    }
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/clients/squid/generate/news-card")) {
      upstreamBody = JSON.parse(String(init?.body));
      return Response.json({
        client_id: "squid",
        content_type: "news_card",
        spec: {
          ...squidCreativeMetadata("remix"),
          headline: "Squid 공식 비주얼",
          body_lines: ["원문 배너를 그대로 반영해요"],
          source_url: SOURCE_URL,
          source_text_visible: false,
          translation_regions: [],
          visual_localization_status: "no_source_text",
          output_policy: "official_source_native_v1",
          source_image_width: 1600,
          source_image_height: 900,
          output_width: 1200,
          output_height: 675,
        },
        png_path: "/app/output/squid/news_456/news_card_remix.png",
        template_style: "remix",
        requested_template_style: "remix",
        source_image_used: true,
        source_image_url: pinnedImage,
        source_image_sha256: SOURCE_IMAGE_SHA256,
        source_visual_path: null,
        figma_template: null,
        manifest_path: "/app/output/squid/news_456/manifest.json",
        duration_ms: 987,
      });
    }
    if (url.endsWith("/files/squid/news_456/news_card_remix.png")) {
      return new Response(png, { headers: { "content-type": "image/png" } });
    }
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") {
      return Response.json({ Key: "stored" });
    }
    if (url.endsWith("/rest/v1/rpc/record_generated_content")) {
      recordBody = JSON.parse(String(init?.body));
      const asset = recordBody.target_asset as { asset_id?: string };
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        asset_ids: [asset.asset_id],
      });
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
      source_image_url: submittedImage,
    }, true, true), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(syndicationCalls, 1);
    assert.equal(upstreamBody.source_image_url, pinnedImage);
    assert.equal(upstreamBody.template_style, "remix");
    assert.equal(result.source_image_url, pinnedImage);
    assert.equal(result.source_image_sha256, SOURCE_IMAGE_SHA256);
    assert.equal(result.source_media_status, "present");
    assert.equal(result.source_image_used, true);
    assert.equal(result.output_width, 1200);
    assert.equal(result.output_height, 675);
    assert.equal(recordBody.target_asset.width, 1200);
    assert.equal(recordBody.target_asset.height, 675);
    assert.equal(recordBody.target_content.source.image_url, pinnedImage);
    assert.equal(recordBody.target_content.source.media_status, "present");
    assert.equal(recordBody.target_content.source.prepared_sha256, SOURCE_IMAGE_SHA256);
    const expectedHash = newsCardRequestHash({
      clientId: "squid",
      sourceContent: SOURCE,
      sourceType: "tweet",
      sourceUrl: SOURCE_URL,
      mockMode: false,
      templateStyle: "remix",
      sourceImageUrl: pinnedImage,
    });
    assert.equal(recordBody.target_content.request_hash, expectedHash);
    assert.equal(recordBody.target_generation_meta.request_hash, expectedHash);
  });
});

test("automation pins an official Yellow creative and keeps it in the source-dominant remix", async () => {
  const yellowSourceUrl = "https://x.com/Yellow/status/2087177332670750834";
  const submittedImage = "https://pbs.twimg.com/media/HPckuPQXAAAj0cx.jpg";
  const pinnedImage = `${submittedImage}?name=orig`;
  const yellowSource = (
    "Yellow and Deep3Labs describe an AI-agent partnership in which Yellow's "
    + "clearing layer addresses source-stated cost and latency constraints."
  );
  const profile = NEWS_BRAND_PROFILES.yellow;
  let upstreamBody: Record<string, unknown> = {};
  let recordBody: Record<string, any> = {};

  await withEnvironment(async (input, init) => {
    const url = String(input);
    if (url.includes("cdn.syndication.twimg.com")) {
      return Response.json({
        id_str: "2087177332670750834",
        text: "Official Yellow partnership source post with verified media.",
        user: { id_str: "2651", screen_name: "Yellow" },
        photos: [{ url: submittedImage }],
      });
    }
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "yellow", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/clients/yellow/generate/news-card")) {
      upstreamBody = JSON.parse(String(init?.body));
      return Response.json({
        client_id: "yellow",
        content_type: "news_card",
        spec: {
          brand_profile_policy_version: NEWS_BRAND_PROFILE_POLICY_VERSION,
          render_strategy: "source_remix",
          channel_profile: "x_square",
          brand_tokens_version: profile.brandTokensVersion,
          template_version: "yellow-news-remix@1",
          asset_pack_version: profile.assetPackVersion,
          visual_design_profile_id: profile.designProfileId,
          visual_design_profile_version: profile.designProfileVersion,
          label: "파트너십",
          date: "2026.08.13",
          headline: "AI 에이전트의 실행 제약을 낮추는 인프라",
          body_lines: ["원문이 설명한 비용과 지연 제약에 집중합니다"],
          source_url: yellowSourceUrl,
          source_logo_visible: true,
          theme: "dark",
        },
        png_path: "/app/output/yellow/news_456/news_card_remix.png",
        template_style: "remix",
        requested_template_style: "remix",
        source_image_used: true,
        source_image_url: pinnedImage,
        source_image_sha256: SOURCE_IMAGE_SHA256,
        source_visual_path: null,
        figma_template: null,
        manifest_path: "/app/output/yellow/news_456/manifest.json",
        duration_ms: 987,
      });
    }
    if (url.endsWith("/files/yellow/news_456/news_card_remix.png")) {
      return new Response(minimalPng(), { headers: { "content-type": "image/png" } });
    }
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") {
      return Response.json({ Key: "stored" });
    }
    if (url.endsWith("/rest/v1/rpc/record_generated_content")) {
      recordBody = JSON.parse(String(init?.body));
      const asset = recordBody.target_asset as { asset_id?: string };
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        asset_ids: [asset.asset_id],
      });
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      source_content: yellowSource,
      source_type: "tweet",
      source_url: yellowSourceUrl,
      mock_mode: false,
      template_style: "remix",
      source_image_url: submittedImage,
    }, true, true), {
      params: { clientId: "yellow" },
    } as never);

    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(upstreamBody.source_image_url, pinnedImage);
    assert.equal(upstreamBody.template_style, "remix");
    assert.equal(result.source_image_url, pinnedImage);
    assert.equal(result.source_image_used, true);
    assert.equal(result.template_style, "remix");
    assert.equal(recordBody.target_content.source.image_url, pinnedImage);
    assert.equal(recordBody.target_content.render.template_style, "remix");
  });
});

test("rejects a stale square Railway PNG for a source-native Squid remix", async () => {
  const submittedImage = "https://pbs.twimg.com/media/official-source.jpg?name=orig";
  let uploads = 0;
  await withEnvironment(async (input, init) => {
    const url = String(input);
    if (url.includes("cdn.syndication.twimg.com")) {
      return Response.json(officialSquidSyndicationPayload(submittedImage));
    }
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/clients/squid/generate/news-card")) {
      return Response.json({
        client_id: "squid",
        content_type: "news_card",
        spec: {
          ...squidCreativeMetadata("remix"),
          headline: "Squid 공식 비주얼",
          body_lines: ["원문 기반"],
          source_text_visible: false,
          translation_regions: [],
          output_policy: "official_source_native_v1",
          source_image_width: 1600,
          source_image_height: 900,
          output_width: 1200,
          output_height: 675,
        },
        png_path: "/app/output/squid/news_457/news_card_remix.png",
        template_style: "remix",
        requested_template_style: "remix",
        source_image_used: true,
        source_image_url: submittedImage,
        source_image_sha256: SOURCE_IMAGE_SHA256,
        source_visual_path: null,
        figma_template: null,
        manifest_path: "/app/output/squid/news_457/manifest.json",
        duration_ms: 987,
      });
    }
    if (url.endsWith("/files/squid/news_457/news_card_remix.png")) {
      return new Response(minimalPng(1080, 1080), { headers: { "content-type": "image/png" } });
    }
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") {
      uploads += 1;
      return Response.json({ Key: "unexpected" });
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
      source_image_url: submittedImage,
    }, true, true), { params: { clientId: "squid" } } as never);
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "invalid_generation_response" });
    assert.equal(uploads, 0);
  });
});

test("Squid remix rejects Railway responses that do not prove the pinned source", async () => {
  const pinnedImage = "https://pbs.twimg.com/media/official-source.jpg?name=orig";
  const invalidProofs = [
    { source_image_used: false, source_image_url: pinnedImage, source_image_sha256: SOURCE_IMAGE_SHA256 },
    { source_image_used: true, source_image_url: "https://pbs.twimg.com/media/other.jpg?name=orig", source_image_sha256: SOURCE_IMAGE_SHA256 },
    { source_image_used: true, source_image_url: pinnedImage, source_image_sha256: "invalid" },
  ];

  for (const proof of invalidProofs) {
    let downstreamFetches = 0;
    await withEnvironment(async (input) => {
      const url = String(input);
      if (url.includes("cdn.syndication.twimg.com")) {
        return Response.json(officialSquidSyndicationPayload(pinnedImage));
      }
      if (url.endsWith("/storage/v1/bucket/content-studio")) {
        return Response.json({ id: "content-studio", public: false });
      }
      if (url.includes("/rest/v1/workspace_clients")) {
        return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
      }
      if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
      if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
      if (url.endsWith("/clients/squid/generate/news-card")) {
        return Response.json({
          client_id: "squid",
          content_type: "news_card",
          spec: {
            ...squidCreativeMetadata("remix"),
            headline: "Squid 공식 비주얼",
            source_text_visible: false,
          },
          png_path: "/app/output/squid/news_456/news_card_remix.png",
          template_style: "remix",
          requested_template_style: "remix",
          source_visual_path: null,
          manifest_path: "/app/output/squid/news_456/manifest.json",
          duration_ms: 987,
          ...proof,
        });
      }
      downstreamFetches += 1;
      throw new Error(`unexpected downstream fetch ${url}`);
    }, async () => {
      const response = await newsCardHandler(studioRequest({
        ...requestBody(),
        template_style: "remix",
        source_image_url: pinnedImage,
      }, true, true), {
        params: { clientId: "squid" },
      } as never);
      assert.equal(response.status, 502);
      assert.deepEqual(await response.json(), { error: "invalid_generation_response" });
      assert.equal(downstreamFetches, 0);
    });
  }
});

test("Squid remix distinguishes missing official media from an unavailable lookup", async () => {
  const cases = [
    { unavailable: false, expected: "source_image_required" },
    { unavailable: true, expected: "source_media_unavailable" },
  ];
  for (const item of cases) {
    let railwayCalls = 0;
    await withEnvironment(async (input) => {
      const url = String(input);
      if (url.endsWith("/storage/v1/bucket/content-studio")) {
        return Response.json({ id: "content-studio", public: false });
      }
      if (url.includes("/rest/v1/workspace_clients")) {
        return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
      }
      if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
      if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
      if (url.includes("cdn.syndication.twimg.com")) {
        return item.unavailable
          ? new Response("unavailable", { status: 503 })
          : Response.json({
            id_str: SOURCE_TWEET_ID,
            text: "Official Squid post with enough source text and no media.",
            user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
          });
      }
      if (url.startsWith("https://publish.x.com/oembed")) {
        return Response.json({
          html: "<blockquote><p>Official Squid fallback source text.</p></blockquote>",
        });
      }
      if (url.includes("railway.example")) railwayCalls += 1;
      throw new Error(`unexpected fetch ${url}`);
    }, async () => {
      const response = await newsCardHandler(studioRequest({
        ...requestBody(),
        template_style: "remix",
      }), {
        params: { clientId: "squid" },
      } as never);
      assert.equal(response.status, 422);
      assert.deepEqual(await response.json(), { error: item.expected });
      assert.equal(railwayCalls, 0);
    });
  }
});

test("browser sessions cannot inject a pinned source image", async () => {
  let fetched = false;
  await withEnvironment(async () => {
    fetched = true;
    return Response.json({});
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
      source_image_url: "https://pbs.twimg.com/media/official-source.jpg",
    }), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 403);
    assert.deepEqual(await response.json(), { error: "source_image_url_automation_only" });
    assert.equal(fetched, false);
  });
});

test("automation rejects pinned media outside the exact Squid X provenance", async () => {
  let fetched = false;
  await withEnvironment(async () => {
    fetched = true;
    return Response.json({});
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      source_url: "https://x.com/partner/status/1234567890",
      template_style: "remix",
      source_image_url: "https://pbs.twimg.com/media/official-source.jpg",
    }, true, true), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 422);
    assert.deepEqual(await response.json(), { error: "invalid_source_image_url" });
    assert.equal(fetched, false);
  });
});

test("a spoofed Squid URL cannot turn another account's tweet into an official source", async () => {
  let railwayCalls = 0;
  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
    if (url.includes("cdn.syndication.twimg.com")) {
      return Response.json({
        id_str: SOURCE_TWEET_ID,
        text: "Another account's tweet cannot become official through the URL path.",
        user: { id_str: "999999999999999999", screen_name: "anotheraccount" },
        photos: [{ url: "https://pbs.twimg.com/media/other-account.jpg" }],
      });
    }
    if (url.includes("railway.example")) railwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
    }), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 422);
    assert.deepEqual(await response.json(), { error: "source_not_official_squid" });
    assert.equal(railwayCalls, 0);
  });
});

test("automation rejects an allowlisted pbs URL that is not attached to the exact official tweet", async () => {
  const attachedImage = "https://pbs.twimg.com/media/attached.jpg?name=orig";
  const arbitraryImage = "https://pbs.twimg.com/media/arbitrary.jpg?name=orig";
  let railwayCalls = 0;
  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
    if (url.includes("cdn.syndication.twimg.com")) {
      return Response.json(officialSquidSyndicationPayload(attachedImage));
    }
    if (url.includes("railway.example")) railwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
      source_image_url: arbitraryImage,
    }, true, true), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 422);
    assert.deepEqual(await response.json(), { error: "invalid_source_image_url" });
    assert.equal(railwayCalls, 0);
  });
});

test("an exact news card retry replays verified Supabase bytes without Railway", async () => {
  const png = minimalPng();
  const sha256 = createHash("sha256").update(png).digest("hex");
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "classic",
  });
  const storagePath = `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`;
  let railwayCalls = 0;

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        client_id: "squid",
        content_kind: "daily_news",
        status: "needs_review",
        title: "Squid 라우팅 업데이트",
        content: {
          request_hash: requestHash,
          spec: { headline: "Squid 라우팅 업데이트", body_lines: ["사실"] },
          source: { mode: "provided", image_url: "" },
          render: { template_style: "classic", requested_template_style: "classic" },
        },
        channel_copy: { telegram: "텔레그램", x: "X" },
        generation_meta: {
          request_hash: requestHash,
          duration_ms: 1234,
          mock_mode: false,
          fact_check: validFactCheck(),
        },
        assets: [{
          asset_id: ASSET_ID,
          asset_kind: "png",
          storage_bucket: "content-studio",
          storage_path: storagePath,
          filename: "news-card.png",
          mime_type: "image/png",
          byte_size: png.byteLength,
          sha256,
          width: 1080,
          height: 1080,
        }],
      });
    }
    if (url.includes("/storage/v1/object/content-studio/")) return new Response(png);
    if (url.includes("railway.example")) railwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest(), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(result.reused, true);
    assert.equal(result.content_item_id, REQUEST_ID);
    assert.equal(result.asset_ids[0], ASSET_ID);
    assert.equal(railwayCalls, 0);
  });
});

test("an unpinned Squid remix replay binds stored proof to the currently resolved X image", async () => {
  const png = minimalPng();
  const resolvedImage = "https://pbs.twimg.com/media/current-source.jpg?name=orig";
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "remix",
  });
  let syndicationCalls = 0;
  let railwayCalls = 0;

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json(storedSquidRemixLookup(requestHash, {
        mode: "provided",
        image_url: resolvedImage,
        media_status: "present",
        prepared_sha256: SOURCE_IMAGE_SHA256,
      }, png));
    }
    if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
    if (url.includes("cdn.syndication.twimg.com")) {
      syndicationCalls += 1;
      return Response.json(officialSquidSyndicationPayload(resolvedImage));
    }
    if (url.includes("/storage/v1/object/content-studio/")) return new Response(png);
    if (url.includes("railway.example")) railwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
    }), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(result.reused, true);
    assert.equal(result.source_image_url, resolvedImage);
    assert.equal(result.source_image_sha256, SOURCE_IMAGE_SHA256);
    assert.equal(syndicationCalls, 1);
    assert.equal(railwayCalls, 0);
  });
});

test("an unpinned legacy Squid square replay remains available after live source verification", async () => {
  const png = minimalPng();
  const resolvedImage = "https://pbs.twimg.com/media/legacy-source.jpg?name=orig";
  const requestHash = createHash("sha256").update(JSON.stringify({
    client_id: "squid",
    source_content: SOURCE,
    source_type: "tweet",
    source_url: SOURCE_URL,
    mock_mode: false,
    template_style: "remix",
    style_references: [],
    style_reference_pack_hash: "",
  }), "utf8").digest("hex");
  assert.notEqual(requestHash, newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "remix",
  }));
  let railwayCalls = 0;

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json(storedSquidRemixLookup(requestHash, {
        mode: "provided",
        image_url: resolvedImage,
      }, png, true));
    }
    if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
    if (url.includes("cdn.syndication.twimg.com")) {
      return Response.json(officialSquidSyndicationPayload(resolvedImage));
    }
    if (url.includes("/storage/v1/object/content-studio/")) return new Response(png);
    if (url.includes("railway.example")) railwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
    }), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(result.reused, true);
    assert.equal(result.source_image_url, resolvedImage);
    assert.equal(result.source_image_sha256, "");
    assert.equal(result.source_media_status, "not_requested");
    assert.equal(result.output_width, 1080);
    assert.equal(result.output_height, 1080);
    assert.equal(railwayCalls, 0);
  });
});

test("a legacy Squid replay rejects a non-square stored PNG", async () => {
  const png = minimalPng(1200, 675);
  const resolvedImage = "https://pbs.twimg.com/media/legacy-source.jpg?name=orig";
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "remix",
  });

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json(storedSquidRemixLookup(requestHash, {
        mode: "provided",
        image_url: resolvedImage,
      }, png, true));
    }
    if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
    if (url.includes("cdn.syndication.twimg.com")) {
      return Response.json(officialSquidSyndicationPayload(resolvedImage));
    }
    if (url.includes("/storage/v1/object/content-studio/")) return new Response(png);
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
    }), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "durable_storage_invalid_response" });
  });
});

test("an unpinned Squid remix replay rejects malformed or stale stored source proof", async () => {
  const png = minimalPng();
  const resolvedImage = "https://pbs.twimg.com/media/current-source.jpg?name=orig";
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "remix",
  });
  const invalidSources = [
    {
      mode: "provided",
      image_url: "https://pbs.twimg.com/media/stale-source.jpg?name=orig",
      media_status: "present",
      prepared_sha256: SOURCE_IMAGE_SHA256,
    },
    {
      mode: "provided",
      image_url: resolvedImage,
      media_status: "present",
    },
  ];

  for (const storedSource of invalidSources) {
    let assetDownloads = 0;
    await withEnvironment(async (input) => {
      const url = String(input);
      if (url.endsWith("/storage/v1/bucket/content-studio")) {
        return Response.json({ id: "content-studio", public: false });
      }
      if (url.includes("/rest/v1/workspace_clients")) {
        return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
      }
      if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
        return Response.json(storedSquidRemixLookup(requestHash, storedSource, png));
      }
      if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
      if (url.includes("cdn.syndication.twimg.com")) {
        return Response.json(officialSquidSyndicationPayload(resolvedImage));
      }
      if (url.includes("/storage/v1/object/content-studio/")) {
        assetDownloads += 1;
        return new Response(png);
      }
      throw new Error(`unexpected fetch ${url}`);
    }, async () => {
      const response = await newsCardHandler(studioRequest({
        ...requestBody(),
        template_style: "remix",
      }), {
        params: { clientId: "squid" },
      } as never);
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: "durable_storage_invalid_response" });
      assert.equal(assetDownloads, 0);
    });
  }
});

test("an unpinned concurrent Squid replay rejects source proof that differs from the resolved X image", async () => {
  const png = minimalPng();
  const resolvedImage = "https://pbs.twimg.com/media/current-source.jpg?name=orig";
  const staleImage = "https://pbs.twimg.com/media/stale-source.jpg?name=orig";
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "remix",
  });
  let catalogLookups = 0;
  let assetDownloads = 0;
  let cleanupCalls = 0;

  await withEnvironment(async (input, init) => {
    const url = String(input);
    const method = init?.method || "GET";
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      catalogLookups += 1;
      return catalogLookups === 1
        ? Response.json(null)
        : Response.json(storedSquidRemixLookup(requestHash, {
          mode: "provided",
          image_url: staleImage,
          media_status: "present",
          prepared_sha256: SOURCE_IMAGE_SHA256,
        }, png));
    }
    if (url.endsWith("/rest/v1/rpc/get_brand_review_guidance")) return Response.json([]);
    if (url.includes("cdn.syndication.twimg.com")) {
      return Response.json(officialSquidSyndicationPayload(resolvedImage));
    }
    if (url.endsWith("/clients/squid/generate/news-card")) {
      const upstreamBody = JSON.parse(String(init?.body));
      assert.equal(upstreamBody.source_image_url, resolvedImage);
      return Response.json({
        client_id: "squid",
        content_type: "news_card",
        spec: {
          ...squidCreativeMetadata("remix"),
          headline: "Squid 공식 비주얼",
          body_lines: ["원문 기반"],
          source_text_visible: false,
          translation_regions: [],
          visual_localization_status: "no_source_text",
          output_policy: "official_source_native_v1",
          source_image_width: 1080,
          source_image_height: 1080,
          output_width: 1080,
          output_height: 1080,
        },
        png_path: "/app/output/squid/news_789/news_card_remix.png",
        template_style: "remix",
        requested_template_style: "remix",
        source_image_used: true,
        source_image_url: resolvedImage,
        source_image_sha256: SOURCE_IMAGE_SHA256,
        source_visual_path: null,
        figma_template: null,
        manifest_path: "/app/output/squid/news_789/manifest.json",
        duration_ms: 987,
      });
    }
    if (url.endsWith("/files/squid/news_789/news_card_remix.png")) {
      return new Response(png, { headers: { "content-type": "image/png" } });
    }
    if (url.includes("/storage/v1/object/content-studio/") && method === "POST") {
      return Response.json({ Key: "stored" });
    }
    if (url.endsWith("/rest/v1/rpc/record_generated_content")) {
      return new Response("conflict", { status: 409 });
    }
    if (url.endsWith("/storage/v1/object/content-studio") && method === "DELETE") {
      cleanupCalls += 1;
      return new Response(null, { status: 200 });
    }
    if (url.includes("/storage/v1/object/content-studio/") && method === "GET") {
      assetDownloads += 1;
      return new Response(png);
    }
    throw new Error(`unexpected fetch ${method} ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
    }), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "durable_storage_invalid_response" });
    assert.equal(catalogLookups, 2);
    assert.equal(cleanupCalls, 1);
    assert.equal(assetDownloads, 0);
  });
});

test("an exact pinned Squid retry replays the same source proof and PNG without Railway", async () => {
  const png = minimalPng();
  const pinnedImage = "https://pbs.twimg.com/media/official-source.jpg?name=orig";
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "remix",
    sourceImageUrl: pinnedImage,
  });
  let railwayCalls = 0;
  let syndicationCalls = 0;

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.includes("cdn.syndication.twimg.com")) {
      syndicationCalls += 1;
      return new Response("X unavailable during an exact replay", { status: 503 });
    }
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json(storedSquidRemixLookup(requestHash, {
        mode: "provided",
        image_url: pinnedImage,
        media_status: "present",
        prepared_sha256: SOURCE_IMAGE_SHA256,
      }, png));
    }
    if (url.includes("/storage/v1/object/content-studio/")) return new Response(png);
    if (url.includes("railway.example")) railwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
      source_image_url: pinnedImage,
    }, true, true), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 200, JSON.stringify(await response.clone().json()));
    const result = await response.json();
    assert.equal(result.reused, true);
    assert.equal(result.content_item_id, REQUEST_ID);
    assert.equal(result.content_version_id, VERSION_ID);
    assert.equal(result.asset_ids[0], ASSET_ID);
    assert.equal(result.source_image_url, pinnedImage);
    assert.equal(result.source_image_sha256, SOURCE_IMAGE_SHA256);
    assert.equal(result.source_media_status, "present");
    assert.equal(result.source_image_used, true);
    assert.equal(syndicationCalls, 0);
    assert.equal(railwayCalls, 0);
  });
});

test("a pinned Squid retry with another source image conflicts before asset access", async () => {
  const firstImage = "https://pbs.twimg.com/media/first.jpg?name=orig";
  const secondImage = "https://pbs.twimg.com/media/second.jpg?name=orig";
  const storedHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "remix",
    sourceImageUrl: firstImage,
  });
  let assetOrRailwayCalls = 0;

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        client_id: "squid",
        content_kind: "daily_news",
        status: "needs_review",
        title: "Squid 공식 비주얼",
        content: {
          request_hash: storedHash,
          spec: { headline: "Squid 공식 비주얼" },
          source: { mode: "provided", image_url: firstImage, media_status: "present" },
          render: { template_style: "remix", requested_template_style: "remix", source_image_used: true },
        },
        channel_copy: { telegram: "텔레그램", x: "X" },
        generation_meta: {
          request_hash: storedHash,
          duration_ms: 987,
          mock_mode: false,
          fact_check: validFactCheck(),
        },
        assets: [{
          asset_id: ASSET_ID,
          asset_kind: "png",
          storage_bucket: "content-studio",
          storage_path: `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`,
          filename: "news-card.png",
          mime_type: "image/png",
          byte_size: 25,
          sha256: "0".repeat(64),
          width: 1080,
          height: 1080,
        }],
      });
    }
    assetOrRailwayCalls += 1;
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest({
      ...requestBody(),
      template_style: "remix",
      source_image_url: secondImage,
    }, true, true), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 409);
    assert.deepEqual(await response.json(), { error: "news_card_idempotency_conflict" });
    assert.equal(assetOrRailwayCalls, 0);
  });
});

test("news card generation rejects a PNG that cannot fit safely in base64 JSON", async () => {
  const png = new Uint8Array(MAX_NEWS_CARD_BYTES + 1);
  png.set(minimalPng());
  let uploadCalls = 0;

  await withEnvironment(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) return Response.json(null);
    if (url.endsWith("/clients/squid/generate/news-card")) {
      return Response.json({
        client_id: "squid",
        content_type: "news_card",
        spec: {
          ...squidCreativeMetadata("classic"),
          headline: "Squid 라우팅 업데이트",
        },
        png_path: "/app/output/squid/news_123/news_card_classic.png",
        template_style: "classic",
        requested_template_style: "classic",
        source_image_used: false,
        source_visual_path: null,
        manifest_path: "/app/output/squid/news_123/manifest.json",
        duration_ms: 1234,
      });
    }
    if (url.endsWith("/files/squid/news_123/news_card_classic.png")) {
      return new Response(png, { headers: { "content-type": "image/png" } });
    }
    if (url.includes("/storage/v1/object/content-studio/") && init?.method === "POST") {
      uploadCalls += 1;
      return Response.json({ Key: "unexpected" });
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest(), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "generated_image_too_large" });
    assert.equal(uploadCalls, 0);
  });
});

test("news card replay rejects an oversized stored PNG before download", async () => {
  const requestHash = newsCardRequestHash({
    clientId: "squid",
    sourceContent: SOURCE,
    sourceType: "tweet",
    sourceUrl: SOURCE_URL,
    mockMode: false,
    templateStyle: "classic",
  });
  const storagePath = `${WORKSPACE_ID}/squid/${ASSET_ID}/news-card.png`;
  let assetDownloads = 0;

  await withEnvironment(async (input) => {
    const url = String(input);
    if (url.endsWith("/storage/v1/bucket/content-studio")) {
      return Response.json({ id: "content-studio", public: false });
    }
    if (url.includes("/rest/v1/workspace_clients")) {
      return Response.json([{ workspace_id: WORKSPACE_ID, client_id: "squid", active: true }]);
    }
    if (url.endsWith("/rest/v1/rpc/get_generated_content")) {
      return Response.json({
        content_item_id: REQUEST_ID,
        content_version_id: VERSION_ID,
        client_id: "squid",
        content_kind: "daily_news",
        status: "needs_review",
        title: "Squid 라우팅 업데이트",
        content: {
          request_hash: requestHash,
          spec: { headline: "Squid 라우팅 업데이트" },
          source: { mode: "provided", image_url: "" },
          render: { template_style: "classic", requested_template_style: "classic" },
        },
        channel_copy: { telegram: "텔레그램", x: "X" },
        generation_meta: {
          request_hash: requestHash,
          duration_ms: 1234,
          mock_mode: false,
          fact_check: validFactCheck(),
        },
        assets: [{
          asset_id: ASSET_ID,
          asset_kind: "png",
          storage_bucket: "content-studio",
          storage_path: storagePath,
          filename: "news-card.png",
          mime_type: "image/png",
          byte_size: MAX_NEWS_CARD_BYTES + 1,
          sha256: "0".repeat(64),
          width: 1080,
          height: 1080,
        }],
      });
    }
    if (url.includes("/storage/v1/object/content-studio/")) {
      assetDownloads += 1;
      return new Response(minimalPng());
    }
    throw new Error(`unexpected fetch ${url}`);
  }, async () => {
    const response = await newsCardHandler(studioRequest(), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "generated_image_too_large" });
    assert.equal(assetDownloads, 0);
  });
});

test("news card generation requires a UUID idempotency key before fetching", async () => {
  let fetched = false;
  await withEnvironment(async () => {
    fetched = true;
    return Response.json({});
  }, async () => {
    const response = await newsCardHandler(studioRequest(requestBody(), false), {
      params: { clientId: "squid" },
    } as never);
    assert.equal(response.status, 400);
    assert.deepEqual(await response.json(), { error: "invalid_news_card_idempotency_key" });
    assert.equal(fetched, false);
  });
});
