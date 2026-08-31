import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { userInfo } from "node:os";
import test from "node:test";

import {
  pgJsonbText,
  sha256,
  validateInspectResponse,
  validateRequest,
} from "../scripts/lib/telegram-resolution-inspect.mjs";

// Normal JS tests never connect to any database. This explicit opt-in is used
// only in the disposable PostgreSQL CI job or the named local test databases.
const OPTED_IN = process.env.RESOLUTION_INSPECT_LOCAL_DB === "1";

function localPostgresEnvironment(): NodeJS.ProcessEnv {
  assert.equal(process.env.RESOLUTION_INSPECT_LOCAL_DB, "1");
  const host = process.env.PGHOST;
  assert.ok(
    host === "127.0.0.1" || host === "localhost" || host === "::1"
      || host === "/private/tmp" || host === "/tmp",
    "PostgreSQL parity requires an explicit loopback or local test socket host",
  );
  const port = process.env.PGPORT;
  assert.ok(port !== undefined && /^[1-9][0-9]{0,4}$/.test(port));
  assert.ok(Number(port) <= 65535);
  const database = process.env.PGDATABASE;
  const ciDatabase = process.env.CI === "true"
    && process.env.GITHUB_ACTIONS === "true"
    && host === "127.0.0.1"
    && database === "postgres";
  assert.ok(
    ciDatabase || (database !== undefined
      && /^(?:coineasy_resolution_v[1-9][0-9]*|coineasy_[a-z0-9_]+_test)$/.test(database)),
    "PostgreSQL parity requires an explicitly named disposable test database",
  );
  const username = process.env.PGUSER ?? userInfo().username;
  assert.match(username, /^[A-Za-z_][A-Za-z0-9_-]{0,62}$/);

  // Do not inherit PGSERVICE, PGHOSTADDR, PGOPTIONS, connection strings, the
  // operator's password file, or any production credential environment.
  const env: NodeJS.ProcessEnv = {
    PATH: process.env.PATH,
    LANG: "C",
    TZ: "UTC",
    PGHOST: host,
    PGPORT: port,
    PGDATABASE: database,
    PGUSER: username,
    PGPASSFILE: "/dev/null",
    PGCONNECT_TIMEOUT: "3",
    PGAPPNAME: "telegram-resolution-inspect-readonly-parity-test",
    PGOPTIONS: "-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000 -c TimeZone=UTC",
  };
  if (ciDatabase) {
    assert.equal(username, "postgres");
    // This is the public synthetic postgres:16 service fixture, not an
    // operator credential. No other password is read or forwarded.
    env.PGPASSWORD = "postgres";
  }
  return env;
}

function jsonbLiteral(value: unknown): string {
  const hex = Buffer.from(JSON.stringify(value), "utf8").toString("hex");
  assert.match(hex, /^[0-9a-f]+$/);
  // Only generated hex enters the SQL string; psql receives SQL on stdin and
  // never invokes a shell. Even quotes/control bytes remain decoded JSON data.
  return `pg_catalog.convert_from(pg_catalog.decode('${hex}', 'hex'), 'UTF8')::jsonb`;
}

function postgresJson(sqlExpression: string): any {
  const result = spawnSync(
    "psql",
    ["-X", "--no-password", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1"],
    {
      env: localPostgresEnvironment(),
      input: `begin read only;\nset local time zone 'UTC';\nselect (${sqlExpression})::text;\nrollback;\n`,
      encoding: "utf8",
      timeout: 10_000,
      maxBuffer: 1024 * 1024,
      shell: false,
    },
  );
  assert.equal(result.error, undefined, "local SELECT-only psql must be available");
  assert.equal(result.status, 0, "local SELECT-only PostgreSQL parity query failed");
  const output = result.stdout.trim();
  assert.ok(output.length > 0 && !output.includes("\n"));
  return JSON.parse(output);
}

function postgresCanonical(value: unknown) {
  const literal = jsonbLiteral(value);
  return postgresJson(`pg_catalog.jsonb_build_object(
    'text', (${literal})::text,
    'sha256', pg_catalog.encode(pg_catalog.sha256(
      pg_catalog.convert_to((${literal})::text, 'UTF8')
    ), 'hex'),
    'read_only', pg_catalog.current_setting('transaction_read_only'),
    'version', pg_catalog.current_setting('server_version_num')
  )`);
}

const AUDIT = {
  snapshot_sha256: "e".repeat(64),
  schema_version: "telegram-public-channel-audit@1",
  scan_source: "public_telegram_web_history",
  public_channel: "squid_kor_update",
  png_match_count: 0,
  message_count: 121,
  last_message_id: "9223372036854775807",
  first_message_id: "9223372036854775687",
  checked_at: "2026-08-31T12:00:00Z",
  caption_match_count: 0,
};

const NOW = new Date("2026-08-31T12:01:00Z");
const fixtureUuid = (index: number) =>
  `11111111-1111-4111-8111-${index.toString(16).padStart(12, "0")}`;
const REQUEST = {
  schema_version: "telegram-resolution-inspect-request@1",
  project_ref: "abcdefghijklmnopqrst",
  environment: "production",
  client_id: "squid",
  release_sha: "a".repeat(40),
  workspace_id: fixtureUuid(1),
  content_item_id: fixtureUuid(2),
  content_version_id: fixtureUuid(3),
  publication_id: fixtureUuid(4),
  job_id: fixtureUuid(5),
  resolution_id: fixtureUuid(6),
  operator_approval_id: fixtureUuid(7),
  inspected_by: "operator:synthetic-inspector",
  approved_by: "operator:synthetic-future-approver",
  expires_at: "2026-08-31T13:00:00Z",
  public_audit: AUDIT,
};

function postgresInspectResponse(startedAt = "2026-08-31T11:40:00.123456Z") {
  const subject = {
    schema_version: "exact-telegram-delivery-resolution@1",
    action: "resolve_delivery_unknown_without_resend",
    workspace_id: REQUEST.workspace_id,
    client_id: "squid",
    content_item_id: REQUEST.content_item_id,
    content_version_id: REQUEST.content_version_id,
    publication_id: REQUEST.publication_id,
    job_id: REQUEST.job_id,
    publication_approval_id: fixtureUuid(8),
    asset_id: fixtureUuid(9),
    delivery_attempt_id: fixtureUuid(10),
    delivery_request_sha256: "0".repeat(64),
    publication_request_sha256: "1".repeat(64),
    publication_response_sha256: "2".repeat(64),
    job_input_sha256: "3".repeat(64),
    job_output_sha256: "4".repeat(64),
    content_item_row_sha256: "5".repeat(64),
    content_version_row_sha256: "6".repeat(64),
    publication_row_sha256: "7".repeat(64),
    job_row_sha256: "8".repeat(64),
    publication_approval_row_sha256: "9".repeat(64),
    asset_row_sha256: "a".repeat(64),
    caption_sha256: "b".repeat(64),
    asset_sha256: "c".repeat(64),
    publication_status: "delivery_unknown",
    job_status: "failed",
    delivery_outcome: "unknown",
    disposition: "operator_closed_without_resend",
    public_observation: "not_observed_at_checked_at",
    public_audit: AUDIT,
    resolution_id: REQUEST.resolution_id,
    operator_approval_id: REQUEST.operator_approval_id,
    approved_by: REQUEST.approved_by,
    approved_release_sha: REQUEST.release_sha,
    resend_authorized: false,
    provider_calls: 0,
    database_claims: 0,
    publication_state_changed: false,
    job_state_changed: false,
    forbidden_actions: [
      "provider_call", "claim", "requeue", "resend", "mark_published",
      "create_publication", "create_job",
    ],
  };
  const envelope = {
    eligible: true,
    resolved: false,
    reused: false,
    resolution_id: REQUEST.resolution_id,
    publication_id: REQUEST.publication_id,
    job_id: REQUEST.job_id,
    content_item_id: REQUEST.content_item_id,
    content_version_id: REQUEST.content_version_id,
    delivery_outcome: "unknown",
    disposition: "operator_closed_without_resend",
    public_observation: "not_observed_at_checked_at",
    approved: false,
    approved_at: null,
    resend_authorized: false,
  };
  // Construct only synthetic JSON, timestamps and SHA-256 in PostgreSQL. This
  // intentionally calls no inspect RPC and selects from no production table.
  return postgresJson(`(
    with synthetic as (
      select ${jsonbLiteral(subject)} || pg_catalog.jsonb_build_object(
        'delivery_started_at', ((${jsonbLiteral(startedAt)}) #>> '{}')::timestamptz,
        'expires_at', ((${jsonbLiteral(REQUEST.expires_at)}) #>> '{}')::timestamptz,
        'public_audit_sha256', pg_catalog.encode(pg_catalog.sha256(
          pg_catalog.convert_to((${jsonbLiteral(AUDIT)})::text, 'UTF8')
        ), 'hex')
      ) as subject
    )
    select pg_catalog.jsonb_build_object(
      'subject_text', subject::text,
      'response', ${jsonbLiteral(envelope)} || pg_catalog.jsonb_build_object(
        'approval_subject', subject,
        'approval_subject_sha256', pg_catalog.encode(pg_catalog.sha256(
          pg_catalog.convert_to(subject::text, 'UTF8')
        ), 'hex')
      )
    )
    from synthetic
  )`);
}

test("inspect canonical JSON and digest match actual local PostgreSQL 16", {
  skip: !OPTED_IN,
}, async (t) => {
  const cases: Array<[string, unknown]> = [
    ["bounded public audit with lossless bigint message identifiers", AUDIT],
    ["PostgreSQL byte-length then lexical ASCII key order", {
      longer: 1, bb: 2, a: 3, AA: 4, aa: 5, B: 6, "": 7,
    }],
    ["nested arrays, booleans, null and UTC microseconds", {
      zz: [true, false, null, [], {}, ["2026-08-31T11:40:00.123456+00:00"]],
      audit: AUDIT,
      a: { long: null, z: false, aa: true, b: [1, 2, 3] },
    }],
    ["safe integer boundaries without exponent or precision loss", {
      maximum: Number.MAX_SAFE_INTEGER,
      minimum: Number.MIN_SAFE_INTEGER,
      positive: 1000,
      negative: -1000,
      zero: 0,
    }],
    ["ASCII escapes cannot become SQL or lose JSONB parity", {
      quote: "\"'; select 1; --",
      backslash: "\\",
      controls: "\b\f\n\r\t\u0001\u001f",
      slash: "/",
    }],
  ];
  for (const [name, value] of cases) {
    await t.test(name, () => {
      const pg = postgresCanonical(value);
      assert.equal(pg.read_only, "on");
      assert.ok(Number(pg.version) >= 160000 && Number(pg.version) < 170000);
      assert.equal(pgJsonbText(value), pg.text);
      assert.equal(sha256(pgJsonbText(value)), pg.sha256);
    });
  }

  await t.test("insertion-order differences do not change audited digests", () => {
    const reversed = Object.fromEntries(Object.entries(AUDIT).reverse());
    const pg = postgresCanonical(reversed);
    assert.equal(pgJsonbText(AUDIT), pg.text);
    assert.equal(sha256(pgJsonbText(AUDIT)), pg.sha256);
  });

  await t.test("request and audit manifests pin the PostgreSQL canonical hashes", () => {
    const validated = validateRequest(REQUEST, NOW);
    assert.equal(validated.request_sha256, postgresCanonical(REQUEST).sha256);
    assert.equal(validated.public_audit_sha256, postgresCanonical(AUDIT).sha256);
  });

  await t.test("complete PostgreSQL inspect-shaped subject survives strict response validation", () => {
    const validated = validateRequest(REQUEST, NOW);
    const pg = postgresInspectResponse();
    assert.equal(pg.response.approval_subject.delivery_started_at,
      "2026-08-31T11:40:00.123456+00:00");
    assert.equal(pg.response.approval_subject.expires_at,
      "2026-08-31T13:00:00+00:00");
    assert.equal(pg.response.approved_at, null);
    assert.equal(pgJsonbText(pg.response.approval_subject), pg.subject_text);
    assert.equal(sha256(pg.subject_text), pg.response.approval_subject_sha256);
    assert.deepEqual(
      validateInspectResponse(JSON.stringify(pg.response), validated, NOW),
      pg.response,
    );
  });

  await t.test("one PostgreSQL microsecond cannot cross the ten-minute attempt fence", () => {
    const validated = validateRequest(REQUEST, NOW);
    const allowed = postgresInspectResponse("2026-08-31T11:50:00Z").response;
    assert.deepEqual(validateInspectResponse(allowed, validated, NOW), allowed);
    const tooRecent = postgresInspectResponse("2026-08-31T11:50:00.000001Z").response;
    assert.equal(tooRecent.approval_subject.delivery_started_at,
      "2026-08-31T11:50:00.000001+00:00");
    assert.throws(() => validateInspectResponse(tooRecent, validated, NOW),
      /invalid_inspect_response/);
  });
});
