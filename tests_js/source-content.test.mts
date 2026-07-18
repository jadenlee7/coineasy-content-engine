import assert from "node:assert/strict";
import test from "node:test";

import {
  canonicalXStatusUrl,
  extractXPostText,
  normalizeSourceUrl,
  resolveSourceInput,
  SourceInputError,
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
});

test("fetches a public X post when only its link is provided", async () => {
  const fetchImpl = async (input: string | URL | Request) => {
    assert.match(String(input), /^https:\/\/publish\.x\.com\/oembed\?/);
    return Response.json({
      html: '<blockquote><p lang="en">A public X post with enough source text.<br>Second line.</p></blockquote>',
    });
  };
  const result = await resolveSourceInput(
    "",
    "x.com/squidrouter/status/123?s=46",
    fetchImpl as typeof fetch,
  );
  assert.equal(result.mode, "x_oembed");
  assert.equal(result.url, "https://x.com/squidrouter/status/123");
  assert.match(result.content, /public X post/);
});

test("also fetches when the X link is pasted into the content field", async () => {
  const fetchImpl = async () => Response.json({
    html: '<blockquote><p lang="en">Content imported from a link pasted into the main field.</p></blockquote>',
  });
  const result = await resolveSourceInput(
    "https://x.com/squidrouter/status/123?s=46",
    "",
    fetchImpl as typeof fetch,
  );
  assert.equal(result.mode, "x_oembed");
  assert.equal(result.url, "https://x.com/squidrouter/status/123");
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
