"""Report credential readiness without printing secrets or task contents."""
import argparse
import json

import httpx
from engine.providers.cloud_video import credential, check_response, endpoint

CHECKS={
    'minimax':('MINIMAX_API_KEY','https://api.minimax.io/v2/query/video_generation'),
    'seedance':('ARK_API_KEY','https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks'),
}


def check(remote=False):
    result={}
    for provider,(name,url) in CHECKS.items():
        state={'configured':False,'readable':False,'remote_checked':False}
        try:
            key=credential(name)
            state.update(configured=bool(key),readable=bool(key))
            if provider=='minimax':url=endpoint('minimax-h3-api')[0]+'/v2/query/video_generation'
            state['api_origin']=str(httpx.URL(url).copy_with(path='',query=None))
            if key and remote:
                if any(c.isspace() for c in key):raise ValueError('Credential must be a single token')
                with httpx.Client(timeout=30,follow_redirects=False) as client:
                    response=client.get(url,headers={'Authorization':'Bearer '+key},params={'page_num':1,'page_size':1})
                state.update(remote_checked=True,http_status=response.status_code)
                check_response(response)
                state['authenticated']=True
        except (OSError,ValueError,RuntimeError,httpx.HTTPError) as error:
            # Do not serialize exceptions that could contain request headers/data.
            state['authenticated']=False
            state['error']='credential_file_or_format' if isinstance(error,(OSError,ValueError)) else 'network_or_provider_rejected'
        result[provider]=state
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote',action='store_true',help='Official task-list GET only; never creates a video')
    args=parser.parse_args()
    result=check(args.remote)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if all(s.get('authenticated') if args.remote else s['readable'] for s in result.values()) else 1


if __name__=='__main__':raise SystemExit(main())
