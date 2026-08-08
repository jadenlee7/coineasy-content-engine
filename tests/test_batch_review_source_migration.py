from pathlib import Path


MIGRATION = (
    Path(__file__).parents[1]
    / "supabase"
    / "migrations"
    / "20260804164500_expose_agent_batch_review_source.sql"
).read_text()


def test_batch_review_source_is_bounded_and_comes_from_immutable_input():
    assert "create or replace function public.get_agent_batch_review_item" in MIGRATION
    assert "'source_content'" in MIGRATION
    assert "(batch_job.input_payload ->> 'input')::jsonb" in MIGRATION
    assert "-> 'source' ->> 'content'" in MIGRATION
    assert ") between 1 and 60000" in MIGRATION
    assert "-> 'source' ->> 'url'" in MIGRATION
    assert "= review_job.input ->> 'source_url'" in MIGRATION
    assert "batch_job.input_payload -> 'input_immutable' = 'true'::jsonb" in MIGRATION
    assert "batch_job.input_payload -> 'source_snapshot_complete' = 'true'::jsonb" in MIGRATION
    assert "'input_payload'" not in MIGRATION


def test_batch_review_source_rpc_remains_service_role_only():
    assert "security definer" in MIGRATION
    assert "set search_path = ''" in MIGRATION
    assert "from public, anon, authenticated, service_role" in MIGRATION
    assert "to service_role" in MIGRATION
