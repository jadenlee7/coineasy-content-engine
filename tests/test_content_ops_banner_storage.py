"""Storage transport is fully mocked; no credentials/network/provider spends."""
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from core.content_ops.banner_regeneration import BannerError
from core.content_ops.banner_storage import SupabaseBannerStorage
from test_content_ops_banner_regeneration import RESULT, request

URL='https://'+'a'*20+'.supabase.co'
PATH=str(uuid4())+'/yellow/'+str(uuid4())+'/news-card.png'


def client(handler):
    return SupabaseBannerStorage(supabase_url=URL,project_key='synthetic-project-key-'+'a'*32,
        token='synthetic-scoped-token-'+'b'*32,transport=httpx.MockTransport(handler))


def test_private_snapshot_does_not_need_public_eligibility():
    req=request()
    private=replace(req,snapshot=replace(req.snapshot,eligibility='blocked'))
    private.validate()
    with pytest.raises(BannerError):
        replace(req,snapshot=replace(req.snapshot,eligibility='recap_requires_separate_approval')).validate()


def test_private_bucket_create_only_full_byte_readback():
    calls=[]
    def handle(req):
        calls.append(req)
        assert req.headers['apikey']!=req.headers['authorization'].removeprefix('Bearer ')
        if '/bucket/' in req.url.path:
            return httpx.Response(200,json={'id':'content-studio','public':False})
        if req.method=='POST':
            assert req.headers['x-upsert']=='false' and req.content==RESULT
            return httpx.Response(200,json={'Key':'not trusted for readback'})
        return httpx.Response(200,content=RESULT)
    receipt=client(handle).put_and_verify(path=PATH,png=RESULT)
    assert [(r.method,'bucket' if '/bucket/' in r.url.path else 'object') for r in calls]==[
        ('GET','bucket'),('POST','object'),('GET','object')]
    assert receipt['storage_path']==PATH and receipt['byte_size']==len(RESULT)


@pytest.mark.parametrize('status',[301,400,401,403,409,429,500])
def test_no_retry_or_cleanup_after_upload_failure(status):
    calls=[]
    def handle(req):
        calls.append(req.method)
        if '/bucket/' in req.url.path: return httpx.Response(200,json={'id':'content-studio','public':False})
        return httpx.Response(status,headers={'location':'https://other.invalid/'})
    with pytest.raises(BannerError,match='^banner_storage_unknown$'):
        client(handle).put_and_verify(path=PATH,png=RESULT)
    assert calls==['GET','POST']


@pytest.mark.parametrize('bucket',[{'id':'content-studio','public':True},{'id':'other','public':False},{},None])
def test_unknown_or_public_bucket_never_uploads(bucket):
    calls=[]
    def handle(req):
        calls.append(req.method); return httpx.Response(200,json=bucket)
    with pytest.raises(BannerError): client(handle).put_and_verify(path=PATH,png=RESULT)
    assert calls==['GET']


def test_mismatched_bytes_or_timeout_hold_without_cleanup():
    for failure in ('bytes','timeout'):
        calls=[]
        def handle(req):
            calls.append(req.method)
            if '/bucket/' in req.url.path: return httpx.Response(200,json={'id':'content-studio','public':False})
            if req.method=='POST': return httpx.Response(200)
            if failure=='timeout': raise httpx.ReadTimeout('synthetic private error')
            return httpx.Response(200,content=b'not-the-saved-image')
        with pytest.raises(BannerError,match='^banner_storage_unknown$'):
            client(handle).put_and_verify(path=PATH,png=RESULT)
        assert calls==['GET','POST','GET']


@pytest.mark.parametrize('path',['../../evil','https://external.invalid/banner.png',PATH.replace('/yellow/','/other/'),PATH+'?x=1'])
def test_wrong_path_never_connects(path):
    def forbidden(req): raise AssertionError('must not connect')
    with pytest.raises(BannerError,match='banner_storage_path_invalid'):
        client(forbidden).put_and_verify(path=path,png=RESULT)
