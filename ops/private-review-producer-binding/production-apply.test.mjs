import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import test from 'node:test';
import { sha256 } from '../managed-inspector-activation/production-apply-lib.mjs';
import { approvalSubject, newApprovalTemplate,
  runProductionApply, validateApproval, validateContractRows,
  canonicalJson } from './production-apply-lib.mjs';

const ROOT = resolve(import.meta.dirname, '../..');
const source = readFileSync(resolve(ROOT,
  'supabase/migrations/20260916190000_content_ops_review_producer_binding.sql'), 'utf8');
const preflightSql = readFileSync(resolve(ROOT,
  'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql'), 'utf8');
const preflightSha256 = sha256(Buffer.from(preflightSql));
const releaseSha = 'a'.repeat(40);
const now = new Date('2026-09-23T15:00:00.000Z');

function approval() {
  const value = newApprovalTemplate({ releaseSha, preflightSha256, now });
  value.approvedBy = 'operator:test';
  value.approvalSubject = approvalSubject(value);
  value.approvalSubjectSha256 = sha256(value.approvalSubject);
  return value;
}

function contract(state) {
  return [{ jsonb_build_object: {
    producer_binding_contract: state, read_only: true, changes: 0, provider_calls: 0,
  } }];
}

function applied() {
  return [{ jsonb_build_object: {
    producer_binding_apply: 'committed', version: '20260916190000',
    migration_sha256: 'dd5dc69d4bc18053a828a8634cd8d603e9282f375ccf21d6b7d96dc29f247ae6',
    provider_calls: 0, runtime_activation: false,
  } }];
}

function response(rows) {
  return new Response(JSON.stringify(rows), { status: 201,
    headers: { 'content-type': 'application/json' } });
}

function fake({ preflight = 'legacy_no_history', postflight = 'corrected_exact_history',
  failSend = false } = {}) {
  const calls = [];
  const journal = { events: [], async append(state, detail) {
    this.events.push({ state, detail });
  } };
  const fetchImpl = async (url, options) => {
    const { query } = JSON.parse(options.body.toString('utf8'));
    const readOnly = url.endsWith('/read-only');
    calls.push({ readOnly, query });
    if (readOnly) {
      return response(contract(calls.length === 5 ? postflight : preflight));
    }
    if (query.startsWith('select current_user::text')) {
      return response([{ current_user: 'postgres', session_user: 'postgres',
        transaction_read_only: 'off' }]);
    }
    if (failSend) throw new Error('simulated_transport_loss');
    return response(applied());
  };
  return { calls, journal, fetchImpl };
}

function args(f, value = approval()) {
  return { approval: value, approvedSubjectSha256: value.approvalSubjectSha256,
    source, preflightSql, fetchImpl: f.fetchImpl, token: 'synthetic-token-1234567890',
    journal: f.journal, releaseSha, now: () => now };
}

test('approval requires exact release, subject hash and a separate matching operator hash', () => {
  const value = approval();
  assert.equal(validateApproval(value, { releaseSha, preflightSha256, now,
    approvedSubjectSha256: value.approvalSubjectSha256 }), value);
  assert.throws(() => validateApproval(value, { releaseSha, preflightSha256, now,
    approvedSubjectSha256: '0'.repeat(64) }), /separate operator approval hash differs/u);
  assert.throws(() => validateApproval(value, { releaseSha: 'b'.repeat(40),
    preflightSha256, now }), /approval release differs/u);
  const template = newApprovalTemplate({ releaseSha, preflightSha256, now });
  assert.throws(() => validateApproval(template, { releaseSha, preflightSha256, now }),
    /approval is a template/u);
});

test('CLI validates canonical approval offline; --apply without operator hash fails closed', () => {
  const liveSha = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: ROOT, encoding: 'utf8' }).trim();
  const value = newApprovalTemplate({ releaseSha: liveSha, preflightSha256 });
  value.approvedBy = 'operator:test';
  value.approvalSubject = approvalSubject(value);
  value.approvalSubjectSha256 = sha256(value.approvalSubject);
  const folder = mkdtempSync(resolve(tmpdir(), 'coineasy-producer-approval-'));
  const path = realpathSync(folder) + '/approval.json';
  try {
    writeFileSync(path, canonicalJson(value), { mode: 0o600 });
    const cli = resolve(import.meta.dirname, 'production-apply.mjs');
    const valid = spawnSync(process.execPath,
      [cli, '--validate', '--approval', path], { cwd: ROOT, encoding: 'utf8' });
    assert.equal(valid.status, 0, valid.stderr);
    assert.equal(JSON.parse(valid.stdout).status, 'offline_validate_only');
    const invalid = spawnSync(process.execPath,
      [cli, '--apply', '--approval', path, '--receipt-root', folder],
      { cwd: ROOT, encoding: 'utf8' });
    assert.notEqual(invalid.status, 0);
    assert.match(invalid.stderr, /separate_operator-approved_subject_SHA-256_required/u);
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
});

test('contract readback rejects unknown wrapper keys and altered fields', () => {
  assert.throws(() => validateContractRows([{
    ...contract('legacy_no_history')[0], hidden: true,
  }], 'legacy_no_history'), /contract row keys changed/u);
  assert.throws(() => validateContractRows(contract('corrected_exact_history'),
    'legacy_no_history'), /Expected values to be strictly deep-equal/u);
});

test('mock production sequence sends one exact write only after two fresh preflights', async () => {
  const f = fake();
  const result = await runProductionApply(args(f));
  assert.equal(result.status, 'corrected_exact_history');
  assert.deepEqual(f.calls.map(c => c.readOnly), [true, false, true, false, true]);
  assert.match(f.calls[3].query, /lock table supabase_migrations\.schema_migrations/u);
  assert.deepEqual(f.journal.events.map(e => e.state), [
    'PACK_VERIFIED', 'PREFLIGHT_VERIFIED', 'WRITE_EXECUTOR_VERIFIED',
    'IMMEDIATE_PREFLIGHT_VERIFIED', 'SEND_INTENT', 'COMMIT_RESPONSE_RECEIVED',
    'POSTFLIGHT_VERIFIED',
  ]);
  assert.equal(f.journal.events.at(-1).detail.runtimeActivationAllowed, false);
});

test('caller mutation after entry cannot change the approved packet', async () => {
  const f = fake();
  const supplied = args(f);
  const originalFetch = supplied.fetchImpl;
  let changed = false;
  supplied.fetchImpl = (...params) => {
    if (!changed) {
      supplied.approval.approvedBy = 'attacker:changed';
      supplied.approval.actions[0] = 'unapproved_action';
      changed = true;
    }
    return originalFetch(...params);
  };
  assert.equal((await runProductionApply(supplied)).status, 'corrected_exact_history');
  assert.equal(f.calls.filter(c => !c.readOnly).length, 2); // executor SELECT + one apply
});

test('wrong preflight cannot reach write endpoint', async () => {
  const f = fake({ preflight: 'corrected_exact_history' });
  await assert.rejects(runProductionApply(args(f)), /Expected values to be strictly deep-equal/u);
  assert.deepEqual(f.calls.map(c => c.readOnly), [true]);
  assert.deepEqual(f.journal.events.map(e => e.state), ['PACK_VERIFIED', 'ABORTED_CLEAN']);
  assert.equal(f.journal.events.at(-1).detail.phase, 'preflight');
});

test('wrong operator hash blocks all I/O', async () => {
  const f = fake();
  const supplied = args(f);
  supplied.approvedSubjectSha256 = '0'.repeat(64);
  await assert.rejects(runProductionApply(supplied),
    /separate operator approval hash differs/u);
  assert.equal(f.calls.length, 0);
  assert.equal(f.journal.events.length, 0);
});

test('postflight drift is committed-unverified and never retries', async () => {
  const f = fake({ postflight: 'legacy_no_history' });
  await assert.rejects(runProductionApply(args(f)), /producer_binding_postflight_unverified/u);
  assert.deepEqual(f.calls.map(c => c.readOnly), [true, false, true, false, true]);
  assert.equal(f.journal.events.at(-1).state, 'COMMITTED_UNVERIFIED');
});

test('lost write acknowledgement performs read-only reconciliation and never retries', async () => {
  const f = fake({ failSend: true });
  await assert.rejects(runProductionApply(args(f)), /producer_binding_apply_outcome_unknown/u);
  assert.deepEqual(f.calls.map(c => c.readOnly), [true, false, true, false, true]);
  assert.equal(f.journal.events.at(-1).state, 'OUTCOME_UNKNOWN');
  assert.equal(f.journal.events.at(-1).detail.reconciliation, 'corrected_exact_history');
  assert.equal(f.journal.events.at(-1).detail.automaticRetryAllowed, false);
});
