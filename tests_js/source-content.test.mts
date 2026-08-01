import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalXStatusUrl,
  extractXMediaUrl,
  extractXPostText,
  normalizeSourceUrl,
  resolveSourceInput,
  SourceInputError,
  xSyndicationToken,
} from "../netlify/functions/_shared/source-content.mts";

test("normalizes scheme-less X links and canonicalizes status URLs", () => {
  assert.equal(
    normalizeSourceUrl("x.com/squidrouter/status/123?s=46"),
    "https://x.com/squidrouter/status/123?s=46",
  );
  assert.equal(
    canonicalXStatusUrl("https://twitter.com/squidrouter/status/123?s=46"),
    "https://x.com/squidrouter/status/123",
  );
});

test("extracts readable post text from X oEmbed HTML", () => {
  const html = '<blockquote><p lang="en">Swap <a href="/x">$XRP</a><br>Stay in self-custody &amp; safe.</p></blockquote>';
  assert.equal(extractXPostText(html), "Swap $XRP\nStay in self-custody & safe.");
});

test("builds the X syndication token and accepts only pbs.twimg.com media", () => {
  assert.equal(xSyndicationToken("2077373111717282032"), "51a9d3j3l3");
  assert.equal(
    extractXMediaUrl({ photos: [{ url: "https://pbs.twimg.com/media/banner.jpg" }] }),
    "https://pbs.twimg.com/media/banner.jpg?name=orig",
  );
  assert.equal(
    extractXMediaUrl({ photos: [{ url: "https://example.com/banner.jpg" }] }),
    "",
  );
});

test("inherits photo media from a same-account official Squid quote", () => {
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "SquidRouter" },
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
        photos: [{ url: "https://pbs.twimg.com/media/quoted-banner.jpg" }],
      },
    }),
    "https://pbs.twimg.com/media/quoted-banner.jpg?name=orig",
  );
});

test("inherits a safe video poster from a same-account official Squid quote", () => {
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "@squidrouter" },
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "SquidRouter" },
        video: {
          poster: "https://pbs.twimg.com/amplify_video_thumb/123/img/poster.jpg?format=jpg&name=small",
        },
      },
    }),
    "https://pbs.twimg.com/amplify_video_thumb/123/img/poster.jpg?format=jpg&name=orig",
  );
});

test("treats null outer syndication media as absent for the real Canton quote shape", () => {
  assert.equal(
    extractXMediaUrl({
      photos: null,
      mediaDetails: null,
      video: null,
      user: { id_str: "1547672532660105216", screen_name: "SquidRouter" },
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
        video: {
          poster: "https://pbs.twimg.com/amplify_video_thumb/2079266440268464128/img/canton.jpg",
        },
      },
    }),
    "https://pbs.twimg.com/amplify_video_thumb/2079266440268464128/img/canton.jpg?name=orig",
  );
});

test("inherits a safe video thumbnail when the poster field is absent", () => {
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
        mediaDetails: [{
          type: "video",
          media_url_https: "https://pbs.twimg.com/amplify_video_thumb/123/img/fallback.jpg",
        }],
      },
    }),
    "https://pbs.twimg.com/amplify_video_thumb/123/img/fallback.jpg?name=orig",
  );
});

test("uses direct media before a same-account official Squid quote", () => {
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
      photos: [{ url: "https://pbs.twimg.com/media/direct-banner.jpg" }],
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
        photos: [{ url: "https://pbs.twimg.com/media/quoted-banner.jpg" }],
      },
    }),
    "https://pbs.twimg.com/media/direct-banner.jpg?name=orig",
  );
});

test("does not inherit quote media when direct media is present but unsafe", () => {
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
      photos: [{ url: "https://example.com/invalid-direct.jpg" }],
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
        photos: [{ url: "https://pbs.twimg.com/media/quoted-banner.jpg" }],
      },
    }),
    "",
  );
});

test("does not inherit quote media when direct media evidence is malformed", () => {
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
      photos: { url: "https://pbs.twimg.com/media/not-an-array.jpg" },
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
        photos: [{ url: "https://pbs.twimg.com/media/quoted-banner.jpg" }],
      },
    }),
    "",
  );
});

test("does not inherit quote media across accounts or from an unsafe host", () => {
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
      quoted_tweet: {
        user: { id_str: "1635419045058031617", screen_name: "CantonNetwork" },
        photos: [{ url: "https://pbs.twimg.com/media/canton-banner.jpg" }],
      },
    }),
    "",
  );
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1635419045058031617", screen_name: "CantonNetwork" },
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
        photos: [{ url: "https://pbs.twimg.com/media/squid-banner.jpg" }],
      },
    }),
    "",
  );
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
      quoted_tweet: {
        user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
        video: { poster: "https://example.com/spoofed-poster.jpg" },
      },
    }),
    "",
  );
  assert.equal(
    extractXMediaUrl({
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
      quoted_tweet: {
        user: { id_str: "999999999999999999", screen_name: "squidrouter" },
        photos: [{ url: "https://pbs.twimg.com/media/spoofed-account.jpg" }],
      },
    }),
    "",
  );
});

test("uses provided content without fetching the linked source", async () => {
  const fetchImpl = async () => {
    throw new Error("fetch should not run");
  };
  const result = await resolveSourceInput(
    "Squid launched a new cross-chain route for XRP users.",
    "https://x.com/squidrouter/status/123?s=46",
    fetchImpl as typeof fetch,
  );
  assert.equal(result.mode, "provided");
  assert.equal(result.url, "https://x.com/squidrouter/status/123");
  assert.equal(result.imageUrl, "");
});

test("fetches a public X post when only its link is provided", async () => {
  const fetchImpl = async (input: string | URL | Request) => {
    assert.match(String(input), /^https:\/\/cdn\.syndication\.twimg\.com\/tweet-result\?/);
    return Response.json({
      text: "A public X post with enough source text.\nSecond line. https://t.co/media",
      photos: [{ url: "https://pbs.twimg.com/media/banner.jpg" }],
      mediaDetails: [{ type: "photo", url: "https://t.co/media" }],
    });
  };
  const result = await resolveSourceInput(
    "",
    "x.com/squidrouter/status/123?s=46",
    fetchImpl as typeof fetch,
  );
  assert.equal(result.mode, "x_import");
  assert.equal(result.url, "https://x.com/squidrouter/status/123");
  assert.match(result.content, /public X post/);
  assert.doesNotMatch(result.content, /t\.co/);
  assert.equal(result.imageUrl, "https://pbs.twimg.com/media/banner.jpg?name=orig");
});

test("also fetches when the X link is pasted into the content field", async () => {
  const fetchImpl = async () => Response.json({
    text: "Content imported from a link pasted into the main field.",
  });
  const result = await resolveSourceInput(
    "https://x.com/squidrouter/status/123?s=46",
    "",
    fetchImpl as typeof fetch,
  );
  assert.equal(result.mode, "x_import");
  assert.equal(result.url, "https://x.com/squidrouter/status/123");
});

test("imports media for remix while preserving manually provided source text", async () => {
  const fetchImpl = async () => Response.json({
    text: "The original X text should not replace manually provided copy.",
    photos: [{ url: "https://pbs.twimg.com/media/remix.png" }],
  });
  const result = await resolveSourceInput(
    "Use this manually provided Korean GTM source context.",
    "https://x.com/squidrouter/status/123",
    fetchImpl as typeof fetch,
    true,
  );
  assert.equal(result.mode, "provided");
  assert.equal(result.content, "Use this manually provided Korean GTM source context.");
  assert.equal(result.imageUrl, "https://pbs.twimg.com/media/remix.png?name=orig");
});

test("imports same-account quoted Squid media for a manual remix", async () => {
  const fetchImpl = async () => Response.json({
    text: "Have you explored Canton yet? With Squid, it is easy.",
    user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
    quoted_tweet: {
      text: "Canton Network is live on Squid Intents.",
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
      video: {
        poster: "https://pbs.twimg.com/amplify_video_thumb/2079266440268464128/img/canton.jpg",
      },
    },
  });
  const result = await resolveSourceInput(
    "Squid Canton 소식을 한국 사용자에게 설명합니다.",
    "https://x.com/squidrouter/status/2081031728622178334",
    fetchImpl as typeof fetch,
    true,
  );
  assert.equal(result.mode, "provided");
  assert.equal(result.content, "Squid Canton 소식을 한국 사용자에게 설명합니다.");
  assert.equal(
    result.imageUrl,
    "https://pbs.twimg.com/amplify_video_thumb/2079266440268464128/img/canton.jpg?name=orig",
  );
});

test("rejects non-X URL-only input", async () => {
  await assert.rejects(
    resolveSourceInput("", "https://example.com/article"),
    (error: unknown) => error instanceof SourceInputError && error.code === "source_content_must_be_10_to_20000_chars",
  );
  await assert.rejects(
    resolveSourceInput("https://example.com/article", ""),
    (error: unknown) => error instanceof SourceInputError && error.code === "source_content_must_be_10_to_20000_chars",
  );
});
