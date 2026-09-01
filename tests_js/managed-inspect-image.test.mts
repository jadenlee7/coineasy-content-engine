import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

test('managed inspect packaging has a minimal separate copy list and defaults OFF', () => {
  const docker = readFileSync(new URL('../Dockerfile.managed-inspect', import.meta.url), 'utf8');
  const ignore = readFileSync(new URL('../Dockerfile.managed-inspect.dockerignore', import.meta.url), 'utf8');
  assert.match(docker, /FROM node:24\.11\.1-slim/);
  assert.match(docker, /MANAGED_INSPECT_ENABLED=false/);
  assert.match(docker, /USER node/);
  assert.match(docker, /build-sha\.txt/);
  assert.match(docker, /mode: 0o444/);
  assert.deepEqual(docker.split('\n').filter((line) => line.startsWith('COPY ')), [
    'COPY tools/managed-telegram-inspect/*.mjs ./tools/managed-telegram-inspect/',
    'COPY scripts/lib/telegram-resolution-inspect.mjs ./scripts/lib/telegram-resolution-inspect.mjs',
  ]);
  assert.equal(ignore.split('\n')[0], '**');
  assert.ok(!ignore.includes('!netlify') && !ignore.includes('!core') && !ignore.includes('!api'));
  const netlify = readFileSync(new URL('../netlify.toml', import.meta.url), 'utf8');
  assert.ok(!netlify.includes('managed-inspect'), 'no existing-site deployment integration');
});
