import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

for (const filename of ['verify_final_queue_signup_free_local.mjs',
  'verify_final_queue_signup_free_concurrency_local.mjs']) {
const script = fileURLToPath(new URL(`../scripts/${filename}`, import.meta.url));
for (const [label, args, env, expected] of [
  ['requires explicit local flag', [], {}, 'explicit --local-only required'],
  ['rejects network hosts', ['--local-only'], {
    PGHOST: 'example.supabase.co', PGPORT: '5432', PGUSER: 'postgres',
    PGDATABASE: 'coineasy_final_signup_free_test',
  }, 'explicit disposable UNIX-socket test database required'],
  ['rejects ordinary database names', ['--local-only'], {
    PGHOST: '/private/tmp', PGPORT: '55438', PGUSER: 'postgres', PGDATABASE: 'postgres',
  }, 'explicit disposable UNIX-socket test database required'],
] as const) {
  test(`${filename}: ${label}`, () => {
    const result = spawnSync(process.execPath, [script, ...args], {
      // No PATH: reaching psql would fail, and none of these cases may reach it.
      env: { ...env }, encoding: 'utf8', timeout: 5000,
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, new RegExp(expected));
    assert.equal(result.stdout, '');
  });
}
}
