import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { chmod, open, readFile, lstat } from "node:fs/promises";
import { resolve } from "node:path";

import { validateMigrationBytes, validatePack } from "./validate-pack.mjs";

export const PROJECT_REF = "isuqcqwxpojgzevxfdwr";
export const API_ORIGIN = "https://api.supabase.com";
export const APPROVAL_SCHEMA = "coineasy-managed-inspector-production-approval@1";
export const RECEIPT_SCHEMA = "coineasy-supabase-custom-apply-receipt@1";
export const APPROVAL_ACTOR_PLACEHOLDER = "replace-with-approved-actor";
export const EXPECTED_CHECKS = Object.freeze({
  preflight: Object.freeze({
    pack: "managed-inspector-production-preflight@1",
    ids: Object.freeze([
      "read_only_executor_exact",
      "transaction_read_only_on",
      "server_version_pg17",
      "required_schemas_present",
      "required_roles_present",
      "read_only_executor_data_capability",
      "postgres_definer_prerequisite",
      "required_auth_columns_present",
      "required_auth_columns_missing",
      "required_auth_column_types_allowed",
      "required_auth_column_type_mismatches",
      "required_base_relations_present",
      "required_base_functions_present",
      "target_migration_rows_absent",
      "target_role_absent",
      "authenticator_platform_admin_descendants_exact",
      "public_execute_on_existing_public_functions_zero",
      "public_execute_on_existing_private_functions_zero",
      "public_relation_privileges_zero",
      "public_column_privileges_zero",
      "public_sequence_privileges_zero",
      "public_schema_privileges_compatible",
      "target_relations_absent",
      "target_functions_absent",
      "target_function_name_or_overload_collisions_absent",
      "target_triggers_absent",
    ]),
  }),
  intermediate: Object.freeze({
    pack: "managed-inspector-production-intermediate@1",
    ids: Object.freeze([
      "execution_role_read_only",
      "transaction_read_only_on",
      "execution_role_can_observe_forced_rls_rows",
      "ordinary_roles_exist_exact",
      "first_migration_history_exact",
      "second_migration_history_absent",
      "target_role_absent",
      "target_tables_exact",
      "target_relations_no_unexpected_object",
      "target_tables_owned_by_postgres",
      "target_tables_rls_forced",
      "target_table_acl_exact_owner_only",
      "target_column_acl_inventory_zero",
      "target_functions_exact",
      "target_functions_no_unexpected_overload",
      "target_functions_owned_by_postgres",
      "target_function_acl_exact_owner_only",
      "target_function_security_and_config_exact",
      "target_table_triggers_exact",
      "target_tables_zero_rows",
      "public_execute_on_entrypoints_zero",
      "ordinary_roles_execute_on_entrypoints_zero",
    ]),
  }),
  postflight: Object.freeze({
    pack: "managed-inspector-production-postflight@1",
    ids: Object.freeze([
      "read_only_executor_exact",
      "transaction_read_only_on",
      "observed_exposed_schemas_present",
      "migration_rows_exact",
      "target_role_exists_once",
      "target_role_attributes_exact",
      "target_role_membership_cardinality",
      "authenticator_membership_exact",
      "target_role_has_no_parent_membership",
      "target_role_has_no_transitive_parent_membership",
      "target_role_direct_members_exact",
      "target_role_transitive_members_exact",
      "public_schema_boundary",
      "private_schema_boundary",
      "unexpected_schema_privileges_zero",
      "target_tables_exact",
      "target_relations_no_unexpected_object",
      "target_table_acls_explicit",
      "target_table_acl_exact_allowlist",
      "target_column_acl_inventory_zero",
      "target_tables_rls_forced",
      "target_tables_owned_by_postgres",
      "target_table_triggers_exact",
      "target_functions_exact",
      "target_functions_no_unexpected_overload",
      "target_function_acls_explicit",
      "target_function_acl_exact_allowlist",
      "target_function_security_and_config_exact",
      "target_functions_owned_by_postgres",
      "public_entrypoints_security_definer",
      "effective_exposed_functions_exactly_three",
      "effective_private_functions_zero",
      "relation_privileges_zero",
      "column_privileges_zero",
      "sequence_privileges_zero",
      "owned_objects_zero",
      "default_acl_grants_to_target_role_zero",
      "public_execute_on_target_functions_zero",
      "ordinary_roles_execute_on_target_functions_zero",
    ]),
  }),
});

const APPROVAL_KEYS = Object.freeze([
  "actions",
  "approvalId",
  "approvalSubject",
  "approvalSubjectSha256",
  "approvedAt",
  "approvedBy",
  "canonicalSetSha256",
  "environment",
  "expiresAt",
  "genericDbPushAllowed",
  "operationId",
  "projectRef",
  "releaseSha",
  "runtimeActivationAllowed",
  "schemaVersion",
]);
const REQUIRED_ACTIONS = Object.freeze([
  "apply_exact_migrations",
  "read_only_validate",
  "register_exact_history",
]);
const MAX_APPROVAL_WINDOW_MS = 2 * 60 * 60 * 1000;
const MAX_RESPONSE_BYTES = 512 * 1024;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const SHA1 = /^[a-f0-9]{40}$/u;
const CHECK_ROW_KEYS = Object.freeze([
  "check_id",
  "custom_apply_receipt_required",
  "exact_migration_bytes_proven",
  "expected",
  "full_history_not_reconciled",
  "generic_db_push_allowed",
  "observed",
  "observed_byte_length",
  "observed_sha256",
  "pack",
  "passed",
]);

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])]),
    );
  }
  return value;
}

export function canonicalJson(value) {
  return `${JSON.stringify(canonicalValue(value), null, 2)}\n`;
}

function deepFreeze(value) {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

export function parseCanonicalJson(raw, label = "JSON") {
  const value = JSON.parse(raw);
  assert.equal(raw, canonicalJson(value), `${label} must be deterministic canonical JSON`);
  return value;
}

function exactKeys(value, expected, label) {
  assert.deepEqual(Object.keys(value).sort(), [...expected].sort(), `${label} keys changed`);
}

function parseInstant(value, label) {
  assert.equal(typeof value, "string", `${label} must be a string`);
  const milliseconds = Date.parse(value);
  assert.equal(Number.isFinite(milliseconds), true, `${label} is not an instant`);
  assert.equal(new Date(milliseconds).toISOString(), value, `${label} is not canonical UTC`);
  return milliseconds;
}

export function boundedApprovalSubject(approval) {
  return [
    "coineasy-managed-inspector-production-apply@1",
    `approval_schema=${approval.schemaVersion}`,
    `operation_id=${approval.operationId}`,
    `approval_id=${approval.approvalId}`,
    `approved_by=${approval.approvedBy}`,
    `approved_at=${approval.approvedAt}`,
    `expires_at=${approval.expiresAt}`,
    `environment=${approval.environment}`,
    `project_ref=${approval.projectRef}`,
    `release_sha=${approval.releaseSha}`,
    `canonical_set_sha256=${approval.canonicalSetSha256}`,
    `actions=${approval.actions.join(",")}`,
    `generic_db_push_allowed=${approval.genericDbPushAllowed}`,
    `runtime_activation_allowed=${approval.runtimeActivationAllowed}`,
  ].join("\n");
}

export function validateApproval(approval, manifest, releaseSha, now = new Date()) {
  assert.equal(approval && typeof approval === "object" && !Array.isArray(approval), true);
  exactKeys(approval, APPROVAL_KEYS, "approval");
  assert.equal(approval.schemaVersion, APPROVAL_SCHEMA);
  assert.match(approval.operationId, UUID);
  assert.match(approval.approvalId, UUID);
  assert.equal(typeof approval.approvalSubject, "string");
  assert.ok(Buffer.byteLength(approval.approvalSubject, "utf8") <= 2048);
  assert.match(approval.approvalSubjectSha256, SHA256);
  assert.match(approval.releaseSha, SHA1);
  assert.equal(approval.releaseSha, releaseSha, "approval release SHA does not match exact checkout");
  assert.equal(approval.projectRef, PROJECT_REF);
  assert.equal(approval.environment, "production");
  assert.equal(approval.canonicalSetSha256, manifest.canonicalSetSha256);
  assert.deepEqual(approval.actions, REQUIRED_ACTIONS);
  assert.equal(approval.genericDbPushAllowed, false, "genericDbPushAllowed must remain false");
  assert.equal(approval.runtimeActivationAllowed, false, "runtimeActivationAllowed must remain false");
  assert.match(approval.approvedBy, /^[A-Za-z0-9@._:-]{3,120}$/u);
  assert.notEqual(
    approval.approvedBy,
    APPROVAL_ACTOR_PLACEHOLDER,
    "approvedBy placeholder must be replaced",
  );
  const expectedSubject = boundedApprovalSubject(approval);
  assert.equal(approval.approvalSubject, expectedSubject, "approvalSubject does not match bounded fields");
  assert.equal(
    approval.approvalSubjectSha256,
    sha256(expectedSubject),
    "approvalSubjectSha256 does not bind the bounded subject",
  );
  const approvedAt = parseInstant(approval.approvedAt, "approvedAt");
  const expiresAt = parseInstant(approval.expiresAt, "expiresAt");
  const current = now.getTime();
  assert.ok(expiresAt > approvedAt, "approval expiration must follow approval time");
  assert.ok(expiresAt - approvedAt <= MAX_APPROVAL_WINDOW_MS, "approval window exceeds two hours");
  assert.ok(current >= approvedAt - 60_000, "approval is not active yet");
  assert.ok(current < expiresAt, "approval expired");
  return approval;
}

export function validateApprovedSubjectSha256(approval, approvedSubjectSha256) {
  assert.equal(
    typeof approvedSubjectSha256,
    "string",
    "operator-approved subject SHA-256 is required",
  );
  assert.match(
    approvedSubjectSha256,
    SHA256,
    "operator-approved subject SHA-256 must be exactly 64 lowercase hex characters",
  );
  assert.equal(
    approvedSubjectSha256,
    approval.approvalSubjectSha256,
    "operator-approved subject SHA-256 does not match approval packet",
  );
  return approvedSubjectSha256;
}

export async function loadProductionPack(repositoryRoot) {
  const manifest = await validatePack();
  const packDirectory = resolve(repositoryRoot, "ops/managed-inspector-activation");
  const manifestBytes = await readFile(resolve(packDirectory, "manifest.json"));
  const sql = {};
  for (const name of ["preflight", "intermediate", "postflight"]) {
    const bytes = await readFile(resolve(packDirectory, `${name}.sql`));
    sql[name] = Object.freeze({
      bytes,
      text: new TextDecoder("utf-8", { fatal: true }).decode(bytes),
      byteLength: bytes.byteLength,
      sha256: sha256(bytes),
    });
  }
  const migrations = [];
  for (const migration of manifest.migrations) {
    const absolutePath = resolve(repositoryRoot, migration.path);
    const metadata = await lstat(absolutePath);
    const bytes = await readFile(absolutePath);
    validateMigrationBytes(migration, bytes, metadata);
    migrations.push(Object.freeze({
      ...migration,
      name: migration.path.split("/").at(-1).slice(15, -4),
      absolutePath,
      bytes,
      text: new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    }));
  }
  return Object.freeze({
    manifest,
    manifestSha256: sha256(manifestBytes),
    migrations: Object.freeze(migrations),
    sql: Object.freeze(sql),
  });
}

export async function assertMigrationUnchanged(migration) {
  const metadata = await lstat(migration.absolutePath);
  const bytes = await readFile(migration.absolutePath);
  validateMigrationBytes(migration, bytes, metadata);
  assert.equal(sha256(bytes), migration.sha256);
  return bytes;
}

function boundedProviderRequestId(response) {
  const value = response.headers.get("x-request-id") ?? response.headers.get("cf-ray");
  return value && /^[A-Za-z0-9._:-]{1,160}$/u.test(value) ? value : null;
}

function responseMeta({
  attemptId, status, contentType, bodyBytes, requestBodyBytes,
  sentAt, completedAt, providerRequestId,
}) {
  return Object.freeze({
    attemptId,
    sentAt,
    completedAt,
    httpStatus: status,
    providerRequestId,
    responseContentType: contentType,
    responseByteLength: bodyBytes.byteLength,
    responseSha256: sha256(bodyBytes),
    requestBodyByteLength: requestBodyBytes.byteLength,
    requestBodySha256: sha256(requestBodyBytes),
  });
}

async function boundedResponse(response) {
  const reader = response.body?.getReader();
  if (!reader) return Buffer.alloc(0);
  const chunks = [];
  let length = 0;
  while (true) {
    const item = await reader.read();
    if (item.done) break;
    length += item.value.byteLength;
    if (length > MAX_RESPONSE_BYTES) {
      await reader.cancel();
      throw new Error("provider_response_too_large");
    }
    chunks.push(Buffer.from(item.value));
  }
  return Buffer.concat(chunks);
}

export async function queryManagementApi({
  attemptId = randomUUID(),
  fetchImpl,
  token,
  query,
  readOnly,
  timeoutMs = 60_000,
}) {
  assert.match(attemptId, UUID);
  assert.equal(typeof query, "string");
  assert.ok(query.length > 0);
  assert.equal(typeof token, "string");
  assert.ok(token.length >= 20 && !/\s/u.test(token), "invalid management token");
  const endpoint = readOnly
    ? `${API_ORIGIN}/v1/projects/${PROJECT_REF}/database/query/read-only`
    : `${API_ORIGIN}/v1/projects/${PROJECT_REF}/database/query`;
  const payload = readOnly ? { query } : { query, read_only: false };
  const requestBodyBytes = Buffer.from(JSON.stringify(payload), "utf8");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const sentAt = new Date().toISOString();
  let response;
  try {
    response = await fetchImpl(endpoint, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: requestBodyBytes,
      redirect: "error",
      signal: controller.signal,
    });
    const bodyBytes = await boundedResponse(response);
    const contentType = response.headers.get("content-type") ?? "";
    const meta = responseMeta({
      attemptId,
      status: response.status,
      contentType,
      bodyBytes,
      requestBodyBytes,
      sentAt,
      completedAt: new Date().toISOString(),
      providerRequestId: boundedProviderRequestId(response),
    });
    if (response.status !== 201) {
      const error = new Error(`provider_http_${response.status}`);
      error.outcome = "indeterminate";
      error.meta = meta;
      throw error;
    }
    if (!/^application\/json(?:;|$)/iu.test(contentType)) {
      const error = new Error("provider_content_type_invalid");
      error.outcome = "indeterminate";
      error.meta = meta;
      throw error;
    }
    let parsed;
    try {
      parsed = JSON.parse(bodyBytes.toString("utf8"));
    } catch {
      const error = new Error("provider_json_invalid");
      error.outcome = "indeterminate";
      error.meta = meta;
      throw error;
    }
    return { parsed, meta };
  } catch (error) {
    if (!error.outcome) error.outcome = "indeterminate";
    if (!error.meta) error.meta = Object.freeze({
      attemptId,
      sentAt,
      completedAt: new Date().toISOString(),
      httpStatus: null,
      providerRequestId: response ? boundedProviderRequestId(response) : null,
      responseContentType: null,
      responseByteLength: null,
      responseSha256: null,
      requestBodyByteLength: requestBodyBytes.byteLength,
      requestBodySha256: sha256(requestBodyBytes),
    });
    throw error;
  } finally {
    clearTimeout(timer);
  }
}

export function validateCheckRows(rows, kind) {
  const expected = EXPECTED_CHECKS[kind];
  assert.ok(expected, `unknown check kind ${kind}`);
  assert.equal(Array.isArray(rows), true, `${kind} result must be an array`);
  assert.equal(rows.length, expected.ids.length, `${kind} row count mismatch`);
  const ids = new Set();
  for (const row of rows) {
    assert.equal(row && typeof row === "object" && !Array.isArray(row), true);
    exactKeys(row, CHECK_ROW_KEYS, `${kind} result row`);
    assert.equal(row.pack, expected.pack);
    assert.match(row.check_id, /^[a-z0-9_]+$/u);
    assert.equal(ids.has(row.check_id), false, `${kind} duplicate check id`);
    ids.add(row.check_id);
    assert.equal(row.passed, true, `${kind} check failed: ${row.check_id}`);
    assert.equal(Number.isSafeInteger(row.observed_byte_length), true);
    assert.ok(row.observed_byte_length <= 4096);
    assert.match(row.observed_sha256, SHA256);
    assert.equal(row.generic_db_push_allowed, false);
    assert.equal(row.full_history_not_reconciled, true);
    assert.equal(row.exact_migration_bytes_proven, false);
    assert.equal(row.custom_apply_receipt_required, true);
  }
  assert.deepEqual([...ids].sort(), [...expected.ids].sort(), `${kind} exact check IDs changed`);
  return Object.freeze({ count: rows.length, checkIdsSha256: sha256([...ids].sort().join("\n")) });
}

export function validateWriteExecutorRows(rows) {
  assert.equal(Array.isArray(rows), true);
  assert.equal(rows.length, 1);
  assert.deepEqual(rows[0], {
    current_user: "postgres",
    session_user: "postgres",
    transaction_read_only: "off",
  });
}

function sqlTextFromHex(hex) {
  assert.match(hex, /^[a-f0-9]+$/u);
  return `pg_catalog.convert_from(pg_catalog.decode('${hex}', 'hex'), 'UTF8')`;
}

export function buildHistoryRegistrationSql(migration, ordinal) {
  assert.ok(ordinal === 1 || ordinal === 2);
  assert.match(migration.version, /^\d{14}$/u);
  assert.match(migration.name, /^[a-z0-9_]+$/u);
  assert.match(migration.sha256, SHA256);
  const hex = migration.bytes.toString("hex");
  assert.equal(Buffer.from(hex, "hex").equals(migration.bytes), true);
  const source = sqlTextFromHex(hex);
  const firstVersion = "20260831180000";
  const secondVersion = "20260901120000";
  const catalogGuard = ordinal === 1
    ? `
    if pg_catalog.to_regrole('coineasy_managed_inspector') is not null then
        raise exception 'target role appeared before boundary migration';
    end if;
    if (select count(*) from pg_catalog.pg_class c join pg_catalog.pg_namespace n on n.oid=c.relnamespace
        where n.nspname='private' and c.relname like 'managed_telegram_inspect_%' and c.relkind='r') <> 4 then
        raise exception 'first migration table inventory mismatch';
    end if;
    if (select count(*) from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace
        where n.nspname in ('private','public') and (p.proname like '%managed_telegram_inspect%'
          or p.proname='inspect_managed_telegram_delivery_unknown')) <> 8 then
        raise exception 'first migration function inventory mismatch';
    end if;`
    : `
    if pg_catalog.to_regrole('coineasy_managed_inspector') is null then
        raise exception 'target role missing after boundary migration';
    end if;
    if (select count(*) from pg_catalog.pg_proc p join pg_catalog.pg_namespace n on n.oid=p.pronamespace
        where n.nspname='public' and p.proname in ('managed_telegram_inspect_context',
          'register_managed_telegram_inspect_consent','inspect_managed_telegram_delivery_unknown')
          and pg_catalog.has_function_privilege('coineasy_managed_inspector',p.oid,'EXECUTE')) <> 3 then
        raise exception 'boundary migration execute inventory mismatch';
    end if;`;
  const prerequisite = ordinal === 1
    ? `if exists (select 1 from supabase_migrations.schema_migrations where version in ('${firstVersion}','${secondVersion}')) then
        raise exception 'target migration history is not clean';
    end if;`
    : `if not exists (select 1 from supabase_migrations.schema_migrations
          where version='${firstVersion}' and name='managed_auth_telegram_inspect'
            and pg_catalog.cardinality(statements)=1
            and pg_catalog.encode(extensions.digest(pg_catalog.convert_to(statements[1],'UTF8'),'sha256'),'hex')
              ='61bf61ee4be6993c88d471b0d9b3e3fa2bf1063ba87d1a901cceff2fc953ab46') then
        raise exception 'first migration exact history prerequisite missing';
    end if;
    if exists (select 1 from supabase_migrations.schema_migrations where version='${secondVersion}') then
        raise exception 'second migration history is not clean';
    end if;`;
  return `-- generated by production-apply-lib.mjs; exact source is data, never executed from this statement
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';
set local idle_in_transaction_session_timeout = '30s';
set local search_path = pg_catalog;
lock table supabase_migrations.schema_migrations in share row exclusive mode;
do $history_guard$
begin
    if current_user::text <> 'postgres' or session_user::text <> 'postgres'
       or pg_catalog.current_setting('transaction_read_only') <> 'off' then
        raise exception 'unexpected production write executor';
    end if;
    ${prerequisite}
    if pg_catalog.encode(extensions.digest(pg_catalog.convert_to(${source},'UTF8'),'sha256'),'hex')
       <> '${migration.sha256}' then
        raise exception 'exact migration source digest mismatch';
    end if;${catalogGuard}
end
$history_guard$;
insert into supabase_migrations.schema_migrations(version,name,statements)
values ('${migration.version}','${migration.name}',array[${source}]::text[]);
do $history_assert$
begin
    if (select count(*) from supabase_migrations.schema_migrations
        where version='${migration.version}' and name='${migration.name}'
          and pg_catalog.cardinality(statements)=1
          and pg_catalog.encode(extensions.digest(pg_catalog.convert_to(statements[1],'UTF8'),'sha256'),'hex')
            ='${migration.sha256}') <> 1 then
        raise exception 'exact migration history registration failed';
    end if;
end
$history_assert$;
commit;
select 'coineasy-managed-inspector-history-write@1'::text as schema_version,
       '${migration.version}'::text as version,
       '${migration.name}'::text as history_name,
       current_user::text as write_executor,
       pg_catalog.current_setting('transaction_read_only') as transaction_read_only;
`;
}

export function buildLockedMigrationSql(migration, ordinal) {
  assert.match(migration.version, /^\d{14}$/u);
  assert.match(migration.sha256, SHA256);
  assert.equal(sha256(migration.text), migration.sha256);
  assert.ok(ordinal === 1 || ordinal === 2, "migration ordinal must be 1 or 2");
  const lockName = "coineasy:managed-inspector:production-apply";
  const firstVersion = "20260831180000";
  const secondVersion = "20260901120000";
  const firstSha256 = "61bf61ee4be6993c88d471b0d9b3e3fa2bf1063ba87d1a901cceff2fc953ab46";
  const cleanStateGuard = ordinal === 1
    ? `if exists (
        select 1 from supabase_migrations.schema_migrations
        where version in ('${firstVersion}', '${secondVersion}')
    ) or exists (
        select 1 from pg_catalog.pg_roles where rolname='coineasy_managed_inspector'
    ) or pg_catalog.to_regclass('private.managed_telegram_inspect_releases') is not null
       or pg_catalog.to_regclass('private.managed_telegram_inspect_allowlist') is not null
       or pg_catalog.to_regclass('private.managed_telegram_inspect_consents') is not null
       or pg_catalog.to_regclass('private.managed_telegram_inspect_revocations') is not null then
        raise exception 'first migration fenced state is not clean';
    end if;`
    : `if (select count(*) from supabase_migrations.schema_migrations
        where version='${firstVersion}' and name='managed_auth_telegram_inspect'
          and pg_catalog.cardinality(statements)=1
          and pg_catalog.encode(extensions.digest(pg_catalog.convert_to(statements[1],'UTF8'),'sha256'),'hex')
            ='${firstSha256}') <> 1 then
        raise exception 'first migration exact history prerequisite missing';
    end if;
    if exists (
        select 1 from supabase_migrations.schema_migrations where version='${secondVersion}'
    ) or exists (
        select 1 from pg_catalog.pg_roles where rolname='coineasy_managed_inspector'
    ) then
        raise exception 'second migration fenced state is not clean';
    end if;`;
  return `-- coineasy-managed-inspector-transaction-fenced-migration@2
begin;
do $apply_guard$
begin
    if current_user::text <> 'postgres' or session_user::text <> 'postgres'
       or pg_catalog.current_setting('transaction_read_only') <> 'off' then
        raise exception 'unexpected production write executor';
    end if;
    if not pg_catalog.pg_try_advisory_xact_lock(
        pg_catalog.hashtextextended('${lockName}', 0)
    ) then
        raise exception 'another managed-inspector production apply is active';
    end if;
    ${cleanStateGuard}
end
$apply_guard$;
-- BEGIN EXACT CANONICAL MIGRATION ${migration.version} SHA256 ${migration.sha256}
${migration.text}-- END EXACT CANONICAL MIGRATION ${migration.version}
select 'coineasy-managed-inspector-migration-write@2'::text as schema_version,
       '${migration.version}'::text as version,
       current_user::text as write_executor,
       pg_catalog.current_setting('transaction_read_only') as transaction_read_only;
`;
}

export function validateHistoryWriteRows(rows, migration) {
  assert.equal(Array.isArray(rows), true);
  assert.deepEqual(rows, [{
    schema_version: "coineasy-managed-inspector-history-write@1",
    version: migration.version,
    history_name: migration.name,
    write_executor: "postgres",
    transaction_read_only: "off",
  }]);
}

function safeError(error) {
  const code = typeof error?.message === "string"
    ? error.message.replace(/[^A-Za-z0-9_.:-]/gu, "_").slice(0, 160)
    : "unknown_error";
  return { code, outcome: error?.outcome ?? "blocked" };
}

export class ReceiptJournal {
  constructor({ directory, operationId, approvalId, approvalSubjectSha256, forbiddenValues = [] }) {
    this.directory = directory;
    this.operationId = operationId;
    this.approvalId = approvalId;
    this.approvalSubjectSha256 = approvalSubjectSha256;
    this.forbiddenValues = forbiddenValues.filter((value) => typeof value === "string" && value.length >= 8);
    this.sequence = 0;
    this.previousReceiptSha256 = null;
  }

  async append(state, detail = {}) {
    assert.match(state, /^[A-Z0-9_]+$/u);
    const receipt = {
      approvalId: this.approvalId,
      approvalSubjectSha256: this.approvalSubjectSha256,
      detail,
      operationId: this.operationId,
      previousReceiptSha256: this.previousReceiptSha256,
      receiptSchema: RECEIPT_SCHEMA,
      sequence: this.sequence,
      state,
      writtenAt: new Date().toISOString(),
    };
    const raw = canonicalJson(receipt);
    for (const value of this.forbiddenValues) {
      assert.equal(raw.includes(value), false, "receipt contains forbidden value");
    }
    assert.doesNotMatch(raw, /Authorization|Bearer |access[_-]?token|raw[_-]?sql|serializedRequestBody/iu);
    const filename = `${String(this.sequence).padStart(3, "0")}-${state.toLowerCase()}.json`;
    const filePath = resolve(this.directory, filename);
    const handle = await open(filePath, "wx", 0o600);
    try {
      await handle.writeFile(raw, "utf8");
      await handle.sync();
    } finally {
      await handle.close();
    }
    await chmod(filePath, 0o400);
    const sealedFile = await open(filePath, "r");
    try {
      await sealedFile.sync();
    } finally {
      await sealedFile.close();
    }
    const directoryHandle = await open(this.directory, "r");
    try {
      await directoryHandle.sync();
    } finally {
      await directoryHandle.close();
    }
    this.previousReceiptSha256 = sha256(raw);
    this.sequence += 1;
    return this.previousReceiptSha256;
  }
}

async function appendFailure(journal, state, error, detail = {}) {
  await journal.append(state, {
    ...detail,
    ...(error?.meta ?? {}),
    error: safeError(error),
    activationAllowed: false,
  });
}

async function appendFailureOrThrow(journal, state, error, detail = {}) {
  try {
    await appendFailure(journal, state, error, detail);
  } catch {
    const failure = new Error(`receipt_journal_failed_after_${state.toLowerCase()}`);
    failure.outcome = "receipt_failure";
    failure.productionState = state;
    throw failure;
  }
}

function apiDetail(result, extra = {}) {
  return { ...extra, ...result.meta };
}

export async function runProductionApply({
  approval: suppliedApproval,
  approvedSubjectSha256,
  fetchImpl,
  journal,
  now = () => new Date(),
  pack,
  releaseSha,
  token,
}) {
  const approval = deepFreeze(canonicalValue(suppliedApproval));
  const currentTime = () => typeof now === "function" ? now() : now;
  const assertApprovalActive = () => {
    const validated = validateApproval(
      approval, pack.manifest, releaseSha, currentTime(),
    );
    validateApprovedSubjectSha256(validated, approvedSubjectSha256);
    return validated;
  };
  assertApprovalActive();
  const common = {
    approvalExpiresAt: approval.expiresAt,
    canonicalSetSha256: pack.manifest.canonicalSetSha256,
    environment: "production",
    manifestSha256: pack.manifestSha256,
    projectRef: PROJECT_REF,
    releaseSha,
  };
  await journal.append("PACK_VERIFIED", { ...common, activationAllowed: false });

  let result;
  try {
    assertApprovalActive();
    result = await queryManagementApi({
      fetchImpl, token, query: pack.sql.preflight.text, readOnly: true,
    });
    const checks = validateCheckRows(result.parsed, "preflight");
    await journal.append("PREFLIGHT_VERIFIED", apiDetail(result, {
      ...checks,
      queryByteLength: pack.sql.preflight.byteLength,
      querySha256: pack.sql.preflight.sha256,
      activationAllowed: false,
    }));
  } catch (error) {
    await appendFailureOrThrow(journal, "ABORTED_CLEAN", error, { phase: "preflight" });
    throw error;
  }

  const executorProbe = "select current_user::text as current_user,session_user::text as session_user,pg_catalog.current_setting('transaction_read_only') as transaction_read_only";
  try {
    result = await queryManagementApi({
      fetchImpl, token, query: executorProbe, readOnly: false,
    });
    validateWriteExecutorRows(result.parsed);
    await journal.append("WRITE_EXECUTOR_VERIFIED", apiDetail(result, {
      queryByteLength: Buffer.byteLength(executorProbe),
      querySha256: sha256(executorProbe),
      writeExecutor: "postgres",
      activationAllowed: false,
    }));
  } catch (error) {
    await appendFailureOrThrow(journal, "ABORTED_CLEAN", error, { phase: "write_executor_probe" });
    throw error;
  }

  for (let index = 0; index < pack.migrations.length; index += 1) {
    const migration = pack.migrations[index];
    const ordinal = index + 1;
    const prefix = `M${ordinal}`;
    const migrationAttemptId = randomUUID();
    let bytes;
    let migrationSql;
    try {
      assertApprovalActive();
      bytes = await assertMigrationUnchanged(migration);
      migrationSql = buildLockedMigrationSql(migration, ordinal);
      await journal.append(`${prefix}_SEND_INTENT`, {
        migration: {
          byteLength: migration.byteLength,
          name: migration.name,
          ordinal,
          path: migration.path,
          sha256: migration.sha256,
          version: migration.version,
        },
        requestQueryByteLength: Buffer.byteLength(migrationSql),
        requestQuerySha256: sha256(migrationSql),
        rawMigrationByteLength: bytes.byteLength,
        rawMigrationSha256: sha256(bytes),
        transactionFencedSingleWinner: true,
        stalePreflightStateGuard: true,
        lockAutoReleasesOnCommitOrRollback: true,
        automaticRetryAllowed: false,
        attemptId: migrationAttemptId,
        activationAllowed: false,
      });
    } catch (error) {
      const state = ordinal === 1 ? "ABORTED_CLEAN" : "PARTIAL_M1_COMMITTED";
      await appendFailureOrThrow(journal, state, error, {
        phase: `${prefix.toLowerCase()}_local_pre_send`,
        migrationSha256: migration.sha256,
      });
      throw error;
    }
    try {
      assertApprovalActive();
    } catch (error) {
      const state = ordinal === 1 ? "ABORTED_CLEAN" : "PARTIAL_M1_COMMITTED";
      await appendFailureOrThrow(journal, state, error, {
        phase: `${prefix.toLowerCase()}_approval_expired_before_send`,
        migrationSha256: migration.sha256,
      });
      throw error;
    }
    try {
      result = await queryManagementApi({
        attemptId: migrationAttemptId,
        fetchImpl, token, query: migrationSql, readOnly: false,
        timeoutMs: 120_000,
      });
      assert.equal(Array.isArray(result.parsed), true, "migration response must be JSON array");
      await journal.append(`${prefix}_COMMIT_RESPONSE_RECEIVED`, apiDetail(result, {
        migrationSha256: migration.sha256,
        requestQueryByteLength: Buffer.byteLength(migrationSql),
        requestQuerySha256: sha256(migrationSql),
        rawMigrationByteLength: bytes.byteLength,
        rawMigrationSha256: sha256(bytes),
        commitProof: "guarded_history_and_readback_pending",
        activationAllowed: false,
      }));
    } catch (error) {
      const state = ordinal === 1 ? "OUTCOME_UNKNOWN" : "M2_OUTCOME_UNKNOWN";
      await appendFailureOrThrow(journal, state, error, {
        phase: `${prefix.toLowerCase()}_migration`,
        migrationSha256: migration.sha256,
      });
      throw error;
    }

    let historySql;
    const historyAttemptId = randomUUID();
    try {
      assertApprovalActive();
      historySql = buildHistoryRegistrationSql(migration, ordinal);
      await journal.append(`${prefix}_HISTORY_SEND_INTENT`, {
        queryByteLength: Buffer.byteLength(historySql),
        querySha256: sha256(historySql),
        migrationSha256: migration.sha256,
        rawMigrationEmbeddedAsHexData: true,
        automaticRetryAllowed: false,
        attemptId: historyAttemptId,
        activationAllowed: false,
      });
      assertApprovalActive();
      result = await queryManagementApi({
        attemptId: historyAttemptId,
        fetchImpl, token, query: historySql, readOnly: false,
        timeoutMs: 120_000,
      });
      validateHistoryWriteRows(result.parsed, migration);
      await journal.append(`${prefix}_HISTORY_RESPONSE_RECEIVED`, apiDetail(result, {
        migrationSha256: migration.sha256,
        queryByteLength: Buffer.byteLength(historySql),
        querySha256: sha256(historySql),
        commitProof: "readback_pending",
        activationAllowed: false,
      }));
    } catch (error) {
      await appendFailureOrThrow(journal, `${prefix}_HISTORY_PENDING`, error, {
        phase: `${prefix.toLowerCase()}_history`,
        migrationSha256: migration.sha256,
      });
      throw error;
    }

    if (ordinal === 1) {
      try {
        result = await queryManagementApi({
          fetchImpl, token, query: pack.sql.intermediate.text, readOnly: true,
        });
        const checks = validateCheckRows(result.parsed, "intermediate");
        await journal.append("INTERMEDIATE_VERIFIED", apiDetail(result, {
          ...checks,
          queryByteLength: pack.sql.intermediate.byteLength,
          querySha256: pack.sql.intermediate.sha256,
          migrationSha256: migration.sha256,
          activationAllowed: false,
        }));
      } catch (error) {
        await appendFailureOrThrow(journal, "PARTIAL_M1_COMMITTED", error, {
          phase: "intermediate_readback",
          migrationSha256: migration.sha256,
        });
        throw error;
      }
    }
  }

  try {
    result = await queryManagementApi({
      fetchImpl, token, query: pack.sql.postflight.text, readOnly: true,
    });
    const checks = validateCheckRows(result.parsed, "postflight");
    await journal.append("POSTFLIGHT_VERIFIED", apiDetail(result, {
      ...checks,
      queryByteLength: pack.sql.postflight.byteLength,
      querySha256: pack.sql.postflight.sha256,
      activationAllowed: false,
    }));
  } catch (error) {
    await appendFailureOrThrow(journal, "POSTFLIGHT_BLOCKED", error, { phase: "postflight" });
    throw error;
  }

  const finalReceiptSha256 = await journal.append("COMPLETE", {
    ...common,
    exactMigrationBytesProven: true,
    historyRowsExact: true,
    postflightPassed: true,
    databaseMigrationComplete: true,
    runtimeActivationAllowed: false,
    authAccountCreated: false,
    contentProviderCallsMade: false,
    supabaseManagementApiCallsMade: true,
    telegramSent: false,
    activationAllowed: false,
  });
  return { ok: true, operationId: approval.operationId, finalReceiptSha256 };
}

export function newApprovalTemplate({ releaseSha, canonicalSetSha256, now = new Date() }) {
  const approvedAt = now.toISOString();
  const expiresAt = new Date(now.getTime() + MAX_APPROVAL_WINDOW_MS).toISOString();
  const approval = {
    actions: [...REQUIRED_ACTIONS],
    approvalId: randomUUID(),
    approvalSubject: "",
    approvalSubjectSha256: "",
    approvedAt,
    approvedBy: APPROVAL_ACTOR_PLACEHOLDER,
    canonicalSetSha256,
    environment: "production",
    expiresAt,
    genericDbPushAllowed: false,
    operationId: randomUUID(),
    projectRef: PROJECT_REF,
    releaseSha,
    runtimeActivationAllowed: false,
    schemaVersion: APPROVAL_SCHEMA,
  };
  approval.approvalSubject = boundedApprovalSubject(approval);
  approval.approvalSubjectSha256 = sha256(approval.approvalSubject);
  return canonicalValue(approval);
}
