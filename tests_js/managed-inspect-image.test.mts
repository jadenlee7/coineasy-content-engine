import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

test('managed inspect Railway manifest selects only the isolated default-OFF service', () => {
  const config = JSON.parse(readFileSync(
    new URL('../railway.managed-inspect.json', import.meta.url), 'utf8',
  ));
  assert.deepEqual(config, {
    $schema: 'https://railway.com/railway.schema.json',
    build: {
      builder: 'DOCKERFILE',
      dockerfilePath: 'Dockerfile.managed-inspect',
      watchPatterns: [
        '/Dockerfile.managed-inspect',
        '/Dockerfile.managed-inspect.dockerignore',
        '/tools/managed-telegram-inspect/auth.mjs',
        '/tools/managed-telegram-inspect/browser-guard.mjs',
        '/tools/managed-telegram-inspect/config.mjs',
        '/tools/managed-telegram-inspect/server.mjs',
        '/scripts/lib/telegram-resolution-inspect.mjs',
        '/railway.managed-inspect.json',
      ],
    },
    deploy: {
      preDeployCommand: null,
      startCommand: null,
      healthcheckPath: null,
      cronSchedule: null,
      restartPolicyType: 'ON_FAILURE',
      restartPolicyMaxRetries: 10,
    },
  });
  assert.equal(config.deploy.healthcheckPath, null, 'OFF mode intentionally returns HTTP 503');
  assert.equal(config.deploy.preDeployCommand, null, 'no Auth or database pre-deploy call');
  assert.equal(config.deploy.cronSchedule, null, 'managed inspect is not a scheduled worker');
  assert.equal(config.deploy.startCommand, null, 'the pinned image CMD is the only execution source');
});

test('managed inspect packaging has a minimal separate copy list and defaults OFF', () => {
  const docker = readFileSync(new URL('../Dockerfile.managed-inspect', import.meta.url), 'utf8');
  const ignore = readFileSync(new URL('../Dockerfile.managed-inspect.dockerignore', import.meta.url), 'utf8');
  assert.match(docker, /FROM node:24\.11\.1-slim/);
  assert.equal((docker.match(/MANAGED_INSPECT_ENABLED=false/g) ?? []).length, 1);
  assert.doesNotMatch(docker, /MANAGED_INSPECT_ENABLED=true/);
  assert.match(docker, /USER node/);
  assert.match(docker, /build-sha\.txt/);
  assert.match(docker, /mode: 0o444/);
  assert.deepEqual(docker.split('\n').filter((line) => line.startsWith('COPY ')), [
    'COPY tools/managed-telegram-inspect/*.mjs ./tools/managed-telegram-inspect/',
    'COPY scripts/lib/telegram-resolution-inspect.mjs ./scripts/lib/telegram-resolution-inspect.mjs',
  ]);
  assert.deepEqual(ignore.trimEnd().split('\n'), [
    '**',
    '!Dockerfile.managed-inspect',
    '!tools/',
    '!tools/managed-telegram-inspect/',
    '!tools/managed-telegram-inspect/*.mjs',
    '!scripts/',
    '!scripts/lib/',
    '!scripts/lib/telegram-resolution-inspect.mjs',
  ]);
  assert.deepEqual(
    readdirSync(new URL('../tools/managed-telegram-inspect/', import.meta.url))
      .filter((name) => name.endsWith('.mjs')).sort(),
    ['auth.mjs', 'browser-guard.mjs', 'config.mjs', 'server.mjs'],
  );
  const netlify = readFileSync(new URL('../netlify.toml', import.meta.url), 'utf8');
  assert.doesNotMatch(netlify.toLowerCase(), /managed(?:-|_)inspect|managed(?:-|_)telegram(?:-|_)inspect/,
    'no existing-site deployment integration');
});
