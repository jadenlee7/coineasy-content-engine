#!/usr/bin/env node
/** Default-off exact migration runner. --apply requires a separate operator hash. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { chmod, lstat, mkdir, open, readFile, realpath } from 'node:fs/promises';
import { dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  parseCanonicalJson, ReceiptJournal, sha256,
} from '../managed-inspector-activation/production-apply-lib.mjs';
import { buildAtomicProducerBindingSql } from './atomic-apply-sql.mjs';
import {
  canonicalJson, newApprovalTemplate, runProductionApply, validateApproval,
} from './production-apply-lib.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const SOURCE = 'supabase/migrations/20260916190000_content_ops_review_producer_binding.sql';
const PREFLIGHT = 'supabase/proposals/content_ops_review_producer_binding_contract_readonly.sql';

function parseArgs(args) {
  const options = {};
  while (args.length) {
    const arg = args.shift();
    if (['--template', '--validate', '--apply'].includes(arg)) {
      assert.equal(options.mode, undefined, 'choose one mode');
      options.mode = arg.slice(2);
    } else if (['--approval', '--approved-subject-sha256', '--receipt-root'].includes(arg)) {
      const value = args.shift();
      assert.equal(typeof value, 'string', `${arg} needs a value`);
      options[arg.slice(2)] = value;
    } else throw new Error(`unknown_argument:${arg}`);
  }
  assert.ok(options.mode, 'choose --template, --validate or --apply');
  if (options.mode === 'template') {
    assert.deepEqual(Object.keys(options), ['mode']);
  } else {
    assert.equal(isAbsolute(options.approval ?? ''), true, 'absolute approval path required');
    if (options.mode === 'apply') {
      assert.equal(isAbsolute(options['receipt-root'] ?? ''), true, 'absolute receipt root required');
      assert.match(options['approved-subject-sha256'] ?? '', /^[a-f0-9]{64}$/u,
        'separate operator-approved subject SHA-256 required');
    } else {
      assert.equal(options['receipt-root'], undefined);
      assert.equal(options['approved-subject-sha256'], undefined);
    }
  }
  return options;
}

function git(...args) {
  return execFileSync('git', ['-C', ROOT, ...args], {
    encoding: 'utf8', timeout: 15_000,
    env: { PATH: process.env.PATH, LANG: 'C', TZ: 'UTC' },
  }).trim();
}

function exactMain() {
  const head = git('rev-parse', 'HEAD');
  assert.match(head, /^[a-f0-9]{40}$/u);
  assert.equal(git('status', '--porcelain'), '', 'checkout must be clean');
  assert.equal(git('rev-parse', 'origin/main'), head, 'checkout must be exact origin/main');
  assert.equal(git('ls-remote',
    'https://github.com/jadenlee7/coineasy-content-engine.git',
    'refs/heads/main'), `${head}\trefs/heads/main`, 'trusted main SHA differs');
  return head;
}

async function regularFile(path) {
  const stat = await lstat(path);
  assert.equal(stat.isFile(), true, 'canonical regular file required');
  assert.equal(stat.isSymbolicLink(), false, 'symlink forbidden');
  return readFile(path, 'utf8');
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const source = await regularFile(resolve(ROOT, SOURCE));
  buildAtomicProducerBindingSql(source); // exact bytes before any mode or network
  const preflightSql = await regularFile(resolve(ROOT, PREFLIGHT));
  const preflightSha256 = sha256(Buffer.from(preflightSql));
  const releaseSha = git('rev-parse', 'HEAD');
  if (options.mode === 'template') {
    process.stdout.write(canonicalJson(newApprovalTemplate({ releaseSha, preflightSha256 })));
    process.stderr.write('TEMPLATE ONLY: actor placeholder is invalid; no production request.\n');
    return;
  }
  const approvalPath = await realpath(options.approval);
  assert.equal(approvalPath, options.approval, 'approval path must not be a symlink');
  const approval = parseCanonicalJson(await regularFile(approvalPath), 'approval');
  validateApproval(approval, { releaseSha, preflightSha256,
    approvedSubjectSha256: options['approved-subject-sha256'] });
  if (options.mode === 'validate') {
    process.stdout.write(`${JSON.stringify({ status: 'offline_validate_only',
      releaseSha, migrationSha256: approval.migrationSha256,
      productionWrites: 0, providerCalls: 0 })}\n`);
    return;
  }
  assert.equal(exactMain(), releaseSha);
  const receiptRoot = await realpath(options['receipt-root']);
  assert.equal(receiptRoot, options['receipt-root'], 'receipt root must not be a symlink');
  const rootStat = await lstat(receiptRoot);
  assert.equal(rootStat.isDirectory(), true);
  assert.equal(rootStat.mode & 0o077, 0, 'receipt root must be private');
  const token = (await readFile(resolve(process.env.HOME ?? '', '.supabase/access-token'), 'utf8')).trim();
  assert.ok(token.length >= 20 && !/\s/u.test(token), 'management token unavailable');
  const receiptDirectory = resolve(receiptRoot, approval.operationId);
  assert.equal(receiptDirectory.startsWith(`${receiptRoot}/`), true);
  await mkdir(receiptDirectory, { mode: 0o700, recursive: false });
  await chmod(receiptDirectory, 0o700);
  const rootHandle = await open(receiptRoot, 'r');
  try { await rootHandle.sync(); } finally { await rootHandle.close(); }
  const journal = new ReceiptJournal({ directory: receiptDirectory,
    operationId: approval.operationId, approvalId: approval.approvalId,
    approvalSubjectSha256: approval.approvalSubjectSha256,
    forbiddenValues: [token, source.slice(0, 64)] });
  const result = await runProductionApply({
    approval, approvedSubjectSha256: options['approved-subject-sha256'],
    source, preflightSql, fetchImpl: fetch, token, journal, releaseSha,
  });
  process.stdout.write(`${JSON.stringify({ ...result,
    finalReceiptSha256: journal.previousReceiptSha256 })}\n`);
}

main().catch((error) => {
  process.stderr.write(`Producer-binding apply stopped: ${String(error?.message ?? 'unknown').replace(/[^A-Za-z0-9_.:-]/gu, '_').slice(0, 160)}\n`);
  process.exitCode = 1;
});
