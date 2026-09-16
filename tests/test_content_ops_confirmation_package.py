"""Offline packaging smoke tests; not a Docker image or hosted receipt."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_content_ops_confirmation_runtime import env, SHA, P

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def package(tmp_path):
    """Only the sources permitted by the Dockerfile COPY contract."""
    (tmp_path / 'core').mkdir()
    (tmp_path / 'scripts').mkdir()
    shutil.copyfile(ROOT / 'core/__init__.py', tmp_path / 'core/__init__.py')
    shutil.copytree(ROOT / 'core/content_ops', tmp_path / 'core/content_ops',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copyfile(ROOT / 'scripts/run_content_ops_confirmation.py',
                    tmp_path / 'scripts/run_content_ops_confirmation.py')
    return tmp_path


CHILD = r'''
import asyncio, io, json, pathlib, runpy, sys
from contextlib import redirect_stdout
root = pathlib.Path.cwd()
sys.path.insert(0, str(root))
config = json.loads(sys.stdin.read())
def audit(event, args):
    if event in ('socket.connect', 'socket.bind', 'sqlite3.connect'):
        raise AssertionError('external_or_storage_io_forbidden')
sys.addaudithook(audit)
from core.content_ops import confirmation_runtime as runtime
runtime.STAMP = root / 'stamp'
result = runtime.validate_only(environ=config)
app = runtime.create_app(environ=config)
async def probe():
    messages = []
    async def receive(): raise AssertionError('body_must_not_be_read')
    async def send(message): messages.append(message)
    await app({'type':'http','method':'POST',
        'path':'/internal/content-ops/confirmation-dispatch',
        'headers':[], 'query_string':b''}, receive, send)
    return messages[0]['status']
status = asyncio.run(probe())
# No source modules accidentally resolved from the original workspace.
for name, module in list(sys.modules.items()):
    if name == 'core' or name.startswith('core.'):
        assert pathlib.Path(module.__file__).resolve().is_relative_to(root)
assert 'api.server' not in sys.modules
assert 'core.publications' not in sys.modules
# Exercise the copied command entrypoint with only the synthetic environment.
import os
os.environ.clear()
os.environ.update(config)
sys.argv = ['run_content_ops_confirmation', '--validate-only']
output = io.StringIO()
with redirect_stdout(output):
    try: runpy.run_module('scripts.run_content_ops_confirmation', run_name='__main__')
    except SystemExit as exc: code = exc.code
assert json.loads(output.getvalue()) == result
assert code == (0 if result['ok'] else 1)
print(json.dumps({'validation':result, 'http_status':status, 'cli_exit':code}))
'''


@pytest.mark.parametrize('mode', ['off', 'enabled_unauthenticated', 'wrong_sha', 'missing_stamp'])
def test_copied_package_cli_and_factory_without_owner_or_provider_io(package, mode):
    config = env()
    if mode == 'off':
        config = {P+'RELEASE_SHA':SHA, 'RAILWAY_GIT_COMMIT_SHA':SHA}
    if mode != 'missing_stamp':
        (package / 'stamp').write_text('b'*40 if mode == 'wrong_sha' else SHA)
    result = subprocess.run([sys.executable, '-I', '-B', '-c', CHILD], cwd=package,
        env={'PATH':'/usr/bin:/bin'}, input=json.dumps(config),
        capture_output=True, text=True, timeout=15, check=True)
    receipt = json.loads(result.stdout)
    valid = mode in ('off', 'enabled_unauthenticated')
    assert receipt['validation']['ok'] is valid
    assert receipt['http_status'] == (401 if mode == 'enabled_unauthenticated' else 503)
    assert receipt['validation']['telegram_calls'] is False
    assert receipt['validation']['ledger_access'] is False
    assert not (package / 'data').exists()


@pytest.mark.parametrize('sha', [None, '', 'a'*39, 'a'*41, 'A'*40, 'g'*40,
                               'a'*39+'\n', 'a'*40+'\n', SHA])
def test_actual_dockerfile_sha_gate_shell_fragment(tmp_path, sha):
    docker = (ROOT / 'Dockerfile.content-ops-confirmation').read_text()
    # Execute the actual RUN body; change only its output location for the test.
    gate = docker.split('RUN test -n ', 1)[1].split('\nRUN groupadd', 1)[0]
    target = tmp_path / 'stamp'
    script = ('test -n '+gate).replace('/app/content-ops-confirmation-build-sha', '"$STAMP"')
    config = {'PATH':'/usr/bin:/bin', 'STAMP':str(target)}
    if sha is not None:
        config['RAILWAY_GIT_COMMIT_SHA'] = sha
    result = subprocess.run(['/bin/sh', '-c', script], env=config,
        capture_output=True, timeout=5)
    assert (result.returncode == 0) is (sha == SHA)
    if sha == SHA:
        assert target.read_bytes() == SHA.encode('ascii')
    else:
        assert not target.exists()
