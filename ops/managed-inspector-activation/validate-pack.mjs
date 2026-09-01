#!/usr/bin/env node

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { lstat, readFile } from "node:fs/promises";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const PACK_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = resolve(PACK_DIRECTORY, "../..");

const EXPECTED_MIGRATIONS = Object.freeze([
  Object.freeze({
    byteLength: 44307,
    encoding: "UTF-8",
    finalNewline: true,
    lineEnding: "LF",
    path: "supabase/migrations/20260831180000_managed_auth_telegram_inspect.sql",
    regularNonSymlink: true,
    sha256: "61bf61ee4be6993c88d471b0d9b3e3fa2bf1063ba87d1a901cceff2fc953ab46",
    version: "20260831180000",
  }),
  Object.freeze({
    byteLength: 27348,
    encoding: "UTF-8",
    finalNewline: true,
    lineEnding: "LF",
    path: "supabase/migrations/20260901120000_managed_inspector_role_boundary.sql",
    regularNonSymlink: true,
    sha256: "256f8ddb19a6bbfaf2fc98ea168a1da6dc1945c54856f7450b0ba90d70817a25",
    version: "20260901120000",
  }),
]);

const EXPECTED_MANIFEST_KEYS = Object.freeze([
  "canonicalLineFormat",
  "canonicalPayloadDomain",
  "canonicalSetPurpose",
  "canonicalSetSha256",
  "migrations",
  "schemaVersion",
  "strictOrder",
]);
const EXPECTED_MIGRATION_KEYS = Object.freeze([
  "byteLength",
  "encoding",
  "finalNewline",
  "lineEnding",
  "path",
  "regularNonSymlink",
  "sha256",
  "version",
]);
const CANONICAL_PAYLOAD_DOMAIN = "coineasy-managed-inspector-migration-set@1";
const REJECTED_MIGRATION_HASHES = Object.freeze(new Set([
  // This predecessor granted the three RPCs to authenticated during the gap
  // between separately committed production migrations.
  "a82b9a279b36a535ebdf771b1a183e42d239116e51398bbd6c3b6832d102daf2",
  // This predecessor removed the authenticated grant but did not normalize
  // arbitrary creator default-ACL grantees before the first commit.
  "aecc7af2abf58c00402c026cbf90dabe077a68379ae17bc307a2ed137759a4ed",
  // This predecessor fixed the target-role boundary but rejected Supabase's
  // exact platform-managed postgres -> cli_login_postgres role path.
  "ac5538098b0ce71f1a4f24c15478456354fc30b29814e9bc4c9a9fb6d8ff83ad",
]));
const SQL_FILES = Object.freeze(["preflight.sql", "postflight.sql"]);
const FORBIDDEN_SQL_WORDS = Object.freeze([
  "alter",
  "analyze",
  "call",
  "cluster",
  "comment",
  "copy",
  "create",
  "delete",
  "do",
  "drop",
  "grant",
  "insert",
  "lock",
  "merge",
  "refresh",
  "reindex",
  "reset",
  "revoke",
  "security",
  "set",
  "truncate",
  "update",
  "vacuum",
]);

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function canonicalLines(migrations) {
  return `${CANONICAL_PAYLOAD_DOMAIN}\n${migrations
    .map(
      ({ version, path, byteLength, sha256: digest }) =>
        `${version}\t${path}\t${byteLength}\t${digest}\n`,
    )
    .join("")}`;
}

function canonicalJson(value) {
  if (Array.isArray(value)) {
    return value.map(canonicalJson);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalJson(value[key])]),
    );
  }
  return value;
}

export function parseCanonicalJson(rawJson) {
  const parsed = JSON.parse(rawJson);
  assert.equal(
    rawJson,
    `${JSON.stringify(canonicalJson(parsed), null, 2)}\n`,
    "manifest is not deterministic canonical JSON or contains duplicate keys",
  );
  return parsed;
}

export function assertAllowedMigrationSha256(digest, label = "migration") {
  assert.equal(
    REJECTED_MIGRATION_HASHES.has(digest),
    false,
    `${label} uses a rejected migration hash`,
  );
}

export function validateMigrationBytes(migration, contents, metadata) {
  assert.equal(metadata.isFile(), true, `${migration.version} is not a regular file`);
  assert.equal(metadata.isSymbolicLink(), false, `${migration.version} is a symbolic link`);
  assert.equal(contents.byteLength, migration.byteLength, `${migration.version} byte length mismatch`);
  const text = new TextDecoder("utf-8", { fatal: true }).decode(contents);
  assert.equal(text.startsWith("\uFEFF"), false, `${migration.version} has a UTF-8 BOM`);
  assert.equal(text.includes("\r"), false, `${migration.version} is not LF-only`);
  assert.equal(contents.at(-1), 0x0a, `${migration.version} has no final newline`);
  const actualSha256 = sha256(contents);
  assertAllowedMigrationSha256(actualSha256, migration.version);
  assert.equal(actualSha256, migration.sha256, `${migration.version} sha256 mismatch`);
}

function sortedKeys(value) {
  return Object.keys(value).sort();
}

function assertRepositoryRelativePath(path) {
  assert.equal(isAbsolute(path), false, `migration path must be relative: ${path}`);
  const absolute = resolve(REPOSITORY_ROOT, path);
  const fromRoot = relative(REPOSITORY_ROOT, absolute);
  assert.equal(
    fromRoot === ".." || fromRoot.startsWith(`..${sep}`),
    false,
    `migration path escapes repository: ${path}`,
  );
  assert.equal(fromRoot.split(sep).join("/"), path, `migration path is not canonical: ${path}`);
  return absolute;
}

export async function validateManifest() {
  const manifestPath = resolve(PACK_DIRECTORY, "manifest.json");
  const rawManifest = await readFile(manifestPath, "utf8");
  const manifest = parseCanonicalJson(rawManifest);

  assert.deepEqual(sortedKeys(manifest), EXPECTED_MANIFEST_KEYS, "manifest keys changed");
  assert.equal(manifest.schemaVersion, "coineasy-managed-inspector-migration-manifest@1");
  assert.equal(manifest.strictOrder, true);
  assert.equal(
    manifest.canonicalLineFormat,
    "<version>\\t<path>\\t<byteLength>\\t<sha256>\\n",
  );
  assert.equal(manifest.canonicalPayloadDomain, CANONICAL_PAYLOAD_DOMAIN);
  assert.equal(
    manifest.canonicalSetPurpose,
    "migration-byte-set-integrity-only-not-manifest-or-release-sha",
  );
  assert.deepEqual(manifest.migrations, EXPECTED_MIGRATIONS, "migration identity or order changed");

  const versions = manifest.migrations.map(({ version }) => version);
  assert.deepEqual([...versions].sort(), versions, "migration versions are not strictly ordered");
  assert.equal(new Set(versions).size, versions.length, "migration versions are not unique");

  for (const migration of manifest.migrations) {
    assert.deepEqual(sortedKeys(migration), EXPECTED_MIGRATION_KEYS, `${migration.version} keys changed`);
    assert.match(migration.version, /^\d{14}$/u);
    assert.match(migration.sha256, /^[a-f0-9]{64}$/u);
    assert.equal(migration.encoding, "UTF-8");
    assert.equal(migration.lineEnding, "LF");
    assert.equal(migration.finalNewline, true);
    assert.equal(migration.regularNonSymlink, true);
    assert.equal(Number.isSafeInteger(migration.byteLength), true);
    assert.equal(migration.byteLength > 0, true);
    assertAllowedMigrationSha256(migration.sha256, migration.version);
    assert.equal(
      migration.path.split("/").at(-1).startsWith(`${migration.version}_`),
      true,
      `${migration.version} does not match its filename`,
    );
    const absolutePath = assertRepositoryRelativePath(migration.path);
    const metadata = await lstat(absolutePath);
    const contents = await readFile(absolutePath);
    validateMigrationBytes(migration, contents, metadata);
  }

  assert.equal(
    sha256(canonicalLines(manifest.migrations)),
    manifest.canonicalSetSha256,
    "canonical migration set sha256 mismatch",
  );

  return manifest;
}

// Replace strings, quoted identifiers and comments before checking statement
// boundaries or mutation words. Dollar-quoted bodies are deliberately rejected:
// a catalog-only validation query has no reason to contain one.
export function maskSqlLiteralsAndComments(sql) {
  let result = "";
  let index = 0;
  let state = "plain";

  while (index < sql.length) {
    const current = sql[index];
    const next = sql[index + 1];

    if (state === "line-comment") {
      if (current === "\n") {
        state = "plain";
        result += "\n";
      } else {
        result += " ";
      }
      index += 1;
      continue;
    }
    if (state === "block-comment") {
      if (current === "*" && next === "/") {
        result += "  ";
        index += 2;
        state = "plain";
      } else {
        result += current === "\n" ? "\n" : " ";
        index += 1;
      }
      continue;
    }
    if (state === "single-quote") {
      if (current === "'" && next === "'") {
        result += "  ";
        index += 2;
      } else if (current === "'") {
        result += " ";
        index += 1;
        state = "plain";
      } else {
        result += current === "\n" ? "\n" : " ";
        index += 1;
      }
      continue;
    }
    if (state === "double-quote") {
      if (current === '"' && next === '"') {
        result += "  ";
        index += 2;
      } else if (current === '"') {
        result += " ";
        index += 1;
        state = "plain";
      } else {
        result += current === "\n" ? "\n" : " ";
        index += 1;
      }
      continue;
    }

    if (current === "-" && next === "-") {
      result += "  ";
      index += 2;
      state = "line-comment";
    } else if (current === "/" && next === "*") {
      result += "  ";
      index += 2;
      state = "block-comment";
    } else if (current === "'") {
      result += " ";
      index += 1;
      state = "single-quote";
    } else if (current === '"') {
      result += " ";
      index += 1;
      state = "double-quote";
    } else {
      assert.notEqual(current, "$", "dollar quoting is not allowed in validate-only SQL");
      result += current;
      index += 1;
    }
  }

  assert.equal(state, "plain", `unterminated SQL ${state}`);
  return result;
}

export async function validateSqlPack() {
  for (const filename of SQL_FILES) {
    const sql = await readFile(resolve(PACK_DIRECTORY, filename), "utf8");
    validateSqlText(sql, filename);
  }
}

export function validateSqlText(sql, filename = "validate-only.sql") {
  const masked = maskSqlLiteralsAndComments(sql);
  const normalized = masked.trim();
  assert.match(normalized, /^with\b/iu, `${filename} must be one WITH ... SELECT query`);
  assert.equal((normalized.match(/;/gu) ?? []).length, 1, `${filename} must contain one statement`);
  assert.equal(normalized.endsWith(";"), true, `${filename} must end after its only statement`);
  assert.match(normalized, /\bselect\b/iu, `${filename} has no SELECT`);
  for (const word of FORBIDDEN_SQL_WORDS) {
    assert.doesNotMatch(
      normalized,
      new RegExp(`\\b${word}\\b`, "iu"),
      `${filename} contains forbidden SQL word ${word}`,
    );
  }
}

export async function validatePack() {
  const manifest = await validateManifest();
  await validateSqlPack();
  return manifest;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const manifest = await validatePack();
  process.stdout.write(
    `${JSON.stringify({
      ok: true,
      schemaVersion: manifest.schemaVersion,
      canonicalSetSha256: manifest.canonicalSetSha256,
      migrationCount: manifest.migrations.length,
      sqlPacks: SQL_FILES,
    })}\n`,
  );
}
