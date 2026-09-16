"""Exclusive relay HTTP entrypoint, disabled by default. No scheduler/poller."""
import argparse
import json
import os

from core.content_ops.confirmation_runtime import create_app,validate_only


def main(argv=None):
    parser=argparse.ArgumentParser(description='Private confirmation relay; default OFF.')
    parser.add_argument('--validate-only',action='store_true')
    args=parser.parse_args(argv)
    if args.validate_only:
        result=validate_only()
        print(json.dumps(result,separators=(',',':')))
        return 0 if result['ok'] else 1
    port=os.environ.get('PORT','8080')
    if not port.isascii() or not port.isdecimal() or not 1<=int(port)<=65535:
        print('{"ok":false,"error":"confirmation_configuration_invalid"}')
        return 1
    import uvicorn
    uvicorn.run(create_app(),host='0.0.0.0',port=int(port),workers=1,
        access_log=False,log_level='warning',proxy_headers=False)
    return 0


if __name__=='__main__':raise SystemExit(main())
