/** Exact one-migration production boundary. Network I/O is injected, never implicit. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import {
  canonicalJson, queryManagementApi, sha256, validateWriteExecutorRows,
} from '../managed-inspector-activation/production-apply-lib.mjs';
import {
  buildAtomicProducerBindingSql, MIGRATION_SHA256, MIGRATION_VERSION,
} from './atomic-apply-sql.mjs';

export const PROJECT_REF = 'isuqcqwxpojgzevxfdwr';
export const APPROVAL_SCHEMA = 'coineasy-producer-binding-one-migration-approval@1';
export const APPROVAL_PLACEHOLDER = 'replace-with-approved-actor';
export const REQUIRED_ACTIONS = Object.freeze(['apply_exact_producer_binding_and_history']);
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/u;
const SHA1 = /^[a-f0-9]{40}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const FIELDS = Object.freeze([
  'actions', 'approvalId', 'approvalSubject', 'approvalSubjectSha256', 'approvedAt',
  'approvedBy', 'environment', 'expiresAt', 'genericDbPushAllowed',
  'migrationSha256', 'migrationVersion', 'operationId', 'preflightSha256',
  'projectRef', 'releaseSha', 'runtimeActivationAllowed', 'schemaVersion',
]);

function exactKeys(value, keys, label) {
  assert.equal(value && typeof value === 'object' && !Array.isArray(value), true, `${label} must be an object`);
  assert.deepEqual(Object.keys(value).sort(), [...keys].sort(), `${label} keys changed`);
}

function instant(value) {
  assert.equal(typeof value, 'string');
  const ms = Date.parse(value);
  assert.equal(Number.isFinite(ms), true, 'invalid instant');
  assert.equal(new Date(ms).toISOString(), value, 'instant must be canonical UTC');
  return ms;
}

export function approvalSubject(value) {
  return [
    APPROVAL_SCHEMA,
    `operation_id=${value.operationId}`,
    `approval_id=${value.approvalId}`,
    `approved_by=${value.approvedBy}`,
    `approved_at=${value.approvedAt}`,
    `expires_at=${value.expiresAt}`,
    `environment=${value.environment}`,
    `project_ref=${value.projectRef}`,
    `release_sha=${value.releaseSha}`,
    `migration_version=${value.migrationVersion}`,
    `migration_sha256=${value.migrationSha256}`,
    `preflight_sha256=${value.preflightSha256}`,
    `actions=${value.actions.join(',')}`,
    `generic_db_push_allowed=${value.genericDbPushAllowed}`,
    `runtime_activation_allowed=${value.runtimeActivationAllowed}`,
  ].join('\n');
}

export function newApprovalTemplate({ releaseSha, preflightSha256, now = new Date() }) {
  assert.match(releaseSha, SHA1);
  assert.match(preflightSha256, SHA256);
  const value = {
    actions: [...REQUIRED_ACTIONS], approvalId: randomUUID(), approvalSubject: '',
    approvalSubjectSha256: '', approvedAt: now.toISOString(),
    approvedBy: APPROVAL_PLACEHOLDER, environment: 'production',
    expiresAt: new Date(now.getTime() + 60 * 60_000).toISOString(),
    genericDbPushAllowed: false, migrationSha256: MIGRATION_SHA256,
    migrationVersion: MIGRATION_VERSION, operationId: randomUUID(),
    preflightSha256, projectRef: PROJECT_REF, releaseSha,
    runtimeActivationAllowed: false, schemaVersion: APPROVAL_SCHEMA,
  };
  value.approvalSubject = approvalSubject(value);
  value.approvalSubjectSha256 = sha256(value.approvalSubject);
  return value;
}

export function validateApproval(value, { releaseSha, preflightSha256, now = new Date(), approvedSubjectSha256 } = {}) {
  exactKeys(value, FIELDS, 'approval');
  assert.equal(value.schemaVersion, APPROVAL_SCHEMA);
  assert.match(value.operationId, UUID);
  assert.match(value.approvalId, UUID);
  assert.match(value.releaseSha, SHA1);
  assert.equal(value.releaseSha, releaseSha, 'approval release differs from checkout');
  assert.equal(value.preflightSha256, preflightSha256, 'approval preflight differs from checkout');
  assert.equal(value.projectRef, PROJECT_REF);
  assert.equal(value.environment, 'production');
  assert.equal(value.migrationVersion, MIGRATION_VERSION);
  assert.equal(value.migrationSha256, MIGRATION_SHA256);
  assert.deepEqual(value.actions, REQUIRED_ACTIONS);
  assert.equal(value.genericDbPushAllowed, false);
  assert.equal(value.runtimeActivationAllowed, false);
  assert.match(value.approvedBy, /^[A-Za-z0-9@._:-]{3,120}$/u);
  assert.notEqual(value.approvedBy, APPROVAL_PLACEHOLDER, 'approval is a template');
  assert.equal(typeof value.approvalSubject, 'string');
  assert.ok(Buffer.byteLength(value.approvalSubject) <= 2048);
  assert.equal(value.approvalSubject, approvalSubject(value));
  assert.match(value.approvalSubjectSha256, SHA256);
  assert.equal(value.approvalSubjectSha256, sha256(value.approvalSubject));
  if (approvedSubjectSha256 !== undefined) {
    assert.match(approvedSubjectSha256, SHA256);
    assert.equal(approvedSubjectSha256, value.approvalSubjectSha256,
      'separate operator approval hash differs');
  }
  const approvedAt = instant(value.approvedAt);
  const expiresAt = instant(value.expiresAt);
  assert.ok(expiresAt > approvedAt && expiresAt - approvedAt <= 2 * 60 * 60_000,
    'approval window invalid');
  assert.ok(now.getTime() >= approvedAt - 60_000 && now.getTime() < expiresAt,
    'approval inactive or expired');
  return value;
}

export function validateContractRows(rows, expected) {
  assert.equal(Array.isArray(rows), true);
  assert.equal(rows.length, 1);
  const row = rows[0];
  exactKeys(row, row && Object.hasOwn(row, 'jsonb_build_object')
    ? ['jsonb_build_object'] : row && Object.hasOwn(row, 'receipt')
      ? ['receipt'] : ['producer_binding_contract', 'read_only', 'changes', 'provider_calls'], 'contract row');
  const receipt = row.jsonb_build_object ?? row.receipt ?? row;
  exactKeys(receipt, ['producer_binding_contract', 'read_only', 'changes', 'provider_calls'], 'contract');
  assert.deepEqual(receipt, {
    producer_binding_contract: expected, read_only: true, changes: 0, provider_calls: 0,
  });
  return receipt;
}

export function validateApplyRows(rows) {
  assert.equal(Array.isArray(rows), true);
  assert.equal(rows.length, 1);
  const row = rows[0];
  exactKeys(row, row && Object.hasOwn(row, 'jsonb_build_object')
    ? ['jsonb_build_object'] : row && Object.hasOwn(row, 'receipt')
      ? ['receipt'] : ['producer_binding_apply', 'version', 'migration_sha256',
        'provider_calls', 'runtime_activation'], 'apply row');
  const receipt = row.jsonb_build_object ?? row.receipt ?? row;
  exactKeys(receipt, ['producer_binding_apply', 'version', 'migration_sha256',
    'provider_calls', 'runtime_activation'], 'apply');
  assert.deepEqual(receipt, {
    producer_binding_apply: 'committed', version: MIGRATION_VERSION,
    migration_sha256: MIGRATION_SHA256, provider_calls: 0,
    runtime_activation: false,
  });
  return receipt;
}

function safeError(error) {
  return String(error?.message ?? 'unknown').replace(/[^A-Za-z0-9_.:-]/gu, '_').slice(0, 120);
}

/** No automatic retry under any outcome, including a legacy-looking readback. */
export async function runProductionApply({
  approval, approvedSubjectSha256, source, preflightSql, fetchImpl, token,
  journal, releaseSha, now = () => new Date(),
}) {
  const pinnedApproval = Object.freeze({ ...approval,
    actions: Object.freeze([...approval.actions]) });
  const preflightSha256 = sha256(Buffer.from(preflightSql));
  const current = () => typeof now === 'function' ? now() : now;
  const check = () => validateApproval(pinnedApproval, {
    releaseSha, preflightSha256, now: current(), approvedSubjectSha256,
  });
  check();
  const atomicSql = buildAtomicProducerBindingSql(source);
  await journal.append('PACK_VERIFIED', {
    releaseSha, projectRef: PROJECT_REF, migrationSha256: MIGRATION_SHA256,
    preflightSha256, runtimeActivationAllowed: false,
  });
  const readContract = async (expected) => {
    const result = await queryManagementApi({ fetchImpl, token,
      query: preflightSql, readOnly: true });
    validateContractRows(result.parsed, expected);
    return result;
  };
  const executorSql = "select current_user::text as current_user,session_user::text as session_user,pg_catalog.current_setting('transaction_read_only') as transaction_read_only";
  let phase = 'preflight';
  try {
    check();
    const first = await readContract('legacy_no_history');
    await journal.append('PREFLIGHT_VERIFIED', { ...first.meta, preflightSha256 });
    phase = 'write_executor_probe';
    check();
    const executor = await queryManagementApi({ fetchImpl, token,
      query: executorSql, readOnly: false });
    validateWriteExecutorRows(executor.parsed);
    await journal.append('WRITE_EXECUTOR_VERIFIED', executor.meta);
    phase = 'immediate_preflight';
    check();
    const immediate = await readContract('legacy_no_history');
    await journal.append('IMMEDIATE_PREFLIGHT_VERIFIED', { ...immediate.meta, preflightSha256 });
    phase = 'pre_send';
    check();
  } catch (error) {
    await journal.append('ABORTED_CLEAN', { phase, error: safeError(error),
      automaticRetryAllowed: false, runtimeActivationAllowed: false });
    throw error;
  }
  const attemptId = randomUUID();
  const requestQuerySha256 = sha256(Buffer.from(atomicSql));
  await journal.append('SEND_INTENT', {
    attemptId, requestQuerySha256, migrationSha256: MIGRATION_SHA256,
    automaticRetryAllowed: false, runtimeActivationAllowed: false,
  });
  try {
    check();
    const sent = await queryManagementApi({ fetchImpl, token, query: atomicSql,
      readOnly: false, attemptId, timeoutMs: 120_000 });
    validateApplyRows(sent.parsed);
    await journal.append('COMMIT_RESPONSE_RECEIVED', {
      ...sent.meta, migrationSha256: MIGRATION_SHA256,
      runtimeActivationAllowed: false,
    });
  } catch (error) {
    let reconciliation = 'unobserved';
    try {
      const result = await queryManagementApi({ fetchImpl, token,
        query: preflightSql, readOnly: true });
      for (const state of ['legacy_no_history', 'corrected_exact_history']) {
        try { validateContractRows(result.parsed, state); reconciliation = state; break; }
        catch { /* only an exact classified readback is accepted */ }
      }
    } catch { /* unknown outcome remains terminal */ }
    await journal.append('OUTCOME_UNKNOWN', {
      attemptId, error: safeError(error), reconciliation,
      automaticRetryAllowed: false, runtimeActivationAllowed: false,
    });
    throw new Error('producer_binding_apply_outcome_unknown');
  }
  try {
    const post = await readContract('corrected_exact_history');
    await journal.append('POSTFLIGHT_VERIFIED', {
      ...post.meta, preflightSha256, runtimeActivationAllowed: false,
    });
  } catch (error) {
    await journal.append('COMMITTED_UNVERIFIED', {
      error: safeError(error), automaticRetryAllowed: false,
      runtimeActivationAllowed: false,
    });
    throw new Error('producer_binding_postflight_unverified');
  }
  return { status: 'corrected_exact_history', operationId: pinnedApproval.operationId,
    migrationVersion: MIGRATION_VERSION, migrationSha256: MIGRATION_SHA256,
    runtimeActivationAllowed: false, providerCalls: 0 };
}

export { canonicalJson };
