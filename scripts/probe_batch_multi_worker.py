"""Race N real BatchDispatchers over one ledger and prove nothing is submitted twice.

This is a development harness, not a test. It needs a live Postgres carrying the
ledger schema, so it cannot run inside the transactional CI suite — see
`supabase/tests/agent_batch_ledger_multi_worker_security.sql` for the part that
does.

What it exercises is everything except the provider transport. Only
`OpenAIBatchClient` is replaced, with a recorder; the real `BatchDispatcher`,
the real `SupabaseBatchRepository` parsing and error classification, the real
policy routing, dispatch-key derivation, and the real ledger routines all run.
`SupabaseBatchRepository._rpc` is overridden to call the routine directly
instead of over HTTP, with argument casts read from `pg_proc` so the call
resolves to the same signature PostgREST would pick.

Usage:

    pip install "psycopg[binary]"
    createdb probe && psql -d probe \\
        -f supabase/tests/bootstrap_local_postgres.sql
    for m in supabase/migrations/*.sql; do psql -d probe -f "$m"; done
    python -m scripts.probe_batch_multi_worker --dsn "dbname=probe"

Exit status is 0 only when every job was submitted exactly once.

Payload invariants worth knowing before changing the seed below: a Batch item
must set `approval_required`, its `output_schema` must be closed
(`additionalProperties: false`), risk tiers `T3`/`T4` route to manual sync
rather than Batch, and a daily budget hard cap tops out at 6,000,000 microUSD.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import uuid

import psycopg
from psycopg.rows import tuple_row

from core.batch.dispatcher import BatchDispatcher
from core.batch.models import BatchSnapshot, canonical_input_sha256
from core.batch.policy import BatchPolicy
from core.batch.repository import BatchRepositoryError, SupabaseBatchRepository

WORKSPACE_ID = "e0000000-0000-4000-8000-000000000001"
CLIENT_ID = "squid"
BUDGET_KEY = "openai:probe"
OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"headline": {"type": "string"}},
    "required": ["headline"],
}
INSTRUCTIONS = "Summarise the source into one Korean headline."


class LedgerRepository(SupabaseBatchRepository):
    """The real repository with its HTTP transport swapped for a direct call."""

    _signatures: dict[str, list[tuple[str, str]]] = {}

    def __init__(self, *, dsn: str, **kwargs):
        super().__init__(**kwargs)
        self._dsn = dsn

    async def _signature(self, conn, name: str) -> list[tuple[str, str]]:
        cached = LedgerRepository._signatures.get(name)
        if cached is not None:
            return cached
        async with conn.cursor() as cur:
            await cur.execute(
                """
                select p.proargnames,
                       string_to_array(
                           pg_get_function_identity_arguments(p.oid), ', '
                       )
                from pg_proc p
                join pg_namespace n on n.oid = p.pronamespace
                where n.nspname = 'public' and p.proname = %s
                """,
                (name,),
            )
            names, idents = await cur.fetchone()
        signature = list(zip(names, [i.split(" ", 1)[1] for i in idents]))
        LedgerRepository._signatures[name] = signature
        return signature

    async def _rpc(self, name: str, payload):
        try:
            async with await psycopg.AsyncConnection.connect(
                self._dsn, autocommit=True, row_factory=tuple_row
            ) as conn:
                args, params = [], {}
                for argname, argtype in await self._signature(conn, name):
                    if argname not in payload:
                        continue
                    args.append(f"{argname} => %({argname})s::{argtype}")
                    value = payload[argname]
                    params[argname] = (
                        json.dumps(value)
                        if argtype in {"jsonb", "json"} and not isinstance(value, str)
                        else value
                    )
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"select public.{name}({', '.join(args)})", params
                    )
                    row = await cur.fetchone()
                return row[0] if row else None
        except psycopg.Error as exc:
            raise BatchRepositoryError(
                "batch_database_rpc_failed", retryable=False
            ) from exc


class RecordingProvider:
    """Stands in for OpenAI and records what would have been submitted."""

    def __init__(self, sink: list[tuple[str, str]], worker: str):
        self._sink = sink
        self._worker = worker

    @staticmethod
    def build_jsonl(items):
        return b"\n".join(
            json.dumps({"custom_id": item.custom_id}).encode() for item in items
        )

    async def upload_input_file(self, *, payload, bundle_id):
        return f"file-{bundle_id[:8]}"

    async def create_batch(self, *, input_file_id, metadata):
        self._sink.append((metadata["dispatch_key"], self._worker))
        return BatchSnapshot(
            provider_batch_id="batch_" + uuid.uuid4().hex[:16],
            input_file_id=input_file_id,
            status="in_progress",
            output_file_id=None,
            error_file_id=None,
            request_total=1,
            request_completed=0,
            request_failed=0,
            metadata=metadata,
        )

    async def find_recent_batch(self, *, dispatch_key, not_before):
        return None

    async def retrieve_batch(self, provider_batch_id):
        raise AssertionError("the poll path is not exercised by this harness")

    async def download_file(self, file_id):
        raise AssertionError("the download path is not exercised by this harness")

    @staticmethod
    def parse_result_file(payload):
        return ()


def seed_sql(job_count: int) -> str:
    lines = [
        "insert into public.workspaces (id, name, slug, created_by) values "
        f"('{WORKSPACE_ID}','Probe','probe-ws',null) on conflict do nothing;",
        "insert into public.workspace_clients "
        "(workspace_id, client_id, display_name, active, created_by) values "
        f"('{WORKSPACE_ID}','{CLIENT_ID}','Squid',true,null) on conflict do nothing;",
        "delete from agent_runtime.batch_members;",
        "delete from agent_runtime.batch_jobs;",
        "delete from agent_runtime.batch_runs;",
        "delete from agent_runtime.batch_budgets;",
        f"do $probe$ declare ws uuid := '{WORKSPACE_ID}'; "
        "dl timestamptz := statement_timestamp() + interval '30 hours'; "
        "ps timestamptz := date_trunc('day', statement_timestamp()); begin",
        f"perform public.configure_agent_batch_budget("
        f"ws,'{BUDGET_KEY}',ps,ps+interval '1 day',2000000);",
    ]
    for index in range(1, job_count + 1):
        job_id = f"e1000000-0000-4000-8000-{index:012d}"
        source = f"Squid shipped probe update number {index}."
        payload = {
            "instructions": INSTRUCTIONS,
            "input": source,
            "output_schema": OUTPUT_SCHEMA,
            "estimated_output_tokens": 400,
            "risk_tier": "T1",
            "approval_required": True,
            "interactive": False,
            "incident_or_release_blocker": False,
            "live_tools_required": False,
            "source_snapshot_complete": True,
            "input_immutable": True,
            "retry_idempotent": True,
            "remaining_batch_stages": 1,
        }
        digest = canonical_input_sha256(
            instructions=INSTRUCTIONS,
            input_text=source,
            output_schema=OUTPUT_SCHEMA,
        )
        encoded = json.dumps(payload).replace("'", "''")
        lines.append(
            f"perform public.queue_agent_batch_job(ws,'{CLIENT_ID}',"
            f"'{job_id}'::uuid,'{'e' * 49}{index:015d}','naver_seo_writer',"
            f"'naver_seo_article','generate',3::smallint,'batch_24h',"
            f"'gpt-5.6-luna','S',dl,'{encoded}'::jsonb,'{digest}',"
            f"1200::bigint,1400,50000::bigint,'{BUDGET_KEY}',"
            f"'{job_id}:generate:1',false);"
        )
    lines.append("end $probe$;")
    return "\n".join(lines)


async def run_worker(dsn: str, name: str, sink: list, passes: int, claims: int) -> None:
    dispatcher = BatchDispatcher(
        repository=LedgerRepository(
            dsn=dsn,
            supabase_url="http://localhost",
            service_role_key="x" * 40,
            workspace_id=WORKSPACE_ID,
        ),
        provider=RecordingProvider(sink, name),
        policy=BatchPolicy(allowed_clients=frozenset({CLIENT_ID})),
        worker_id=f"batch:{name}-{uuid.uuid4()}",
        max_claims=claims,
        max_requests_per_batch=1,
    )
    for _ in range(passes):
        await dispatcher.submit_once()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="libpq connection string")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--jobs", type=int, default=30)
    parser.add_argument("--passes", type=int, default=10)
    parser.add_argument("--claims", type=int, default=4)
    args = parser.parse_args()

    async with await psycopg.AsyncConnection.connect(
        args.dsn, autocommit=True
    ) as conn:
        await conn.execute(seed_sql(args.jobs))

    sink: list[tuple[str, str]] = []
    await asyncio.gather(*[
        run_worker(args.dsn, f"w{index}", sink, args.passes, args.claims)
        for index in range(args.workers)
    ])

    keys = [key for key, _ in sink]
    duplicates = {k: c for k, c in collections.Counter(keys).items() if c > 1}
    per_worker = collections.Counter(worker for _, worker in sink)

    async with await psycopg.AsyncConnection.connect(
        args.dsn, autocommit=True
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                select
                    (select count(*) from agent_runtime.batch_jobs),
                    (select count(*) from agent_runtime.batch_jobs
                       where status = 'submitted'),
                    (select count(*) from agent_runtime.batch_runs),
                    (select count(*) from (
                        select job_id from agent_runtime.batch_members
                        group by job_id having count(*) > 1) as multi),
                    (select count(*) from agent_runtime.batch_jobs
                       where attempts > 1)
            """)
            jobs, submitted, runs, multi_batch, retried = await cur.fetchone()

    print(f"workers            : {args.workers}")
    print(f"submissions        : {len(sink)}")
    print(f"distinct keys      : {len(set(keys))}")
    print(f"duplicates         : {len(duplicates)}")
    print(f"per worker         : {dict(sorted(per_worker.items()))}")
    print(f"jobs / submitted   : {jobs} / {submitted}")
    print(f"batch runs         : {runs}")
    print(f"jobs in >1 batch   : {multi_batch}")
    print(f"jobs with attempt>1: {retried}")

    ok = (
        not duplicates
        and submitted == args.jobs
        and len(sink) == args.jobs
        and multi_batch == 0
    )
    print("RESULT             :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
