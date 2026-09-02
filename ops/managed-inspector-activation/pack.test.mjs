import test from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import {
  assertAllowedMigrationSha256,
  parseCanonicalJson,
  validateManifest,
  validateMigrationBytes,
  validatePack,
  validateSqlPack,
  validateSqlText,
} from "./validate-pack.mjs";

test("the canonical migration manifest matches the exact ordered files", async () => {
  const manifest = await validateManifest();
  assert.equal(manifest.strictOrder, true);
  assert.deepEqual(
    manifest.migrations.map(({ version }) => version),
    ["20260831180000", "20260901120000"],
  );
});

test("the production validation SQL pack is catalog-only and one-statement", async () => {
  await validateSqlPack();
});

test("the preflight makes the future NOINHERIT role state predictable", async () => {
  const sql = await readFile(new URL("./preflight.sql", import.meta.url), "utf8");
  const checkIds = [
    ...sql.matchAll(/select\s*\n\s*'([a-z0-9_]+)',/gu),
  ].map((match) => match[1]);

  assert.match(sql, /with recursive/u);
  assert.match(
    sql,
    /'auth,extensions,graphql_public,private,public'/u,
  );
  assert.match(sql, /count\(\*\) = 5/u);
  assert.match(sql, /acl\.grantee = 0/gu);
  assert.match(sql, /pg_catalog\.acldefault\('f', p\.proowner\)/u);
  assert.match(sql, /pg_catalog\.acldefault\('r', c\.relowner\)/u);
  assert.match(sql, /pg_catalog\.acldefault\('S', c\.relowner\)/u);
  assert.match(
    sql,
    /pg_catalog\.acldefault\('n', namespace\.nspowner\)/u,
  );
  assert.match(sql, /public_usable_schemas/u);
  assert.match(sql, /acl\.privilege_type = 'USAGE'/u);
  assert.equal(
    [...sql.matchAll(/join public_usable_schemas n on n\.oid = c\.relnamespace/gu)].length,
    3,
  );
  assert.match(
    sql,
    /\('postgres'::text, true, true, true, true, true, 'supabase_admin'::text, 2\)/u,
  );
  assert.match(
    sql,
    /\('supabase_storage_admin'::text, false, false, true, true, false, 'supabase_admin', 2\)/u,
  );
  assert.match(
    sql,
    /join pg_catalog\.pg_auth_members m on m\.roleid = d\.member/u,
  );
  assert.match(
    sql,
    /array\['authenticator', 'postgres', 'cli_login_postgres'\]::text\[\]/u,
  );
  assert.match(
    sql,
    /where pg_catalog\.to_regrole\('cli_login_postgres'\) is not null/u,
  );
  assert.match(sql, /expected_authenticator_descendant_edges/u);
  assert.ok([...sql.matchAll(/except all/gu)].length >= 2);
  assert.deepEqual(
    checkIds.filter((checkId) => checkId.startsWith("public_")),
    [
      "public_execute_on_existing_public_functions_zero",
      "public_execute_on_existing_private_functions_zero",
      "public_relation_privileges_zero",
      "public_column_privileges_zero",
      "public_sequence_privileges_zero",
      "public_schema_privileges_compatible",
    ],
  );
  assert.ok(
    checkIds.includes("authenticator_platform_admin_descendants_exact"),
  );
  assert.ok(checkIds.includes("read_only_executor_data_capability"));
  assert.equal(checkIds.length, 26);
  assert.equal(new Set(checkIds).size, checkIds.length);
});

test("the intermediate gate fixes the safe state between canonical migrations", async () => {
  const sql = await readFile(new URL("./intermediate.sql", import.meta.url), "utf8");
  const checkIds = [
    ...sql.matchAll(/select\s*\n\s*'([a-z0-9_]+)',/gu),
  ].map((match) => match[1]);

  assert.deepEqual(checkIds, [
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
  ]);
  assert.equal(new Set(checkIds).size, checkIds.length);
  assert.equal(checkIds.length, 22);
  assert.match(sql, /first_migration_history_exact/u);
  assert.match(sql, /second_migration_history_absent/u);
  assert.match(sql, /pg_catalog\.acldefault\('r', c\.relowner\)/u);
  assert.match(sql, /pg_catalog\.acldefault\('f', f\.proowner\)/u);
  assert.match(sql, /except all/gu);
  assert.match(sql, /count\(\*\) = 8/u);
  assert.match(sql, /coalesce\(sum\(row_count\), 0\) = 0/u);
  assert.match(sql, /pg_catalog\.pg_has_role\(r\.oid, reader\.oid, 'USAGE'\)/u);
  assert.match(sql, /61bf61ee4be6993c88d471b0d9b3e3fa2bf1063ba87d1a901cceff2fc953ab46/u);
  assert.match(sql, /pg_catalog\.octet_length\(observed\) <= 4096/u);
  assert.match(sql, /extensions\.digest/u);
  assert.match(sql, /false as generic_db_push_allowed/u);
  assert.match(sql, /true as custom_apply_receipt_required/u);
});

test("the PostgreSQL 17 postflight covers the MAINTAIN table privilege", async () => {
  const sql = await readFile(new URL("./postflight.sql", import.meta.url), "utf8");
  assert.match(sql, /\('MAINTAIN'\)/u);
  assert.match(
    sql,
    /SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN/u,
  );
  assert.match(sql, /eight PostgreSQL 17 table privileges per target table/u);
  assert.match(sql, /pg_catalog\.cardinality\(m\.statements\) = 1/u);
  assert.match(sql, /61bf61ee4be6993c88d471b0d9b3e3fa2bf1063ba87d1a901cceff2fc953ab46/u);
  assert.match(sql, /256f8ddb19a6bbfaf2fc98ea168a1da6dc1945c54856f7450b0ba90d70817a25/u);
});

test("the postflight measures effective object privileges through schema usage", async () => {
  const sql = await readFile(new URL("./postflight.sql", import.meta.url), "utf8");
  assert.match(
    sql,
    /pg_catalog\.has_schema_privilege\(r\.oid, n\.oid, 'USAGE'\)/gu,
  );
  assert.match(sql, /target_role_transitive_members_exact/u);
  assert.match(sql, /expected_authenticator_admin_members/u);
  assert.match(sql, /expected_target_descendant_edges/u);
  assert.match(sql, /\('authenticator'::text, false, false, true, false, false, 'postgres'::text\)/u);
  assert.match(sql, /\('postgres', true, false, false, true, true, 'supabase_admin'\)/u);
  assert.match(
    sql,
    /array\['coineasy_managed_inspector', 'authenticator', 'postgres', 'cli_login_postgres'\]::text\[\]/u,
  );
  assert.match(
    sql,
    /array\['coineasy_managed_inspector', 'postgres', 'cli_login_postgres'\]::text\[\]/u,
  );
  assert.match(
    sql,
    /where pg_catalog\.to_regrole\('cli_login_postgres'\) is not null/u,
  );
  assert.ok([...sql.matchAll(/except all/gu)].length >= 2);
  for (const cte of [
    "unexpected_relation_privileges",
    "unexpected_column_privileges",
    "unexpected_sequence_privileges",
  ]) {
    assert.match(
      sql,
      new RegExp(`${cte}[\\s\\S]{0,1200}has_schema_privilege\\(r\\.oid, n\\.oid, 'USAGE'\\)`, "u"),
    );
  }
});

test("the postflight inventories exact zero target-column ACL entries", async () => {
  const sql = await readFile(new URL("./postflight.sql", import.meta.url), "utf8");
  assert.match(sql, /actual_target_column_acl/u);
  assert.match(sql, /a\.attacl/u);
  assert.match(sql, /pg_catalog\.aclexplode/u);
  assert.match(sql, /target_column_acl_inventory_zero/u);
});

test("the target trigger contract fixes function, timing, level, and events", async () => {
  const sql = await readFile(new URL("./postflight.sql", import.meta.url), "utf8");
  assert.match(sql, /'managed_inspect_immutable'::text, 27::smallint/u);
  assert.match(sql, /'managed_inspect_no_truncate', 34::smallint/u);
  assert.match(sql, /fn\.oid = t\.tgfoid/u);
  assert.match(sql, /e\.tgtype = a\.tgtype/u);
  assert.match(sql, /e\.tgenabled = a\.tgenabled/u);
  assert.match(sql, /e\.function_schema = a\.function_schema/u);
  assert.match(sql, /e\.function_name = a\.function_name/u);
  assert.match(sql, /e\.function_argument_types = a\.function_argument_types/u);
  assert.match(sql, /e\.tgnargs = a\.tgnargs/u);
  assert.match(sql, /e\.tgargs_byte_length = a\.tgargs_byte_length/u);
  assert.match(sql, /e\.tgqual_is_null = \(a\.tgqual is null\)/u);
  assert.match(
    sql,
    /e\.tgattr_is_empty = \(pg_catalog\.cardinality\(a\.tgattr\) = 0\)/u,
  );
});

test("the complete activation pack validates without external state", async () => {
  const manifest = await validatePack();
  assert.equal(manifest.canonicalSetSha256.length, 64);
});

test("canonical JSON rejects duplicate keys", () => {
  assert.throws(
    () => parseCanonicalJson('{\n  "key": 1,\n  "key": 2\n}\n'),
    /canonical JSON|duplicate keys/u,
  );
});

test("all superseded migration hashes are rejected", () => {
  for (const digest of [
    "a82b9a279b36a535ebdf771b1a183e42d239116e51398bbd6c3b6832d102daf2",
    "aecc7af2abf58c00402c026cbf90dabe077a68379ae17bc307a2ed137759a4ed",
    "ac5538098b0ce71f1a4f24c15478456354fc30b29814e9bc4c9a9fb6d8ff83ad",
  ]) assert.throws(
    () => assertAllowedMigrationSha256(digest),
    /rejected migration hash/u,
  );
});

test("migration bytes reject symlinks and CRLF", () => {
  const contents = Buffer.from("select 1;\r\n", "utf8");
  const migration = {
    version: "fixture",
    byteLength: contents.byteLength,
    sha256: createHash("sha256").update(contents).digest("hex"),
  };
  assert.throws(
    () => validateMigrationBytes(migration, contents, {
      isFile: () => true,
      isSymbolicLink: () => true,
    }),
    /symbolic link/u,
  );
  assert.throws(
    () => validateMigrationBytes(migration, contents, {
      isFile: () => true,
      isSymbolicLink: () => false,
    }),
    /LF-only/u,
  );
});

test("validate-only SQL rejects multiple statements and mutation CTEs", () => {
  assert.throws(
    () => validateSqlText("with value as (select 1) select * from value; select 2;"),
    /one statement/u,
  );
  assert.throws(
    () => validateSqlText(
      "with changed as (insert into example values (1) returning 1) select * from changed;",
    ),
    /forbidden SQL word insert/u,
  );
});
