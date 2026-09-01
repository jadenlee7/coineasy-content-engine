import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
import test from 'node:test';

// Explicit local-only browser integration, not a production browser/session.
// The default suite never launches a browser or opens a socket.
test('real Chromium IndexedDB guard: same-profile atomic attempt and retained failures', {
  skip: process.env.MANAGED_INSPECT_BROWSER_TEST !== '1', timeout: 90_000,
}, async (t) => {
  const modulePath = process.env.MANAGED_INSPECT_PLAYWRIGHT_MODULE;
  assert.ok(modulePath?.startsWith('/') && modulePath.endsWith('/playwright/index.mjs'),
    'explicit installed Playwright module path required');
  const { chromium } = await import(pathToFileURL(modulePath).href);
  const guard = await readFile(new URL('../tools/managed-telegram-inspect/browser-guard.mjs', import.meta.url), 'utf8');
  const attempts = new Map<string, number>();
  const id = (n: number) => `10000000-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
  const html = (consent: string) => `<!doctype html><html lang="en"><title>Synthetic guard test</title>
    <form method="post" action="/inspect" data-inspect-form
      data-consent-id="${consent}" data-request-hash="${'a'.repeat(64)}">
      <input type="hidden" name="attempt_marker_committed" value="0">
      <input type="hidden" name="consent_id" value="${consent}">
      <button disabled>Inspect synthetic fixture</button></form>
    <p data-attempt-status></p><script type="module" src="/guard.mjs"></script></html>`;
  const server = createServer((req, res) => {
    res.setHeader('Cache-Control', 'no-store');
    if (req.url === '/guard.mjs') {
      res.setHeader('Content-Type', 'text/javascript'); res.end(guard); return;
    }
    if (req.method === 'POST' && req.url === '/inspect') {
      let body = '';
      req.on('data', (chunk) => { body += chunk; });
      req.on('end', () => {
        const consent = new URLSearchParams(body).get('consent_id')!;
        attempts.set(consent, (attempts.get(consent) ?? 0) + 1);
        // Deliberate ambiguous network failure; a client must not retry this POST.
        if (consent === id(2)) { res.destroy(); return; }
        res.setHeader('Content-Type', 'application/json');
        res.end(JSON.stringify({ synthetic: true, result: 'synthetic bounded result' }));
      }); return;
    }
    if (req.method === 'GET' && /^\/[0-9a-f-]{36}$/.test(req.url ?? '')) {
      res.setHeader('Content-Type', 'text/html'); res.end(html(req.url!.slice(1))); return;
    }
    res.statusCode = 404; res.end();
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address(); assert.ok(address && typeof address !== 'string');
  const origin = `http://127.0.0.1:${address.port}`;
  const browser = await chromium.launch({ headless: true }).catch((error: unknown) => {
    server.closeAllConnections(); server.close(); throw error;
  });
  const context = await browser.newContext();
  // Prevent this synthetic test browser from reaching non-loopback destinations.
  await context.route('**/*', (route: any) => new URL(route.request().url()).origin === origin
    ? route.continue() : route.abort());
  const ready = async (page: any, consent: string) => {
    await page.goto(`${origin}/${consent}`);
    await page.waitForFunction(() => !document.querySelector('button')!.disabled);
  };
  const count = (consent: string) => attempts.get(consent) ?? 0;
  try {
    await t.test('two tabs compete on the actual IndexedDB unique key: only one POST', async () => {
      const pages = await Promise.all([context.newPage(), context.newPage()]);
      await Promise.all(pages.map((page) => ready(page, id(1))));
      await Promise.all(pages.map((page) => page.locator('button').click()));
      await Promise.all(pages.map((page) => page.waitForFunction(() =>
        document.body.textContent!.includes('synthetic bounded result') || document.body.textContent!.includes('BLOCK:'))));
      assert.equal(count(id(1)), 1);
      await Promise.all(pages.map((page) => page.close()));
    });
    await t.test('one fetch invocation despite browser wire retries; unknown outcome retains marker', async () => {
      const page = await context.newPage();
      await page.addInitScript(() => {
        const original = globalThis.fetch;
        (globalThis as any).inspectFetchInvocations = 0;
        globalThis.fetch = (...args) => {
          (globalThis as any).inspectFetchInvocations++;
          return original(...args);
        };
      });
      await ready(page, id(2));
      const failed = page.waitForEvent('requestfailed', { predicate: (request: any) =>
        request.method() === 'POST' && request.url() === `${origin}/inspect` });
      await page.locator('button').click();
      await failed;
      await page.waitForFunction(() => document.body.textContent!.includes('BLOCK:'));
      assert.equal(await page.evaluate(() => (globalThis as any).inspectFetchInvocations), 1);
      const wireRequests = count(id(2));
      assert.ok(wireRequests >= 1);
      // Chromium may internally replay even a fetch POST after connection reset.
      // Do not call this network-level exactly-once. The actual server's separate
      // concurrent/unknown-result tests require only one upstream inspect RPC.
      t.diagnostic(`one explicit fetch; browser transport POSTs observed=${wireRequests}`);
      await ready(page, id(2)); await page.locator('button').click();
      await page.waitForFunction(() => document.body.textContent!.includes('BLOCK:'));
      assert.equal(await page.evaluate(() => (globalThis as any).inspectFetchInvocations), 0);
      assert.equal(count(id(2)), wireRequests); await page.close();
    });
    await t.test('crash after actual commit but before POST permanently holds this marker', async () => {
      const page = await context.newPage(); await ready(page, id(3));
      await page.evaluate(async ({ consent, hash }: any) => {
        const api = await import('/guard.mjs');
        const db = await api.openMarkerDatabase(indexedDB);
        await api.commitAttempt(db, consent, hash); db.close();
      }, { consent: id(3), hash: 'a'.repeat(64) });
      await page.close();
      const next = await context.newPage(); await ready(next, id(3));
      await next.locator('button').click();
      await next.waitForFunction(() => document.body.textContent!.includes('BLOCK:'));
      assert.equal(count(id(3)), 0); await next.close();
    });
    await t.test('aborted pre-commit write creates no durable marker and sends nothing', async () => {
      const page = await context.newPage(); await ready(page, id(4));
      await page.evaluate(async (consent: string) => {
        const api = await import('/guard.mjs');
        const db = await api.openMarkerDatabase(indexedDB);
        await new Promise<void>((resolve) => {
          const tx = db.transaction('attempts', 'readwrite');
          tx.onabort = () => resolve();
          tx.objectStore('attempts').add({ consent_id: consent, request_sha256: 'a'.repeat(64), attempted_at: 1 });
          tx.abort();
        }); db.close();
      }, id(4));
      assert.equal(count(id(4)), 0);
      await page.close();
      const next = await context.newPage(); await ready(next, id(4));
      await next.locator('button').click();
      await next.waitForFunction(() => document.body.textContent!.includes('synthetic bounded result'));
      assert.equal(count(id(4)), 1); await next.close();
    });
    await t.test('storage unavailable leaves submit disabled and sends zero requests', async () => {
      const page = await context.newPage();
      await page.addInitScript(() => Object.defineProperty(globalThis, 'indexedDB', { value: undefined }));
      await page.goto(`${origin}/${id(5)}`);
      await page.waitForFunction(() => document.body.textContent!.includes('local storage unavailable'));
      assert.equal(await page.locator('button').isDisabled(), true);
      assert.equal(count(id(5)), 0); await page.close();
    });
    await t.test('JavaScript disabled cannot submit through the normal UI', async () => {
      const noScript = await browser.newContext({ javaScriptEnabled: false });
      await noScript.route('**/*', (route: any) => new URL(route.request().url()).origin === origin
        ? route.continue() : route.abort());
      const page = await noScript.newPage(); await page.goto(`${origin}/${id(6)}`);
      assert.equal(await page.locator('button').isDisabled(), true);
      await page.keyboard.press('Enter'); assert.equal(count(id(6)), 0);
      await noScript.close();
    });
    await t.test('stored marker includes only non-secret exact binding fields', async () => {
      const page = await context.newPage(); await ready(page, id(7));
      const marker = await page.evaluate(async (consent: string) => {
        const api = await import('/guard.mjs'); const db = await api.openMarkerDatabase(indexedDB);
        const value = await new Promise((resolve, reject) => {
          const request = db.transaction('attempts').objectStore('attempts').get(consent);
          request.onsuccess = () => resolve(request.result); request.onerror = reject;
        }); db.close(); return value;
      }, id(1));
      assert.deepEqual(Object.keys(marker).sort(), ['attempted_at', 'consent_id', 'request_sha256']);
      assert.equal(marker.consent_id, id(1)); assert.equal(marker.request_sha256, 'a'.repeat(64));
      await page.close();
    });
  } finally {
    await context.close(); await browser.close();
    server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
