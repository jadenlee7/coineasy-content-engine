"""Create-only private Storage upload plus full-byte readback; no retry/delete.

Injected scoped token only. This module never discovers or falls back to the
legacy bot's admin key. An uncertain upload or orphan is retained for read-only
reconciliation; it is not permission to generate/upload again.
"""
import re
import json
import httpx

from core.content_ops.banner_regeneration import BannerError, MAX_IMAGE, inspect_png, require, sha


class SupabaseBannerStorage:
    def __init__(self, *, supabase_url, project_key, token, transport=None):
        require(type(supabase_url) is str and re.fullmatch(r'https://[a-z0-9]{20}\.supabase\.co',supabase_url),
                'banner_storage_config_invalid')
        require(type(token) is str and 32<=len(token)<=8192 and token.isascii()
                and not any(c.isspace() for c in token),'banner_storage_config_invalid')
        require(type(project_key) is str and 32<=len(project_key)<=8192 and project_key.isascii()
                and not any(c.isspace() for c in project_key) and project_key!=token,
                'banner_storage_config_invalid')
        self._url,self._token,self._transport=supabase_url,token,transport
        self._project_key=project_key

    def put_and_verify(self, *, path, png):
        require(type(path) is str and re.fullmatch(
            r'[a-f0-9-]{36}/(?:yellow|babylon|squid|origintrail)/[a-f0-9-]{36}/news-card\.png',path),
            'banner_storage_path_invalid')
        require(inspect_png(png,opaque=True)==(1536,1024),'banner_image_invalid')
        url=self._url+'/storage/v1/object/content-studio/'+path
        try:
            with httpx.Client(transport=self._transport,trust_env=False,follow_redirects=False,
                              timeout=20) as client:
                auth={'Authorization':'Bearer '+self._token,'apikey':self._project_key}
                with client.stream('GET',self._url+'/storage/v1/bucket/content-studio',headers=auth) as response:
                    require(response.status_code==200,'banner_storage_unknown')
                    body=bytearray()
                    for block in response.iter_bytes():
                        body.extend(block)
                        require(len(body)<=16384,'banner_storage_unknown')
                    bucket=json.loads(body)
                    require(type(bucket) is dict and bucket.get('id')=='content-studio'
                            and bucket.get('public') is False,'banner_storage_unknown')
                headers={**auth,'Content-Type':'image/png','x-upsert':'false'}
                # Stream so arbitrary provider response bodies are never loaded/logged.
                with client.stream('POST',url,headers=headers,content=png) as response:
                    require(response.status_code in {200,201},'banner_storage_unknown')
                with client.stream('GET',url,headers=auth) as response:
                    require(response.status_code==200,'banner_storage_unknown')
                    result=bytearray()
                    for block in response.iter_bytes():
                        result.extend(block)
                        require(len(result)<=MAX_IMAGE,'banner_storage_unknown')
                require(bytes(result)==png,'banner_storage_unknown')
            return {'storage_bucket':'content-studio','storage_path':path,
                    'sha256':sha(png),'byte_size':len(png)}
        except Exception:
            raise BannerError('banner_storage_unknown') from None
