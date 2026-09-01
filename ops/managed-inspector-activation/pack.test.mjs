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
  assert.equal(checkIds.length, 25);
  assert.equal(new Set(checkIds).size, checkIds.length);
});

test("the PostgreSQL 17 postflight covers the MAINTAIN table privilege", async () => {
  const sql = await readFile(new URL("./postflight.sql", import.meta.url), "utf8");
  assert.match(sql, /\('MAINTAIN'\)/u);
  assert.match(
    sql,
    /SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN/u,
  );
  assert.match(sql, /eight PostgreSQL 17 table privileges per target table/u);
});

test("the postflight measures effective object privileges through schema usage", async () => {
  const sql = await readFile(new URL("./postflight.sql", import.meta.url), "utf8");
  assert.match(
    sql,
    /pg_catalog\.has_schema_privilege\(r\.oid, n\.oid, 'USAGE'\)/gu,
  );
  assert.match(sql, /target_role_transitive_members_exact/u);
  assert.match(sql, /expected_authenticator_admin_members/u);
  assert.match(sql, /\('authenticator'::text, false, false, true, false, false, 'postgres'::text\)/u);
  assert.match(sql, /\('postgres', true, false, false, true, true, 'supabase_admin'\)/u);
  assert.match(
    sql,
    /\('authenticator'::text, 2\),\s*\('postgres', 2\),\s*\('postgres', 3\),\s*\('supabase_storage_admin', 3\)/u,
  );
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

test("both superseded first-migration hashes are rejected", () => {
  for (const digest of [
    "a82b9a279b36a535ebdf771b1a183e42d239116e51398bbd6c3b6832d102daf2",
    "aecc7af2abf58c00402c026cbf90dabe077a68379ae17bc307a2ed137759a4ed",
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
