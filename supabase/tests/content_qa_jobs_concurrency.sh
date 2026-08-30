#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
claim_log="${TMPDIR:-/tmp}/content-qa-claim-$$.log"
poll_log="${TMPDIR:-/tmp}/content-qa-poll-$$.log"
claim_pid=""
poll_pid=""

cleanup() {
  status=$?
  set +e
  if [[ -n "$claim_pid" ]] && kill -0 "$claim_pid" 2>/dev/null; then
    kill "$claim_pid" 2>/dev/null
    wait "$claim_pid" 2>/dev/null
  fi
  if [[ -n "$poll_pid" ]] && kill -0 "$poll_pid" 2>/dev/null; then
    kill "$poll_pid" 2>/dev/null
    wait "$poll_pid" 2>/dev/null
  fi
  psql -X -v ON_ERROR_STOP=1 \
    -f "$test_dir/content_qa_jobs_concurrency_cleanup.sql"
  cleanup_status=$?
  rm -f "$claim_log" "$poll_log"
  if [[ "$status" -ne 0 ]]; then
    exit "$status"
  fi
  exit "$cleanup_status"
}
trap cleanup EXIT

wait_for_signal() {
  local signal_key="$1"
  local session_pid="$2"
  local ready="f"
  for _attempt in $(seq 1 100); do
    if ! kill -0 "$session_pid" 2>/dev/null; then
      wait "$session_pid"
      return 1
    fi
    ready="$(psql -X -A -t -q -v ON_ERROR_STOP=1 \
      -c "select not pg_catalog.pg_try_advisory_lock(20260830, ${signal_key})")"
    if [[ "$ready" == "t" ]]; then
      return 0
    fi
    sleep 0.05
  done
  echo "timed out waiting for Content QA concurrency session" >&2
  return 1
}

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/content_qa_jobs_concurrency_cleanup.sql"
psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/content_qa_jobs_concurrency_setup.sql"

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/content_qa_jobs_concurrency_claim_session.sql" \
  >"$claim_log" 2>&1 &
claim_pid=$!
wait_for_signal 1 "$claim_pid"

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/content_qa_jobs_concurrency_record_session.sql"
if ! wait "$claim_pid"; then
  cat "$claim_log" >&2
  exit 1
fi
claim_pid=""

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/content_qa_jobs_concurrency_poll_session.sql" \
  >"$poll_log" 2>&1 &
poll_pid=$!
wait_for_signal 2 "$poll_pid"

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/content_qa_jobs_concurrency_stale_session.sql"
if ! wait "$poll_pid"; then
  cat "$poll_log" >&2
  exit 1
fi
poll_pid=""
