import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, readdir, realpath, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";

import {
  APPROVAL_SCHEMA,
  APPROVAL_ACTOR_PLACEHOLDER,
  EXPECTED_CHECKS,
  PROJECT_REF,
  ReceiptJournal,
  boundedApprovalSubject,
  buildHistoryRegistrationSql,
  buildLockedMigrationSql,
  canonicalJson,
  loadProductionPack,
  newApprovalTemplate,
  queryManagementApi,
  runProductionApply,
  sha256,
  validateApproval,
  validateApprovedSubjectSha256,
  validateCheckRows,
} from "./production-apply-lib.mjs";

const REPOSITORY_ROOT = resolve(import.meta.dirname, "../..");
const RELEASE_SHA = "a".repeat(40);
const NOW = new Date("2026-09-01T12:30:00.000Z");
const CLI_PATH = resolve(import.meta.dirname, "production-apply.mjs");

function approvalFor(pack, overrides = {}) {
  const approval = {
    actions: [
      "apply_exact_migrations",
      "read_only_validate",
      "register_exact_history",
    ],
    approvalId: "22222222-2222-4222-8222-222222222222",
    approvalSubject: "",
    approvalSubjectSha256: "",
    approvedAt: "2026-09-01T12:00:00.000Z",
    approvedBy: "codex:production-migration",
    canonicalSetSha256: pack.manifest.canonicalSetSha256,
    environment: "production",
    expiresAt: "2026-09-01T14:00:00.000Z",
    genericDbPushAllowed: false,
    operationId: "11111111-1111-4111-8111-111111111111",
    projectRef: PROJECT_REF,
    releaseSha: RELEASE_SHA,
    runtimeActivationAllowed: false,
    schemaVersion: APPROVAL_SCHEMA,
    ...overrides,
  };
  approval.approvalSubject = boundedApprovalSubject(approval);
  approval.approvalSubjectSha256 = sha256(approval.approvalSubject);
  if (Object.hasOwn(overrides, "approvalSubject")) approval.approvalSubject = overrides.approvalSubject;
  if (Object.hasOwn(overrides, "approvalSubjectSha256")) approval.approvalSubjectSha256 = overrides.approvalSubjectSha256;
  return approval;
}

function approvedApply(approval) {
  return { approval, approvedSubjectSha256: approval.approvalSubjectSha256 };
}

function checkRows(kind) {
  const contract = EXPECTED_CHECKS[kind];
  return contract.ids.map((checkId) => ({
    check_id: checkId,
    custom_apply_receipt_required: true,
    exact_migration_bytes_proven: false,
    expected: "fixture",
    full_history_not_reconciled: true,
    generic_db_push_allowed: false,
    observed: "fixture",
    observed_byte_length: 7,
    observed_sha256: sha256("fixture"),
    pack: contract.pack,
    passed: true,
  }));
}

function successfulApplyResponses(pack) {
  return [
    checkRows("preflight"),
    [{ current_user: "postgres", session_user: "postgres", transaction_read_only: "off" }],
    [],
    [{ schema_version: "coineasy-managed-inspector-history-write@1", version: pack.migrations[0].version,
      history_name: pack.migrations[0].name, write_executor: "postgres", transaction_read_only: "off" }],
    checkRows("intermediate"),
    [],
    [{ schema_version: "coineasy-managed-inspector-history-write@1", version: pack.migrations[1].version,
      history_name: pack.migrations[1].name, write_executor: "postgres", transaction_read_only: "off" }],
    checkRows("postflight"),
  ];
}

function jsonResponse(value, status = 201, contentType = "application/json") {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": contentType },
  });
}

function journalFixture() {
  const events = [];
  return {
    events,
    async append(state, detail) {
      events.push({ state, detail });
      return sha256(JSON.stringify(events));
    },
  };
}

test("approval is exact, short-lived, production-only, and release-bound", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  assert.equal(validateApproval(approvalFor(pack), pack.manifest, RELEASE_SHA, NOW).environment, "production");
  const template = newApprovalTemplate({
    releaseSha: RELEASE_SHA,
    canonicalSetSha256: pack.manifest.canonicalSetSha256,
    now: NOW,
  });
  assert.equal(template.approvedBy, APPROVAL_ACTOR_PLACEHOLDER);
  assert.equal(template.approvalSubjectSha256.length, 64);
  assert.throws(
    () => validateApproval(template, pack.manifest, RELEASE_SHA, NOW),
    /approvedBy placeholder must be replaced/u,
  );
  assert.throws(
    () => validateApproval({ ...approvalFor(pack), runtimeActivationAllowed: true }, pack.manifest, RELEASE_SHA, NOW),
    /runtimeActivationAllowed/u,
  );
  assert.throws(
    () => validateApproval({ ...approvalFor(pack), releaseSha: "c".repeat(40) }, pack.manifest, RELEASE_SHA, NOW),
    /release SHA/u,
  );
  assert.throws(
    () => validateApproval(approvalFor(pack, { expiresAt: "2026-09-01T14:00:00.001Z" }), pack.manifest, RELEASE_SHA, NOW),
    /two hours/u,
  );
  assert.throws(
    () => validateApproval({ ...approvalFor(pack), approvalSubjectSha256: "c".repeat(64) }, pack.manifest, RELEASE_SHA, NOW),
    /does not bind/u,
  );
});

test("apply requires the separately supplied operator-approved subject hash", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const approval = approvalFor(pack);
  assert.equal(
    validateApprovedSubjectSha256(approval, approval.approvalSubjectSha256),
    approval.approvalSubjectSha256,
  );
  assert.throws(
    () => validateApprovedSubjectSha256(approval, "c".repeat(64)),
    /does not match approval packet/u,
  );
  assert.throws(
    () => validateApprovedSubjectSha256(approval, "NOT-A-SHA"),
    /64 lowercase hex/u,
  );
});

test("the direct runner rejects a missing or mismatched approved hash before journal and network", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const approval = approvalFor(pack);
  for (const approvedSubjectSha256 of [undefined, "c".repeat(64)]) {
    const journal = journalFixture();
    let calls = 0;
    await assert.rejects(runProductionApply({
      approval,
      approvedSubjectSha256,
      fetchImpl: async () => { calls += 1; throw new Error("network_must_not_run"); },
      journal,
      now: NOW,
      pack,
      releaseSha: RELEASE_SHA,
      token: "fixture-token-that-is-long-enough",
    }), /operator-approved subject SHA-256/u);
    assert.equal(calls, 0);
    assert.deepEqual(journal.events, []);
  }
});

test("the CLI rejects apply before file or network access when the approved hash is omitted", () => {
  const result = spawnSync(process.execPath, [
    CLI_PATH,
    "--apply",
    "--approval",
    "/private/tmp/approval-file-must-not-be-read.json",
    "--receipt-root",
    "/private/tmp/receipt-root-must-not-be-touched",
  ], { encoding: "utf8", timeout: 15_000 });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /--approved-subject-sha256_is_required_for_--apply/u);
  assert.doesNotMatch(result.stderr, /ENOENT|fetch|network/iu);
});

test("the CLI rejects a mismatched approved hash before credentials, receipt, or remote-main access", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const directory = await realpath(await mkdtemp(resolve(tmpdir(), "coineasy-apply-cli-gate-")));
  const releaseSha = spawnSync("git", ["-C", REPOSITORY_ROOT, "rev-parse", "HEAD"], {
    encoding: "utf8",
    timeout: 15_000,
  }).stdout.trim();
  const approvedAt = new Date();
  const approval = approvalFor(pack, {
    approvedAt: approvedAt.toISOString(),
    expiresAt: new Date(approvedAt.getTime() + 60 * 60 * 1000).toISOString(),
    releaseSha,
  });
  const approvalPath = resolve(directory, "approval.json");
  await writeFile(approvalPath, canonicalJson(approval), { encoding: "utf8", mode: 0o600 });
  const result = spawnSync(process.execPath, [
    CLI_PATH,
    "--apply",
    "--approval",
    approvalPath,
    "--approved-subject-sha256",
    "c".repeat(64),
    "--receipt-root",
    resolve(directory, "receipts-must-not-exist"),
  ], {
    encoding: "utf8",
    env: { ...process.env, HOME: resolve(directory, "missing-home") },
    timeout: 15_000,
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /operator-approved_subject_SHA-256_does_not_match_approval_packet/u);
  assert.doesNotMatch(result.stderr, /management_token|trusted_GitHub_main|ENOENT|fetch|network/iu);
  assert.deepEqual(await readdir(directory), ["approval.json"]);
});

test("history SQL stores exact source as hex data with no upsert or raw source text", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  for (const [index, migration] of pack.migrations.entries()) {
    const sql = buildHistoryRegistrationSql(migration, index + 1);
    assert.doesNotMatch(sql, /on\s+conflict/iu);
    assert.doesNotMatch(sql, /-- Additive/iu);
    assert.match(sql, new RegExp(migration.sha256, "u"));
    assert.match(sql, new RegExp(migration.bytes.subarray(0, 24).toString("hex"), "u"));
    assert.equal(Buffer.from(migration.bytes.toString("hex"), "hex").equals(migration.bytes), true);
    assert.match(sql, /lock table supabase_migrations\.schema_migrations/iu);
    assert.match(sql, /current_user::text <> 'postgres'/u);
  }
});

test("single-flight wrapper fences one exact raw migration inside an auto-releasing transaction lock", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  for (const [index, migration] of pack.migrations.entries()) {
    const sql = buildLockedMigrationSql(migration, index + 1);
    assert.equal(sql.split(migration.text).length - 1, 1);
    assert.match(sql, /pg_catalog\.pg_try_advisory_xact_lock/u);
    assert.doesNotMatch(sql, /pg_catalog\.pg_advisory_unlock/u);
    assert.ok(sql.indexOf("pg_try_advisory_xact_lock") < sql.indexOf(migration.text));
    assert.ok(sql.indexOf("fenced state is not clean") < sql.indexOf(migration.text));
    assert.match(sql, new RegExp(migration.sha256, "u"));
    assert.match(sql, /coineasy-managed-inspector-migration-write@2/u);
  }
});

test("management request preserves query text and never retries an HTTP failure", async () => {
  let calls = 0;
  const query = "select 'exact'::text";
  await assert.rejects(
    queryManagementApi({
      fetchImpl: async (_url, init) => {
        calls += 1;
        const payload = JSON.parse(Buffer.from(init.body).toString("utf8"));
        assert.equal(payload.query, query);
        assert.equal(payload.read_only, false);
        return jsonResponse({ error: "bounded fixture" }, 503);
      },
      query,
      readOnly: false,
      token: "fixture-token-that-is-long-enough",
    }),
    (error) => error.outcome === "indeterminate" && error.message === "provider_http_503",
  );
  assert.equal(calls, 1);
});

test("check result contract rejects a false, duplicate, missing, or oversized row", () => {
  const valid = checkRows("preflight");
  assert.equal(validateCheckRows(valid, "preflight").count, 26);
  assert.throws(() => validateCheckRows(valid.slice(1), "preflight"), /row count/u);
  assert.throws(
    () => validateCheckRows(valid.map((row, index) => index === 1 ? { ...row, check_id: valid[0].check_id } : row), "preflight"),
    /duplicate/u,
  );
  assert.throws(
    () => validateCheckRows(valid.map((row, index) => index === 1 ? { ...row, passed: false } : row), "preflight"),
    /check failed/u,
  );
  assert.throws(
    () => validateCheckRows(valid.map((row, index) => index === 1 ? { ...row, check_id: "unexpected_but_unique_check" } : row), "preflight"),
    /exact check IDs/u,
  );
  assert.throws(
    () => validateCheckRows(valid.map((row, index) => index === 1 ? { ...row, observed_byte_length: 4097 } : row), "preflight"),
    /false|4096/u,
  );
});

test("full mocked apply advances only through exact ordered gates", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const journal = journalFixture();
  const calls = [];
  const responses = successfulApplyResponses(pack);
  const fetchImpl = async (url, init) => {
    const payload = JSON.parse(Buffer.from(init.body).toString("utf8"));
    calls.push({ url, query: payload.query, readOnly: !Object.hasOwn(payload, "read_only") });
    return jsonResponse(responses[calls.length - 1]);
  };
  const outcome = await runProductionApply({
    ...approvedApply(approvalFor(pack)), fetchImpl, journal, now: NOW, pack,
    releaseSha: RELEASE_SHA, token: "fixture-token-that-is-long-enough",
  });
  assert.equal(outcome.ok, true);
  assert.equal(calls.length, 8);
  assert.equal(calls[0].query, pack.sql.preflight.text);
  assert.equal(calls[2].query, buildLockedMigrationSql(pack.migrations[0], 1));
  assert.equal(calls[2].query.split(pack.migrations[0].text).length - 1, 1);
  assert.equal(calls[4].query, pack.sql.intermediate.text);
  assert.equal(calls[5].query, buildLockedMigrationSql(pack.migrations[1], 2));
  assert.equal(calls[5].query.split(pack.migrations[1].text).length - 1, 1);
  assert.equal(calls[7].query, pack.sql.postflight.text);
  assert.deepEqual(journal.events.map(({ state }) => state), [
    "PACK_VERIFIED", "PREFLIGHT_VERIFIED", "WRITE_EXECUTOR_VERIFIED",
    "M1_SEND_INTENT", "M1_COMMIT_RESPONSE_RECEIVED",
    "M1_HISTORY_SEND_INTENT", "M1_HISTORY_RESPONSE_RECEIVED", "INTERMEDIATE_VERIFIED",
    "M2_SEND_INTENT", "M2_COMMIT_RESPONSE_RECEIVED",
    "M2_HISTORY_SEND_INTENT", "M2_HISTORY_RESPONSE_RECEIVED",
    "POSTFLIGHT_VERIFIED", "COMPLETE",
  ]);
  assert.equal(journal.events.at(-1).detail.runtimeActivationAllowed, false);
  assert.equal(journal.events.at(-1).detail.telegramSent, false);
});

test("caller mutation after entry cannot change the approved immutable packet", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const approval = approvalFor(pack);
  const originalOperationId = approval.operationId;
  const approvedSubjectSha256 = approval.approvalSubjectSha256;
  const journal = journalFixture();
  const append = journal.append.bind(journal);
  journal.append = async (state, detail) => {
    const receipt = await append(state, detail);
    if (state === "PACK_VERIFIED") {
      approval.operationId = "77777777-7777-4777-8777-777777777777";
      approval.approvalId = "88888888-8888-4888-8888-888888888888";
      approval.approvedBy = "unapproved:mutated-caller";
      approval.approvalSubject = boundedApprovalSubject(approval);
      approval.approvalSubjectSha256 = sha256(approval.approvalSubject);
    }
    return receipt;
  };
  const responses = successfulApplyResponses(pack);
  let calls = 0;
  const outcome = await runProductionApply({
    approval,
    approvedSubjectSha256,
    fetchImpl: async () => jsonResponse(responses[calls++]),
    journal,
    now: NOW,
    pack,
    releaseSha: RELEASE_SHA,
    token: "fixture-token-that-is-long-enough",
  });
  assert.equal(calls, 8);
  assert.equal(outcome.operationId, originalOperationId);
  assert.notEqual(approval.operationId, originalOperationId);
  assert.notEqual(approval.approvalSubjectSha256, approvedSubjectSha256);
});

test("ambiguous first migration response stops before history and second migration", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const journal = journalFixture();
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    if (calls === 1) return jsonResponse(checkRows("preflight"));
    if (calls === 2) return jsonResponse([{ current_user: "postgres", session_user: "postgres", transaction_read_only: "off" }]);
    return jsonResponse({ error: "fixture" }, 503);
  };
  await assert.rejects(runProductionApply({
    ...approvedApply(approvalFor(pack)), fetchImpl, journal, now: NOW, pack,
    releaseSha: RELEASE_SHA, token: "fixture-token-that-is-long-enough",
  }), /provider_http_503/u);
  assert.equal(calls, 3);
  assert.equal(journal.events.at(-1).state, "OUTCOME_UNKNOWN");
  assert.equal(journal.events.some(({ state }) => state.includes("HISTORY_SEND")), false);
});

test("history ambiguity records pending and never sends the next migration", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const journal = journalFixture();
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    if (calls === 1) return jsonResponse(checkRows("preflight"));
    if (calls === 2) return jsonResponse([{ current_user: "postgres", session_user: "postgres", transaction_read_only: "off" }]);
    if (calls === 3) return jsonResponse([]);
    return new Response("not-json", { status: 201, headers: { "content-type": "application/json" } });
  };
  await assert.rejects(runProductionApply({
    ...approvedApply(approvalFor(pack)), fetchImpl, journal, now: NOW, pack,
    releaseSha: RELEASE_SHA, token: "fixture-token-that-is-long-enough",
  }), /provider_json_invalid/u);
  assert.equal(calls, 4);
  assert.equal(journal.events.at(-1).state, "M1_HISTORY_PENDING");
  assert.equal(journal.events.some(({ state }) => state === "M2_SEND_INTENT"), false);
});

test("approval expiration is rechecked before the first mutation", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const journal = journalFixture();
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    if (calls === 1) return jsonResponse(checkRows("preflight"));
    return jsonResponse([{ current_user: "postgres", session_user: "postgres", transaction_read_only: "off" }]);
  };
  let clockCalls = 0;
  const now = () => {
    clockCalls += 1;
    return clockCalls < 3 ? NOW : new Date("2026-09-01T14:00:00.000Z");
  };
  await assert.rejects(runProductionApply({
    ...approvedApply(approvalFor(pack)), fetchImpl, journal, now, pack,
    releaseSha: RELEASE_SHA, token: "fixture-token-that-is-long-enough",
  }), /expired/u);
  assert.equal(calls, 2);
  assert.equal(journal.events.at(-1).state, "ABORTED_CLEAN");
  assert.equal(journal.events.some(({ state }) => state === "M1_SEND_INTENT"), false);
});

test("two hosts racing M1 allow only one single-flight winner", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const journalA = journalFixture();
  const journalB = journalFixture();
  let resolveFirstLocked;
  let releaseFirstLock;
  const firstLocked = new Promise((resolve) => { resolveFirstLocked = resolve; });
  const firstRelease = new Promise((resolve) => { releaseFirstLock = resolve; });
  let lockHeld = false;
  let firstWinnerChosen = false;
  const fetchImpl = async (_url, init) => {
    const { query } = JSON.parse(Buffer.from(init.body).toString("utf8"));
    if (query === pack.sql.preflight.text) return jsonResponse(checkRows("preflight"));
    if (query.includes("current_user::text as current_user")) {
      return jsonResponse([{ current_user: "postgres", session_user: "postgres", transaction_read_only: "off" }]);
    }
    if (query.startsWith("-- coineasy-managed-inspector-transaction-fenced-migration@2")) {
      if (query.includes(pack.migrations[0].text) && !firstWinnerChosen) {
        firstWinnerChosen = true;
        lockHeld = true;
        resolveFirstLocked();
        await firstRelease;
        lockHeld = false;
        return jsonResponse([]);
      }
      if (lockHeld) return jsonResponse({ error: "single-flight fixture" }, 409);
      return jsonResponse([]);
    }
    if (query.startsWith("-- generated by production-apply-lib.mjs")) {
      const migration = query.includes(
        `values ('${pack.migrations[0].version}','${pack.migrations[0].name}',array[`,
      )
        ? pack.migrations[0]
        : pack.migrations[1];
      return jsonResponse([{
        schema_version: "coineasy-managed-inspector-history-write@1",
        version: migration.version,
        history_name: migration.name,
        write_executor: "postgres",
        transaction_read_only: "off",
      }]);
    }
    if (query === pack.sql.intermediate.text) return jsonResponse(checkRows("intermediate"));
    if (query === pack.sql.postflight.text) return jsonResponse(checkRows("postflight"));
    throw new Error("unexpected_mock_query");
  };
  const runA = runProductionApply({
    ...approvedApply(approvalFor(pack)), fetchImpl, journal: journalA, now: NOW, pack,
    releaseSha: RELEASE_SHA, token: "fixture-token-that-is-long-enough",
  });
  await firstLocked;
  const runB = runProductionApply({
    ...approvedApply(approvalFor(pack, {
      operationId: "33333333-3333-4333-8333-333333333333",
      approvalId: "44444444-4444-4444-8444-444444444444",
    })),
    fetchImpl, journal: journalB, now: NOW, pack,
    releaseSha: RELEASE_SHA, token: "fixture-token-that-is-long-enough",
  });
  await assert.rejects(runB, /provider_http_409/u);
  assert.equal(journalB.events.at(-1).state, "OUTCOME_UNKNOWN");
  assert.equal(journalB.events.some(({ state }) => state.includes("HISTORY_SEND")), false);
  releaseFirstLock();
  assert.equal((await runA).ok, true);
});

test("a stale-preflight host is fenced after the winner releases the M1 transaction lock", async () => {
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  const journalA = journalFixture();
  const journalB = journalFixture();
  let resolveM1Committed;
  let releaseM1History;
  const m1CommittedSignal = new Promise((resolve) => { resolveM1Committed = resolve; });
  const m1HistoryRelease = new Promise((resolve) => { releaseM1History = resolve; });
  let m1Committed = false;
  let m1HistoryHeld = false;
  const fetchImpl = async (_url, init) => {
    const { query } = JSON.parse(Buffer.from(init.body).toString("utf8"));
    if (query === pack.sql.preflight.text) return jsonResponse(checkRows("preflight"));
    if (query.includes("current_user::text as current_user")) {
      return jsonResponse([{ current_user: "postgres", session_user: "postgres", transaction_read_only: "off" }]);
    }
    if (query.startsWith("-- coineasy-managed-inspector-transaction-fenced-migration@2")) {
      if (query.includes(pack.migrations[0].text)) {
        if (m1Committed) return jsonResponse({ error: "fenced-state fixture" }, 409);
        m1Committed = true;
        resolveM1Committed();
      }
      return jsonResponse([]);
    }
    if (query.startsWith("-- generated by production-apply-lib.mjs")) {
      const migration = query.includes(
        `values ('${pack.migrations[0].version}','${pack.migrations[0].name}',array[`,
      )
        ? pack.migrations[0]
        : pack.migrations[1];
      if (migration === pack.migrations[0] && !m1HistoryHeld) {
        m1HistoryHeld = true;
        await m1HistoryRelease;
      }
      return jsonResponse([{
        schema_version: "coineasy-managed-inspector-history-write@1",
        version: migration.version,
        history_name: migration.name,
        write_executor: "postgres",
        transaction_read_only: "off",
      }]);
    }
    if (query === pack.sql.intermediate.text) return jsonResponse(checkRows("intermediate"));
    if (query === pack.sql.postflight.text) return jsonResponse(checkRows("postflight"));
    throw new Error("unexpected_mock_query");
  };
  const runA = runProductionApply({
    ...approvedApply(approvalFor(pack)), fetchImpl, journal: journalA, now: NOW, pack,
    releaseSha: RELEASE_SHA, token: "fixture-token-that-is-long-enough",
  });
  await m1CommittedSignal;
  const runB = runProductionApply({
    ...approvedApply(approvalFor(pack, {
      operationId: "55555555-5555-4555-8555-555555555555",
      approvalId: "66666666-6666-4666-8666-666666666666",
    })),
    fetchImpl, journal: journalB, now: NOW, pack,
    releaseSha: RELEASE_SHA, token: "fixture-token-that-is-long-enough",
  });
  await assert.rejects(runB, /provider_http_409/u);
  assert.equal(journalB.events.at(-1).state, "OUTCOME_UNKNOWN");
  assert.equal(journalB.events.some(({ state }) => state.includes("HISTORY_SEND")), false);
  releaseM1History();
  assert.equal((await runA).ok, true);
});

test("append-only receipt rejects secrets and existing event paths", async () => {
  const directory = await mkdtemp(resolve(tmpdir(), "coineasy-apply-receipt-"));
  const journal = new ReceiptJournal({
    directory,
    operationId: "11111111-1111-4111-8111-111111111111",
    approvalId: "22222222-2222-4222-8222-222222222222",
    approvalSubjectSha256: "b".repeat(64),
    forbiddenValues: ["forbidden-secret-value"],
  });
  const digest = await journal.append("PACK_VERIFIED", { safe: true });
  assert.match(digest, /^[a-f0-9]{64}$/u);
  const raw = await readFile(resolve(directory, "000-pack_verified.json"), "utf8");
  assert.equal(raw, canonicalJson(JSON.parse(raw)));
  await assert.rejects(journal.append("TOKEN_TEST", { value: "forbidden-secret-value" }), /forbidden/u);
});
