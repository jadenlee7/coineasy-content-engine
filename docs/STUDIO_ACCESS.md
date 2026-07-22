# Content Studio team access

The public Netlify page never receives Railway's `API_SECRET` or the team access token. Configure a high-entropy `STUDIO_ACCESS_TOKEN` (at least 16 characters) in Netlify's server-side environment before enabling the console.

`POST /api/studio-session` compares the submitted access code in constant time and issues a four-hour `HttpOnly; Secure; SameSite=Strict` signed cookie. `DELETE /api/studio-session` clears it. The access code is sent only to this endpoint and is not written to browser storage.

The `news-card`, `article`, `tutorial`, and `editable-card` relays fail closed when the token is absent and require a valid session before any upstream fetch. Tutorial slide URLs retain path-scoped, expiring HMAC signatures, but the signed path now identifies a private Supabase Storage object rather than a Railway-local file. Tutorial generation also fails closed until durable Storage is configured and verified private.

The tutorial response is returned only after Supabase has atomically cataloged the
generated pages as one `needs_review` content version. A stable request ID makes an
uncertain catalog response safe to retry without creating duplicate history. The
browser retains that ID after timeouts, and the relay checks both the immutable
asset list and the real private Storage objects before returning a reused result.
Tutorial work is bounded to 55 seconds so Netlify still has time to catalog or
clean up. The complete accepted source and an explicit mock/test marker are stored
with the durable version.

Locking the Studio (logout, expired session, or missing server configuration)
clears pasted source text, generated copy, article Markdown, image/SVG links, and
in-memory retry state from the page. In-flight responses carry a session epoch and
cannot repopulate the page after a later login. Test-mode tutorials remain visibly
marked `샘플 · 게시 금지` even when loaded through an idempotent retry. The
database also rejects approval and publication of their `mock_mode: true` versions,
so this is enforced beyond the browser label.

Login failures are limited to five attempts per IP in a ten-minute window. The counter is held in the warm function instance's memory, so it is a best-effort speed bump rather than a distributed security control. Replace it with a shared rate-limit store or Netlify edge/WAF rule before treating the console as an internet-facing multi-user authentication system.
