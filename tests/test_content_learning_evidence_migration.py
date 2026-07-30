from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260730123000_content_learning_evidence.sql"
)


def test_learning_evidence_is_private_immutable_and_service_only():
    sql = MIGRATION.read_text().lower()
    assert "create table private.content_learning_evidence" in sql
    assert "record_content_learning_evidence" in sql
    assert "schema_version = '1.2'" in sql
    assert "tutorial-priority-v1" in sql
    assert "jsonb_array_length(target_learning -> 'gaps') > 8" in sql
    assert "(gap ->> 'attempts')::integer < 20" in sql
    assert "(gap ->> 'participants')::integer < 5" in sql
    assert "on conflict" in sql
    assert "retry does not match" in sql
    assert (
        "revoke all on table private.content_learning_evidence\n"
        "    from public, anon, authenticated, service_role"
    ) in sql
    assert (
        "grant execute on function public.record_content_learning_evidence"
    ) in sql


def test_learning_evidence_has_no_publish_or_user_level_surface():
    sql = MIGRATION.read_text().lower()
    for forbidden in (
        "telegram_id",
        "wallet_address",
        "question_text",
        "answer_choice",
        "insert into public.publications",
        "update public.content_items",
        "queue_review_draft_job",
    ):
        assert forbidden not in sql
