#!/usr/bin/env bash
set -euo pipefail

# Run only against the disposable, migrated PostgreSQL test database. Each
# iteration uses synthetic exact-delivery data from the concurrency fixture.
test_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
stale_log="${TMPDIR:-/tmp}/telegram-resolution-snapshot-$$.log"
signal_log="${TMPDIR:-/tmp}/telegram-resolution-snapshot-signal-$$.log"
stale_pid=""
signal_pid=""

export PGOPTIONS="${PGOPTIONS:+$PGOPTIONS }-c statement_timeout=20000 -c lock_timeout=8000"

cleanup() {
  status=$?
  set +e
  for pid in "$stale_pid" "$signal_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null
      wait "$pid" 2>/dev/null
    fi
  done
  rm -f "$stale_log" "$signal_log"
  exit "$status"
}
trap cleanup EXIT

wait_for_stale_snapshot() {
  local ready="f"
  for _attempt in $(seq 1 200); do
    if ! kill -0 "$stale_pid" 2>/dev/null; then
      wait "$stale_pid" || true
      cat "$stale_log" >&2
      return 1
    fi
    ready="$(psql -X -A -t -q -v ON_ERROR_STOP=1 \
      -c 'select not pg_catalog.pg_try_advisory_lock(20260831, 171)')"
    if [[ "$ready" == "t" ]]; then
      return 0
    fi
    sleep 0.05
  done
  echo "timed out waiting for the stale Telegram resolution snapshot" >&2
  return 1
}

for isolation in 'repeatable read' 'serializable'; do
  psql -X -q -v ON_ERROR_STOP=1 \
    -f "$test_dir/exact_telegram_delivery_unknown_resolution_concurrency_setup.sql"

  psql -X -q -v ON_ERROR_STOP=1 \
    -v isolation="$isolation" \
    -f "$test_dir/exact_telegram_delivery_unknown_resolution_snapshot_session.sql" \
    >"$stale_log" 2>&1 &
  stale_pid=$!
  wait_for_stale_snapshot

  # This separate READ COMMITTED connection commits a legitimate resolution
  # after the stale connection has taken its snapshot.
  psql -X -q -v ON_ERROR_STOP=1 \
    -v session_name=first \
    -v hold_lock=false \
    -f "$test_dir/exact_telegram_delivery_unknown_resolution_concurrency_resolve_session.sql"

  # Advisory locks do not use MVCC snapshots. Holding this bounded signal lock
  # proves the normal resolution committed without refreshing the stale view.
  psql -X -q -v ON_ERROR_STOP=1 \
    -f "$test_dir/exact_telegram_delivery_unknown_resolution_snapshot_signal.sql" \
    >"$signal_log" 2>&1 &
  signal_pid=$!

  if ! wait "$stale_pid"; then
    cat "$stale_log" >&2
    exit 1
  fi
  stale_pid=""
  if ! wait "$signal_pid"; then
    cat "$signal_log" >&2
    exit 1
  fi
  signal_pid=""

  psql -X -q -v ON_ERROR_STOP=1 \
    -f "$test_dir/exact_telegram_delivery_unknown_resolution_snapshot_verify.sql"
done

echo "exact Telegram stale-snapshot guards passed for repeatable read and serializable"
