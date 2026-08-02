import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalXStatusUrl,
  extractXMediaUrls,
  extractXMediaUrl,
  extractXPostText,
  hasVerifiedOfficialSquidXProvenance,
  normalizeXImageUrl,
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
  assert.equal(
    canonicalXStatusUrl("https://x.com/squidrouter/status/123/photo/1?s=20"),
    "https://x.com/squidrouter/status/123",
  );
  assert.equal(canonicalXStatusUrl("https://x.com/squidrouter/status/123abc"), null);
  assert.equal(canonicalXStatusUrl("https://x.com/squidrouter/status/123/other/1"), null);
  assert.equal(canonicalXStatusUrl("https://x.com/squidrouter/status/123456789012345678901"), null);
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
  assert.equal(normalizeXImageUrl("https://user@pbs.twimg.com/media/banner.jpg"), "");
  assert.equal(normalizeXImageUrl("https://pbs.twimg.com/profile_images/banner.jpg"), "");
  assert.equal(normalizeXImageUrl("https://pbs.twimg.com/media/banner.jpg?redirect=https://example.com"), "");
  assert.equal(
    normalizeXImageUrl("https://pbs.twimg.com/tweet_video_thumb/animated.jpg?name=small"),
    "https://pbs.twimg.com/tweet_video_thumb/animated.jpg?name=orig",
  );
  assert.equal(
    normalizeXImageUrl("https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/poster.jpg"),
    "https://pbs.twimg.com/ext_tw_video_thumb/123/pu/img/poster.jpg?name=orig",
  );
});

test("collects every canonical media URL from the exact payload", () => {
  assert.deepEqual(
    extractXMediaUrls({
      photos: [
        { url: "https://pbs.twimg.com/media/first.jpg?name=small" },
        { url: "https://pbs.twimg.com/media/second.png?format=png&name=large" },
      ],
      mediaDetails: [{
        type: "photo",
        media_url_https: "https://pbs.twimg.com/media/first.jpg?name=medium",
      }],
    }),
    [
      "https://pbs.twimg.com/media/first.jpg?name=orig",
      "https://pbs.twimg.com/media/second.png?format=png&name=orig",
    ],
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
  assert.equal(result.mediaStatus, "not_requested");
});

test("fetches a public X post when only its link is provided", async () => {
  const fetchImpl = async (input: string | URL | Request) => {
    assert.match(String(input), /^https:\/\/cdn\.syndication\.twimg\.com\/tweet-result\?/);
    return Response.json({
      id_str: "123",
      text: "A public X post with enough source text.\nSecond line. https://t.co/media",
      user: { id_str: "42", screen_name: "squidrouter" },
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
  assert.equal(result.mediaStatus, "present");
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
    id_str: "123",
    text: "The original X text should not replace manually provided copy.",
    user: { id_str: "42", screen_name: "squidrouter" },
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
  assert.equal(result.mediaStatus, "present");
});

test("imports same-account quoted Squid media for a manual remix", async () => {
  const fetchImpl = async () => Response.json({
    id_str: "2081031728622178334",
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
  assert.equal(result.mediaStatus, "present");
});

test("distinguishes an official X post without media from an unavailable media lookup", async () => {
  const absent = await resolveSourceInput(
    "Use this manually provided Squid source context.",
    "https://x.com/squidrouter/status/123",
    (async () => Response.json({
      text: "An official Squid post with text and no media attachment.",
      id_str: "123",
      user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
    })) as typeof fetch,
    true,
  );
  assert.equal(absent.imageUrl, "");
  assert.equal(absent.mediaStatus, "absent");

  const unavailable = await resolveSourceInput(
    "Use this manually provided Squid source context.",
    "https://x.com/squidrouter/status/123",
    (async (input) => String(input).includes("cdn.syndication.twimg.com")
      ? new Response("unavailable", { status: 503 })
      : Response.json({ html: "<blockquote><p>Official Squid fallback source text.</p></blockquote>" })) as typeof fetch,
    true,
  );
  assert.equal(unavailable.imageUrl, "");
  assert.equal(unavailable.mediaStatus, "unavailable");
});

test("binds official Squid provenance to the resolved tweet id and immutable account id", async () => {
  const sourceUrl = "https://x.com/squidrouter/status/123";
  const resolve = (payload: Record<string, unknown>) => resolveSourceInput(
    "Use this manually provided Squid source context.",
    sourceUrl,
    (async () => Response.json(payload)) as typeof fetch,
    true,
  );
  const mediaUrl = "https://pbs.twimg.com/media/official.jpg";
  const valid = await resolve({
    id_str: "123",
    text: "Official Squid source post with enough provenance text.",
    user: { id_str: "1547672532660105216", screen_name: "SquidRouter" },
    photos: [{ url: mediaUrl }],
  });
  assert.equal(hasVerifiedOfficialSquidXProvenance(valid), true);
  assert.deepEqual(valid.xProvenance?.mediaUrls, [`${mediaUrl}?name=orig`]);

  const spoofedAuthor = await resolve({
    id_str: "123",
    text: "Another account's post reached through a spoofed path.",
    user: { id_str: "999999999999999999", screen_name: "anotheraccount" },
    photos: [{ url: mediaUrl }],
  });
  assert.equal(hasVerifiedOfficialSquidXProvenance(spoofedAuthor), false);

  const mismatchedTweet = await resolve({
    id_str: "456",
    text: "A payload whose tweet id does not match the requested status.",
    user: { id_str: "1547672532660105216", screen_name: "squidrouter" },
    photos: [{ url: mediaUrl }],
  });
  assert.equal(hasVerifiedOfficialSquidXProvenance(mismatchedTweet), false);
  assert.equal(mismatchedTweet.imageUrl, "");
  assert.equal(mismatchedTweet.mediaStatus, "unavailable");
  assert.deepEqual(mismatchedTweet.xProvenance?.mediaUrls, []);
});

test("withholds syndicated media unless tweet and author identities are valid", async () => {
  const sourceUrl = "https://x.com/partner/status/123";
  const mediaUrl = "https://pbs.twimg.com/media/partner.jpg";
  const resolve = (payload: Record<string, unknown>) => resolveSourceInput(
    "Use this manually provided source context for another client.",
    sourceUrl,
    (async () => Response.json(payload)) as typeof fetch,
    true,
  );

  const valid = await resolve({
    id_str: "123",
    text: "A verified partner post with an attached media creative.",
    user: { id_str: "42", screen_name: "partner" },
    photos: [{ url: mediaUrl }],
  });
  assert.equal(valid.imageUrl, `${mediaUrl}?name=orig`);
  assert.equal(valid.mediaStatus, "present");

  for (const payload of [
    {
      id_str: "456",
      text: "A mismatched syndicated tweet must not expose attached media.",
      user: { id_str: "42", screen_name: "partner" },
      photos: [{ url: mediaUrl }],
    },
    {
      id_str: "123",
      text: "A syndicated tweet without a valid user id must not expose media.",
      user: { id_str: "not-a-user-id", screen_name: "partner" },
      photos: [{ url: mediaUrl }],
    },
    {
      id_str: "123",
      text: "A syndicated tweet without a valid handle must not expose media.",
      user: { id_str: "42", screen_name: "invalid-handle!" },
      photos: [{ url: mediaUrl }],
    },
  ]) {
    const result = await resolve(payload);
    assert.equal(result.imageUrl, "");
    assert.equal(result.mediaStatus, "unavailable");
    assert.deepEqual(result.xProvenance?.mediaUrls, []);
  }
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
