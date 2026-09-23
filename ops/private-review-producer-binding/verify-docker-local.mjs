/** Disposable PostgreSQL proof. No host port, network, production or provider I/O. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { buildAtomicProducerBindingSql, CORRECTED_BODY_SHA256 } from './atomic-apply-sql.mjs';

if (process.argv.length !== 3 || !['--pg16', '--pg17'].includes(process.argv[2])
    || !existsSync('supabase/tests/content_ops_review_outbox.bootstrap.sql')) {
  throw Error('explicit --pg16 or --pg17 from repository root required');
}
const version = process.argv[2] === '--pg17' ? '17.6' : '16.13';
const name = `coineasy-producer-${randomUUID().slice(0, 12)}`;
const env = { PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'C' };
let started = false;
let phase = 'start';

function docker(args, { input, allowFailure = false } = {}) {
  const result = spawnSync('docker', args, { env, encoding: 'utf8', timeout: 90_000, input });
  if (!allowFailure && result.status !== 0) {
    throw Error(`disposable producer proof failed at ${phase}: ${String(result.stderr ?? '').slice(-1000)}`);
  }
  return result;
}

function sql(db, statement, { allowFailure = false } = {}) {
  phase = 'bounded SQL';
  return docker(['exec', '-i', '-u', 'postgres', name, 'psql', '-X', '-qAt',
    '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql', '-U', 'postgres', '-d', db],
  { input: statement, allowFailure });
}

function sqlFile(db, file, { allowFailure = false } = {}) {
  phase = file;
  return docker(['exec', '-u', 'postgres', name, 'psql', '-X', '-qAt',
    '-v', 'ON_ERROR_STOP=1', '-h', '/var/run/postgresql', '-U', 'postgres',
    '-d', db, '-f', `/repo/${file}`], { allowFailure });
}

function contract(db, expected) {
  const result = sqlFile(db,
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql');
  assert.deepEqual(JSON.parse(result.stdout.trim()), {
    producer_binding_contract: expected, read_only: true,
    changes: 0, provider_calls: 0,
  });
}

function bootstrap(db) {
  sqlFile(db, 'supabase/tests/content_ops_review_outbox.bootstrap.sql');
  sql(db, `create schema supabase_migrations;
    create table supabase_migrations.schema_migrations(
      version text primary key, statements text[], name text, created_by text,
      idempotency_key text unique, rollback text[]);`);
  sqlFile(db, 'supabase/migrations/20260906100000_content_ops_review_outbox.sql');
  contract(db, 'legacy_no_history');
}

try {
  const mount = `type=bind,source=${process.cwd()},target=/repo,readonly`;
  docker(['run', '--detach', '--rm', '--network', 'none', '--name', name,
    '--mount', mount, '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
    '-e', 'POSTGRES_INITDB_ARGS=--no-locale -E UTF8', `postgres:${version}`]);
  started = true;
  let ready = false;
  for (let attempt = 0; attempt < 60; attempt++) {
    const logs = docker(['logs', name], { allowFailure: true });
    const check = docker(['exec', '-u', 'postgres', name, 'pg_isready',
      '-h', '/var/run/postgresql', '-U', 'postgres'], { allowFailure: true });
    if (logs.status === 0 &&
        (logs.stdout + logs.stderr).includes('PostgreSQL init process complete') &&
        check.status === 0) { ready = true; break; }
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  if (!ready) throw Error('disposable database not ready');

  bootstrap('postgres');
  const source = readFileSync(
    'supabase/migrations/20260916190000_content_ops_review_producer_binding.sql', 'utf8');
  const atomicSql = buildAtomicProducerBindingSql(source);
  sql('postgres', 'alter table supabase_migrations.schema_migrations add column unsafe_required text not null;');
  const unsafeContract = sqlFile('postgres',
    'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql',
    { allowFailure: true });
  if (unsafeContract.status === 0 ||
      !unsafeContract.stderr.includes('producer_binding_contract_history_column_mismatch')) {
    throw Error('unfillable history column passed the read-only contract');
  }
  const unsafeApply = sql('postgres', atomicSql, { allowFailure: true });
  if (unsafeApply.status === 0 ||
      !unsafeApply.stderr.includes('producer_binding_apply_precondition_mismatch')) {
    throw Error('unfillable history column passed the atomic precondition');
  }
  sql('postgres', 'alter table supabase_migrations.schema_migrations drop column unsafe_required;');
  contract('postgres', 'legacy_no_history');
  sql('postgres', atomicSql);
  contract('postgres', 'corrected_exact_history');
  const replay = sql('postgres', atomicSql, { allowFailure: true });
  if (replay.status === 0 || !replay.stderr.includes('producer_binding_apply_precondition_mismatch')) {
    throw Error('duplicate migration was not rejected');
  }

  sql('postgres', 'create database rollback_probe;');
  bootstrap('rollback_probe');
  const brokenHistory = atomicSql.replace(
    'insert into supabase_migrations.schema_migrations(version,name,statements)',
    'insert into supabase_migrations.absent_table(version,name,statements)');
  if (brokenHistory === atomicSql || sql('rollback_probe', brokenHistory,
    { allowFailure: true }).status === 0) throw Error('history-failure probe failed');
  contract('rollback_probe', 'legacy_no_history');
  const brokenPost = atomicSql.replace(CORRECTED_BODY_SHA256, '0'.repeat(64));
  const result = sql('rollback_probe', brokenPost, { allowFailure: true });
  if (brokenPost === atomicSql || result.status === 0 ||
      !result.stderr.includes('producer_binding_apply_postcondition_mismatch')) {
    throw Error('postcondition-failure probe failed');
  }
  contract('rollback_probe', 'legacy_no_history');
  process.stdout.write(`${JSON.stringify({ postgres: version, atomicCommit: true,
    exactHistory: true, duplicateRejected: true, rollbackOnFailure: true,
    providerCalls: 0, productionCalls: 0 })}\n`);
} finally {
  if (started) docker(['rm', '--force', name], { allowFailure: true });
  process.stdout.write(`${JSON.stringify({ disposableContainerRemoved: true })}\n`);
}
