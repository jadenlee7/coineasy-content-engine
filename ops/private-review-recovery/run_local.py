"""Fresh disposable PostgreSQL recovery proof. Explicit local/CI routes only."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def main():
    assert sys.argv[1:] == ['--local-only']
    host = os.environ.get('PGHOST')
    assert host == '/private/tmp' or (host == '127.0.0.1' and os.environ.get('GITHUB_ACTIONS') == 'true')
    assert os.environ.get('PGPORT') == '5432'
    assert not os.environ.get('PGHOSTADDR') and not os.environ.get('PGSERVICE')
    name = 'coineasy_review_recovery_test_' + uuid4().hex[:12]
    with psycopg.connect(host=host, port=5432, dbname='postgres', user=os.environ['PGUSER'], autocommit=True) as admin:
        for role in ('anon', 'authenticated', 'service_role'):
            if not admin.execute('select 1 from pg_roles where rolname=%s', (role,)).fetchone():
                admin.execute(sql.SQL('create role {} nologin').format(sql.Identifier(role)))
        admin.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
        try:
            os.environ['PGDATABASE'] = name
            with psycopg.connect(host=host, port=5432, dbname=name, user=os.environ['PGUSER'], autocommit=True) as db:
                db.execute((HERE / 'recovery_fixture.sql').read_text())
                original = (ROOT / 'supabase/proposals/content_ops_button_card_send_ledger.sql').read_text()
                start = original.index('create function private.content_ops_button_card_outbox_owned(')
                end = original.index('end $$;', start) + len('end $$;')
                db.execute(original[start:end].replace('create function', 'create or replace function', 1))
                db.execute('revoke all on function private.content_ops_button_card_outbox_owned(uuid) from public,anon,authenticated,service_role')
                db.execute((ROOT / 'supabase/proposals/content_ops_exact_private_review_recovery.sql').read_text())
            spec = importlib.util.spec_from_file_location('recovery_local_unittest', HERE / 'recovery_local_unittest.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
            assert result.wasSuccessful()
        finally:
            admin.execute(sql.SQL('drop database {}').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
