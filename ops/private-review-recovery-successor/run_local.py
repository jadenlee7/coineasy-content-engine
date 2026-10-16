"""Disposable PostgreSQL 17 proof; never connect to a user's existing local DB."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from uuid import uuid4

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def exercise(settings):
    name = 'coineasy_review_recovery_test_' + uuid4().hex[:12]
    with psycopg.connect(**settings, dbname='postgres', autocommit=True) as admin:
        version = int(admin.execute('show server_version_num').fetchone()[0])
        assert 170000 <= version < 180000, 'PostgreSQL 17 required'
        for role in ('anon', 'authenticated', 'service_role'):
            if not admin.execute('select 1 from pg_roles where rolname=%s', (role,)).fetchone():
                admin.execute(sql.SQL('create role {} nologin').format(sql.Identifier(role)))
        admin.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
        try:
            connect = {**settings, 'dbname': name}
            with psycopg.connect(**connect, autocommit=True) as db:
                db.execute((ROOT / 'ops/private-review-recovery/recovery_fixture.sql').read_text())
                original = (ROOT / 'supabase/proposals/content_ops_button_card_send_ledger.sql').read_text()
                start = original.index('create function private.content_ops_button_card_outbox_owned(')
                end = original.index('end $$;', start) + len('end $$;')
                db.execute(original[start:end].replace('create function', 'create or replace function', 1))
                db.execute('revoke all on function private.content_ops_button_card_outbox_owned(uuid) from public,anon,authenticated,service_role')
                db.execute((ROOT / 'supabase/proposals/content_ops_exact_private_review_recovery.sql').read_text())
                # Structural anchor fixture; production anchor DDL is separately tested.
                db.execute('create table private.ybo_standalone_babylon_trial_anchor '
                    '(like private.ybo_readiness_babylon_trial_anchor including all)')
            spec = importlib.util.spec_from_file_location('successor_local_unittest', HERE / 'successor_local_unittest.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.CONNECT = connect
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(module))
            assert result.wasSuccessful(), 'successor disposable tests failed'
            print(f'PostgreSQL {version}; tests={result.testsRun}; production_calls=0', flush=True)
        finally:
            admin.execute(sql.SQL('drop database {}').format(sql.Identifier(name)))


def main():
    assert sys.argv[1:] in (['--local-only'], ['--ci-only']), 'explicit disposable mode required'
    if sys.argv[1:] == ['--ci-only']:
        assert os.environ.get('GITHUB_ACTIONS') == 'true'
        assert os.environ.get('PGHOST') == '127.0.0.1' and os.environ.get('PGPORT') == '5432'
        assert not os.environ.get('PGHOSTADDR') and not os.environ.get('PGSERVICE')
        exercise(dict(host='127.0.0.1', port=5432, user=os.environ['PGUSER'], connect_timeout=3))
        return
    binary = Path('/opt/homebrew/opt/postgresql@17/bin')
    with tempfile.TemporaryDirectory(prefix='coineasy-recovery-successor-', dir='/private/tmp') as temp:
        directory = Path(temp)
        env = {'PATH': str(binary) + ':' + os.environ['PATH'], 'LANG': 'C', 'TZ': 'UTC',
            'PGHOST': temp, 'PGPORT': '65439', 'PGUSER': 'postgres', 'PGPASSFILE': '/dev/null'}
        def run(command, *args):
            try:
                subprocess.run([str(binary / command), *args], env=env, check=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(error.stderr.decode('utf8', errors='replace')[-3000:]) from None
        started = False
        try:
            run('initdb', '-D', temp, '-U', 'postgres', '--auth=trust', '--no-locale', '-E', 'UTF8')
            run('pg_ctl', '-D', temp, '-l', str(directory / 'server.log'),
                '-o', f"-h '' -k {temp} -p 65439", '-w', 'start')
            started = True
            exercise(dict(host=temp, port=65439, user='postgres', connect_timeout=3, passfile='/dev/null'))
        finally:
            if started:
                run('pg_ctl', '-D', temp, '-m', 'fast', '-w', 'stop')
            assert not (directory / '.s.PGSQL.65439').exists(), 'disposable socket remained'


if __name__ == '__main__':
    main()
