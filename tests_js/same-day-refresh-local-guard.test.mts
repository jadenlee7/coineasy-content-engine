import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const script = fileURLToPath(new URL('../scripts/verify_official_x_same_day_refresh_local.mjs', import.meta.url));
const source = readFileSync(script, 'utf8');

test('refresh SQL harness rejects unspecified, live-URL and existing-DB targets before initialization', () => {
  for (const args of [[], ['--local-only'], ['--local-only', '--pg16', 'https://example.invalid/db'],
    ['--database-url', 'https://example.invalid/db'], ['--local-only', '--pg15']]) {
    const result = spawnSync(process.execPath, [script, ...args], {
      env: { PATH: process.env.PATH, HOME: process.env.HOME, PGHOST: 'example.invalid' },
      encoding: 'utf8', timeout: 5000,
    });
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /explicit --local-only --pg16 or --pg17 required/);
    assert.equal(result.stdout, '');
  }
});

test('refresh SQL harness scrubs credentials and only starts its own Unix-socket cluster', () => {
  assert.match(source, /const env = \{ PATH: process\.env\.PATH, HOME: process\.env\.HOME, LANG: 'C', LC_ALL: 'C' \}/);
  assert.doesNotMatch(source, /\.\.\.process\.env|process\.env\.(?:PG|DATABASE|SUPABASE|STUDIO|TELEGRAM)/);
  assert.match(source, /-h '' -k \$\{dir\}/);
  assert.match(source, /mkdtempSync/);
  assert.match(source, /if \(stopped\) rmSync\(dir, \{ recursive: true \}\)/);
  assert.match(source, /productionCalls: 0, providerCalls: 0, telegramCalls: 0, hostedProof: false/);
});
