#!/usr/bin/env bash
set -euo pipefail

test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
first_log="${TMPDIR:-/tmp}/telegram-resolution-first-$$.log"
second_log="${TMPDIR:-/tmp}/telegram-resolution-second-$$.log"
first_pid=""
second_pid=""

export PGOPTIONS="${PGOPTIONS:+$PGOPTIONS }-c statement_timeout=15000 -c lock_timeout=8000"

cleanup() {
  status=$?
  set +e
  if [[ -n "$first_pid" ]] && kill -0 "$first_pid" 2>/dev/null; then
    kill "$first_pid" 2>/dev/null
    wait "$first_pid" 2>/dev/null
  fi
  if [[ -n "$second_pid" ]] && kill -0 "$second_pid" 2>/dev/null; then
    kill "$second_pid" 2>/dev/null
    wait "$second_pid" 2>/dev/null
  fi
  rm -f "$first_log" "$second_log"
  exit "$status"
}
trap cleanup EXIT

wait_for_first_session() {
  local ready="f"
  for _attempt in $(seq 1 100); do
    if ! kill -0 "$first_pid" 2>/dev/null; then
      wait "$first_pid" || true
      cat "$first_log" >&2
      return 1
    fi
    ready="$(psql -X -A -t -q -v ON_ERROR_STOP=1 \
      -c 'select not pg_catalog.pg_try_advisory_lock(20260831, 170)')"
    if [[ "$ready" == "t" ]]; then
      return 0
    fi
    sleep 0.05
  done
  echo "timed out waiting for the first Telegram resolve session" >&2
  return 1
}

psql -X -q -v ON_ERROR_STOP=1 \
  -f "$test_dir/exact_telegram_delivery_unknown_resolution_concurrency_setup.sql"

psql -X -q -v ON_ERROR_STOP=1 \
  -v session_name=first \
  -v hold_lock=true \
  -f "$test_dir/exact_telegram_delivery_unknown_resolution_concurrency_resolve_session.sql" \
  >"$first_log" 2>&1 &
first_pid=$!
wait_for_first_session

psql -X -q -v ON_ERROR_STOP=1 \
  -v session_name=second \
  -v hold_lock=false \
  -f "$test_dir/exact_telegram_delivery_unknown_resolution_concurrency_resolve_session.sql" \
  >"$second_log" 2>&1 &
second_pid=$!

if ! wait "$first_pid"; then
  cat "$first_log" >&2
  exit 1
fi
first_pid=""
if ! wait "$second_pid"; then
  cat "$second_log" >&2
  exit 1
fi
second_pid=""

psql -X -q -v ON_ERROR_STOP=1 \
  -f "$test_dir/exact_telegram_delivery_unknown_resolution_concurrency_verify.sql"
