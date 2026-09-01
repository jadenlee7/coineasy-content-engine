// First-party only. Contains no Auth SDK, tokens, refresh or remote script imports.
// This is one-attempt per origin/profile storage, NOT server/global exactly-once.
export function openMarkerDatabase(indexedDB) {
  return new Promise((resolve, reject) => {
    if (!indexedDB) { reject(new Error('local_marker_unavailable')); return; }
    const request = indexedDB.open('managed-telegram-inspect-attempts', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('attempts', { keyPath: 'consent_id' });
    request.onerror = () => reject(new Error('local_marker_unavailable'));
    request.onblocked = () => reject(new Error('local_marker_unavailable'));
    request.onsuccess = () => resolve(request.result);
  });
}

export function commitAttempt(db, consentId, requestHash, now = Date.now()) {
  return new Promise((resolve, reject) => {
    if (!/^[0-9a-f-]{36}$/.test(consentId) || !/^[0-9a-f]{64}$/.test(requestHash)) {
      reject(new Error('local_marker_rejected')); return;
    }
    try {
      const transaction = db.transaction('attempts', 'readwrite', { durability: 'strict' });
      transaction.oncomplete = () => resolve();
      transaction.onabort = () => reject(new Error('local_marker_rejected'));
      transaction.onerror = () => reject(new Error('local_marker_rejected'));
      // add, never put/delete/clear. Success of add is NOT the commit boundary.
      transaction.objectStore('attempts').add({ consent_id: consentId,
        request_sha256: requestHash, attempted_at: now });
    } catch { reject(new Error('local_marker_rejected')); }
  });
}

export async function installGuard(document, indexedDB) {
  const form = document.querySelector('[data-inspect-form]');
  if (!form) return;
  const button = form.querySelector('button');
  const status = document.querySelector('[data-attempt-status]');
  let busy = false;
  try {
    const db = await openMarkerDatabase(indexedDB);
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (busy) return;
      busy = true; button.disabled = true;
      try {
        await commitAttempt(db, form.dataset.consentId, form.dataset.requestHash);
        form.elements.namedItem('attempt_marker_committed').value = '1';
        // Exactly one application fetch invocation, not a wire-level promise:
        // browser transport can replay POST after a connection reset. The live
        // server-session upstream-inspect-RPC attempt guard is also required. Never add
        // a retry loop/navigation or compensate by removing the marker.
        const target = new URL(form.action, document.location.href);
        if (target.origin !== document.location.origin || target.pathname !== '/inspect'
            || target.search || target.hash) throw new Error('local_target_rejected');
        const response = await fetch(target.href, { method: 'POST',
          body: new URLSearchParams(new FormData(form)), credentials: 'same-origin',
          redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(15000) });
        if (!response.ok || !(response.headers.get('content-type') ?? '').startsWith('application/json')) throw new Error('inspect_result_unknown');
        const reader = response.body.getReader(); const chunks = []; let size = 0;
        while (true) {
          const { done, value } = await reader.read(); if (done) break;
          size += value.byteLength;
          if (size > 65536) { await reader.cancel(); throw new Error('inspect_result_unknown'); }
          chunks.push(value);
        }
        const bytes = new Uint8Array(size); let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
        const result = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
        const output = document.createElement('pre');
        output.textContent = JSON.stringify(result, null, 2);
        status.textContent = 'Bounded inspection result. No send, approval or resolution. Do not retry.';
        status.after(output);
      } catch { status.textContent = 'BLOCK: attempt blocked or outcome unknown. Any committed marker is retained. Do not retry.'; }
    });
    button.disabled = false;
  } catch { status.textContent = 'BLOCK: local storage unavailable; no inspect request was sent.'; }
}

if (typeof document !== 'undefined') installGuard(document, globalThis.indexedDB);
