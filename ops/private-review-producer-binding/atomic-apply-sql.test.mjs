import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
  buildAtomicProducerBindingSql,
  MIGRATION_NAME, MIGRATION_SHA256, MIGRATION_VERSION,
} from './atomic-apply-sql.mjs';

const source = readFileSync(resolve(import.meta.dirname,
  '../../supabase/migrations/20260916190000_content_ops_review_producer_binding.sql'), 'utf8');

test('one exact migration and history registration share one transaction', () => {
  const sql = buildAtomicProducerBindingSql(source);
  assert.equal((sql.match(/^begin;$/gmu) ?? []).length, 1);
  assert.equal((sql.match(/^commit;$/gmu) ?? []).length, 1);
  assert.match(sql, /lock table supabase_migrations\.schema_migrations/iu);
  assert.match(sql, /set local search_path=pg_catalog;/u);
  assert.match(sql, /producer_binding_apply_precondition_mismatch/u);
  assert.match(sql, /producer_binding_apply_postcondition_mismatch/u);
  assert.match(sql, new RegExp(MIGRATION_VERSION, 'u'));
  assert.match(sql, new RegExp(MIGRATION_NAME, 'u'));
  assert.match(sql, new RegExp(MIGRATION_SHA256, 'u'));
  assert.ok(sql.indexOf('create or replace function private.content_ops_review_candidate(')
    < sql.indexOf('insert into supabase_migrations.schema_migrations('));
});

test('changed migration bytes cannot produce a write statement', () => {
  assert.throws(() => buildAtomicProducerBindingSql(source.replace('job backfill', 'job backfilL')),
    /migration bytes changed/u);
  assert.throws(() => buildAtomicProducerBindingSql(source.slice(0, -1)),
    /migration byte length changed/u);
});
