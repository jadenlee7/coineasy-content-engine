# Content Studio team access

The public Netlify page never receives Railway's `API_SECRET` or the team access token. Configure a high-entropy `STUDIO_ACCESS_TOKEN` (at least 16 characters) in Netlify's server-side environment before enabling the console.

`POST /api/studio-session` compares the submitted access code in constant time and issues a four-hour `HttpOnly; Secure; SameSite=Strict` signed cookie. `DELETE /api/studio-session` clears it. The access code is sent only to this endpoint and is not written to browser storage.

The `news-card`, `article`, `tutorial`, `editable-card`, and `library` relays fail closed when the token is absent and require a valid session before any upstream fetch. Generated PNG links are short-lived and identify private Supabase Storage objects rather than Railway-local files. Generation also fails closed until the durable catalog is configured and its Storage bucket is verified private.

Daily News, Article, and Tutorial responses are returned only after Supabase has
atomically cataloged one immutable `needs_review` content version. News and
Tutorial versions include validated private PNG assets; Article versions have no
asset. A stable request ID makes an
uncertain catalog response safe to retry without creating duplicate history. The
browser retains that ID after timeouts, and each relay checks the immutable request
hash before returning a reused result. Asset-backed results also verify the stored
asset metadata and private object before replay. Work is time-bounded so Netlify
still has time to catalog or safely reconcile the result. The complete accepted
source and an explicit mock/test marker are stored with the durable version.

Locking the Studio (logout, expired session, or missing server configuration)
clears pasted source text, generated copy, article Markdown, image/SVG links, and
in-memory retry state from the page. In-flight responses carry a session epoch and
cannot repopulate the page after a later login. Test-mode generations remain visibly
marked `샘플 · 게시 금지` even when loaded through an idempotent retry or the
team library. The database also rejects approval and publication of
`mock_mode: true` versions,
so this is enforced beyond the browser label.

Login failures are limited to five attempts per IP in a ten-minute window. The counter is held in the warm function instance's memory, so it is a best-effort speed bump rather than a distributed security control. Replace it with a shared rate-limit store or Netlify edge/WAF rule before treating the console as an internet-facing multi-user authentication system.
