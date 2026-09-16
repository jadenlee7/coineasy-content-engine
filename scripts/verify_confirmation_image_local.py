"""Opt-in local image smoke test. No host ports, volumes or real credentials.

Uses only a prebuilt local image (never pulls), with network disabled and a
read-only filesystem. Removes only the disposable container it creates.
Synthetic SHA is explicitly not GitHub/production provenance.
"""
import argparse
from contextlib import contextmanager
import json
import re
import subprocess
import uuid

SHA = 'a' * 40


def docker(*args, input=None, expected=0, require_empty_stderr=False):
    result = subprocess.run(['docker', *args], input=input, text=True,
                            capture_output=True, timeout=40)
    if result.returncode != expected:
        raise RuntimeError('local_image_command_failed')
    if require_empty_stderr and result.stderr:
        raise RuntimeError('local_image_unexpected_stderr')
    return result.stdout


@contextmanager
def disposable(*args):
    """Name known before create, so uncertain create/start can still be cleaned."""
    name = 'coineasy-confirmation-smoke-' + uuid.uuid4().hex
    try:
        container = docker('create','--name',name,*args).strip()
        if not re.fullmatch('[a-f0-9]{64}', container):
            raise RuntimeError('local_image_container_invalid')
        yield container
    finally:
        # A failed create may have created nothing. Only remove our exact name.
        found = docker('ps','-aq','--no-trunc','--filter','name=^/'+name+'$').strip()
        if found:
            if not re.fullmatch('[a-f0-9]{64}', found):
                raise RuntimeError('local_image_cleanup_ambiguous')
            docker('rm','-f',found)
        if docker('ps','-aq','--no-trunc','--filter','name=^/'+name+'$').strip():
            raise RuntimeError('local_image_cleanup_failed')


PROBE = r'''
import json, os, pathlib, time, urllib.request, urllib.error
assert os.getuid() == os.getgid() == 10001
assert pathlib.Path('/app/content-ops-confirmation-build-sha').read_text() == 'a'*40
assert not pathlib.Path('/app/core/publications').exists()
assert not pathlib.Path('/app/api').exists()
assert not pathlib.Path('/data/content-ops-confirmation/replay.sqlite').exists()
assert not any(x.name.startswith('.env') for x in pathlib.Path('/app').iterdir())
results=[]
for method,path in [('GET','/'),('POST','/internal/content-ops/confirmation-dispatch')]:
    for attempt in range(30):
        try:
            request=urllib.request.Request('http://127.0.0.1:8080'+path,method=method)
            urllib.request.urlopen(request,timeout=1)
            raise AssertionError('off_must_not_succeed')
        except urllib.error.HTTPError as response:
            assert response.code == 503 and response.read() == b'Disabled'
            assert response.headers['Cache-Control'] == 'no-store'
            results.append({'method':method,'status':503,'body':'Disabled'})
            break
        except urllib.error.URLError:
            if attempt == 29: raise
            time.sleep(.1)
print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'http':results,'ledger_exists':False}))
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-only', action='store_true', required=True)
    parser.add_argument('--image', required=True)
    args = parser.parse_args(argv)
    image = json.loads(docker('image', 'inspect', args.image))[0]
    image_id = image['Id']
    assert re.fullmatch('sha256:[a-f0-9]{64}', image_id)
    assert image['Config']['User'] == '10001:10001'
    defaults = dict(v.split('=', 1) for v in image['Config']['Env'])
    assert defaults['CONTENT_OPS_CONFIRMATION_ENABLED'] == 'false'
    assert defaults['CONTENT_OPS_REVIEW_ENABLED'] == 'false'
    assert image['Config']['Cmd'] == ['python','-m','scripts.run_content_ops_confirmation']
    safety = ['--pull=never','--network=none','--read-only','--cap-drop=ALL',
              '--security-opt=no-new-privileges','--memory=128m','--cpus=1']
    checks = []
    for name, config, expected in [
        ('matching_sha', {'RAILWAY_GIT_COMMIT_SHA':SHA,'CONTENT_OPS_CONFIRMATION_RELEASE_SHA':SHA},0),
        ('wrong_sha', {'RAILWAY_GIT_COMMIT_SHA':'b'*40,'CONTENT_OPS_CONFIRMATION_RELEASE_SHA':SHA},1),
        ('missing_sha', {},1),
    ]:
        flags = [part for key,value in config.items() for part in ('-e',key+'='+value)]
        with disposable(*safety,*flags,image_id,'python','-m',
                        'scripts.run_content_ops_confirmation','--validate-only') as container:
            output = docker('start','-a',container,expected=expected)
            exit_code = int(docker('inspect','--format','{{.State.ExitCode}}',container).strip())
            if exit_code != expected:
                raise RuntimeError('local_image_validation_exit_invalid')
            result = json.loads(output)
        assert result['ok'] is (expected == 0)
        assert all(result[key] is False for key in
            ('network_calls','database_calls','telegram_calls','ledger_access','execution_authorized'))
        checks.append({'case':name,'exit_code':expected,'ok':result['ok']})
    with disposable(*safety,image_id) as container:
        docker('start',container)
        state = json.loads(docker('inspect',container))[0]
        assert state['HostConfig']['NetworkMode'] == 'none'
        assert state['HostConfig']['ReadonlyRootfs'] is True
        assert not state['HostConfig']['PortBindings'] and not state['Mounts']
        http = json.loads(docker('exec','-i',container,'python','-',input=PROBE))
        assert docker('logs',container,require_empty_stderr=True) == ''
    print(json.dumps({'status':'LOCAL_IMAGE_VERIFIED_NOT_DEPLOYED','image_id':image_id,
        'platform':image['Os']+'/'+image['Architecture'], 'synthetic_build_sha':SHA,
        'validation':checks,'runtime':http,'network':'none','host_ports':0,
        'volumes':0,'container_removed':True,'production_changes':0,'external_sends':0},indent=2))


if __name__ == '__main__':
    main()
