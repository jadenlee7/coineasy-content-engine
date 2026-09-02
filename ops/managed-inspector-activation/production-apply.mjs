#!/usr/bin/env node

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir, open, readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  ReceiptJournal,
  canonicalJson,
  loadProductionPack,
  newApprovalTemplate,
  parseCanonicalJson,
  runProductionApply,
  validateApprovedSubjectSha256,
  validateApproval,
} from "./production-apply-lib.mjs";

const PACK_DIRECTORY = dirname(fileURLToPath(import.meta.url));
const REPOSITORY_ROOT = resolve(PACK_DIRECTORY, "../..");

function usage() {
  return `Usage:
  node ops/managed-inspector-activation/production-apply.mjs --template
  node ops/managed-inspector-activation/production-apply.mjs --validate --approval /absolute/approval.json
  node ops/managed-inspector-activation/production-apply.mjs --apply --approval /absolute/approval.json --approved-subject-sha256 <64-lowercase-hex> --receipt-root /absolute/new/private-directory

Default behavior is offline. --apply is the only network/write mode and never activates runtime, creates Auth users, or contacts Telegram/providers.\n`;
}

function parseArguments(args) {
  const options = { apply: false, template: false, validate: false };
  while (args.length) {
    const argument = args.shift();
    if (argument === "--apply") options.apply = true;
    else if (argument === "--template") options.template = true;
    else if (argument === "--validate") options.validate = true;
    else if (argument === "--approval") options.approval = args.shift();
    else if (argument === "--approved-subject-sha256") options.approvedSubjectSha256 = args.shift();
    else if (argument === "--receipt-root") options.receiptRoot = args.shift();
    else throw new Error(`unknown_argument:${argument}`);
  }
  assert.equal([options.apply, options.template, options.validate].filter(Boolean).length, 1,
    "choose exactly one of --template, --validate, or --apply");
  if (!options.template) {
    assert.equal(typeof options.approval, "string", "--approval is required");
    assert.equal(isAbsolute(options.approval), true, "approval path must be absolute");
  }
  if (options.apply) {
    assert.equal(typeof options.receiptRoot, "string", "--receipt-root is required");
    assert.equal(isAbsolute(options.receiptRoot), true, "receipt root must be absolute");
    assert.equal(
      typeof options.approvedSubjectSha256,
      "string",
      "--approved-subject-sha256 is required for --apply",
    );
  } else {
    assert.equal(
      options.approvedSubjectSha256,
      undefined,
      "--approved-subject-sha256 is valid only with --apply",
    );
  }
  return options;
}

function git(...args) {
  return execFileSync("git", ["-C", REPOSITORY_ROOT, ...args], {
    encoding: "utf8",
    timeout: 15_000,
    env: { PATH: process.env.PATH, LANG: "C", TZ: "UTC" },
  }).trim();
}

function exactReleaseSha() {
  const head = git("rev-parse", "HEAD");
  assert.match(head, /^[a-f0-9]{40}$/u);
  assert.equal(git("status", "--porcelain"), "", "production apply requires a clean checkout");
  assert.equal(git("rev-parse", "origin/main"), head, "production apply requires exact origin/main");
  const remoteMain = git(
    "ls-remote",
    "https://github.com/jadenlee7/coineasy-content-engine.git",
    "refs/heads/main",
  );
  assert.equal(remoteMain, `${head}\trefs/heads/main`, "trusted GitHub main does not match checkout");
  return head;
}

async function readApproval(path) {
  const canonicalPath = await realpath(path);
  assert.equal(canonicalPath, path, "approval path must already be canonical and non-symlinked");
  return parseCanonicalJson(await readFile(path, "utf8"), "approval");
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const pack = await loadProductionPack(REPOSITORY_ROOT);
  if (options.template) {
    process.stdout.write(canonicalJson(newApprovalTemplate({
      releaseSha: git("rev-parse", "HEAD"),
      canonicalSetSha256: pack.manifest.canonicalSetSha256,
    })));
    process.stderr.write("TEMPLATE ONLY: replace every approval field; no network or database action occurred.\n");
    return;
  }
  const approval = await readApproval(options.approval);
  const releaseSha = git("rev-parse", "HEAD");
  validateApproval(approval, pack.manifest, releaseSha);
  if (options.apply) {
    validateApprovedSubjectSha256(approval, options.approvedSubjectSha256);
    assert.equal(exactReleaseSha(), releaseSha);
  }
  if (options.validate) {
    process.stdout.write(`${JSON.stringify({
      ok: true,
      mode: "offline_validate_only",
      releaseSha,
      canonicalSetSha256: pack.manifest.canonicalSetSha256,
      productionWrites: 0,
    })}\n`);
    return;
  }

  const tokenPath = resolve(process.env.HOME ?? "", ".supabase/access-token");
  const token = (await readFile(tokenPath, "utf8")).trim();
  assert.ok(token.length >= 20 && !/\s/u.test(token), "management token unavailable");
  const canonicalReceiptRoot = await realpath(options.receiptRoot);
  assert.equal(canonicalReceiptRoot, options.receiptRoot, "receipt root must be canonical and non-symlinked");
  const receiptDirectory = resolve(canonicalReceiptRoot, approval.operationId);
  assert.equal(receiptDirectory.startsWith(`${canonicalReceiptRoot}/`), true);
  await mkdir(receiptDirectory, { mode: 0o700, recursive: false });
  const receiptRootHandle = await open(canonicalReceiptRoot, "r");
  try {
    await receiptRootHandle.sync();
  } finally {
    await receiptRootHandle.close();
  }
  const journal = new ReceiptJournal({
    directory: receiptDirectory,
    operationId: approval.operationId,
    approvalId: approval.approvalId,
    approvalSubjectSha256: approval.approvalSubjectSha256,
    forbiddenValues: [
      token,
      ...pack.migrations.map((migration) => migration.text.slice(0, 64)),
    ],
  });
  const outcome = await runProductionApply({
    approval,
    approvedSubjectSha256: options.approvedSubjectSha256,
    fetchImpl: fetch,
    journal,
    pack,
    releaseSha,
    token,
  });
  process.stdout.write(`${JSON.stringify(outcome)}\n`);
}

main().catch((error) => {
  process.stderr.write(`Production apply stopped: ${String(error?.message ?? "unknown").replace(/[^A-Za-z0-9_.:-]/gu, "_").slice(0, 180)}\n`);
  process.exitCode = 1;
});
