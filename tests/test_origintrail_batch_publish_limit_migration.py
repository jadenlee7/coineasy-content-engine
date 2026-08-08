from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "supabase/migrations/20260808121500_origintrail_batch_telegram_publish_limit.sql").read_text()


def test_new_origintrail_batch_results_fit_exact_telegram_caption():
    assert "origintrail_batch_telegram_copy_publish_limit" in SQL
    assert "between 1 and 1024" in SQL
    assert "not valid" in SQL.lower()
    assert "workflow_kind = 'official_source_nonurgent_pack'" in SQL
