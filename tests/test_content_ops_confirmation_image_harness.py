"""Cleanup/error-stream tests use a fake Docker CLI; never connect to Docker."""
import subprocess
from types import SimpleNamespace

import pytest

from scripts import verify_confirmation_image_local as harness


@pytest.mark.parametrize('point', ['create', 'body', 'success'])
def test_cleanup_even_when_create_ack_or_body_is_lost(monkeypatch, point):
    calls = []
    existing = False
    ident = 'c' * 64
    def fake(*args, **kwargs):
        nonlocal existing
        calls.append(args)
        if args[0] == 'create':
            existing = True
            if point == 'create':
                raise subprocess.TimeoutExpired('docker',40)
            return ident
        if args[0] == 'ps':
            assert '--no-trunc' in args and args[-1].startswith('name=^/coineasy-confirmation-smoke-')
            return ident if existing else ''
        if args[0] == 'rm':
            assert args == ('rm','-f',ident)
            existing = False
            return ident
        raise AssertionError('unexpected Docker operation')
    monkeypatch.setattr(harness,'docker',fake)
    def run():
        with harness.disposable('--network=none','synthetic-image') as container:
            assert container == ident
            if point == 'body':
                raise subprocess.TimeoutExpired('docker',40)
    if point == 'success':
        run()
    else:
        with pytest.raises(subprocess.TimeoutExpired):run()
    assert not existing
    assert len([x for x in calls if x[0]=='rm']) == 1


def test_failed_create_without_container_does_not_remove_anything(monkeypatch):
    def fake(*args,**kwargs):
        if args[0]=='create':raise RuntimeError('create_failed')
        assert args[0]=='ps'
        return ''
    monkeypatch.setattr(harness,'docker',fake)
    with pytest.raises(RuntimeError,match='create_failed'):
        with harness.disposable('synthetic-image'):pytest.fail('must not run')


def test_stderr_logs_cannot_pass_empty_log_check(monkeypatch):
    monkeypatch.setattr(harness.subprocess,'run',lambda *a,**k:
        SimpleNamespace(returncode=0,stdout='',stderr='synthetic-private-log'))
    with pytest.raises(RuntimeError,match='local_image_unexpected_stderr') as error:
        harness.docker('logs','synthetic-container',require_empty_stderr=True)
    assert 'synthetic-private-log' not in str(error.value)


def test_local_opt_in_required_before_docker(monkeypatch):
    monkeypatch.setattr(harness,'docker',lambda *a,**k:pytest.fail('no Docker access'))
    with pytest.raises(SystemExit) as error:harness.main(['--image','synthetic-image'])
    assert error.value.code == 2
