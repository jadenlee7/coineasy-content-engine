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
