#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
poll_log="${TMPDIR:-/tmp}/squid-recovery-poll-$$.log"
trap 'rm -f "$poll_log"' EXIT

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/squid_failed_draft_recovery_concurrency_setup.sql"

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/squid_failed_draft_recovery_poll_session.sql" \
  >"$poll_log" 2>&1 &
poll_pid=$!

poll_ready="f"
for _attempt in $(seq 1 50); do
  poll_ready="$(psql -X -A -t -v ON_ERROR_STOP=1 \
    -c 'select not pg_catalog.pg_try_advisory_lock(20260825, 1)')"
  if [[ "$poll_ready" == "t" ]]; then
    break
  fi
  sleep 0.1
done
test "$poll_ready" = "t"

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/squid_failed_draft_recovery_recovery_session.sql"
wait "$poll_pid"

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/squid_failed_draft_recovery_time_poll_session.sql" \
  >"$poll_log" 2>&1 &
poll_pid=$!

poll_ready="f"
for _attempt in $(seq 1 50); do
  poll_ready="$(psql -X -A -t -v ON_ERROR_STOP=1 \
    -c 'select not pg_catalog.pg_try_advisory_lock(20260825, 2)')"
  if [[ "$poll_ready" == "t" ]]; then
    break
  fi
  sleep 0.1
done
test "$poll_ready" = "t"

psql -X -v ON_ERROR_STOP=1 \
  -f "$test_dir/squid_failed_draft_recovery_time_session.sql"
wait "$poll_pid"
