"""Durable official asynchronous video APIs; never resubmit an ambiguous POST."""
import json
import os
from pathlib import Path
import time
from urllib.parse import quote, urlsplit

import httpx
from engine.workflows import video
from engine.workflows.cloud_video import request

API_PROVIDERS = {'minimax-h3-api', 'seedance-api'}
ENDPOINTS = {
    'minimax-h3-api': ('https://api.minimax.io', '/v2/video_generation', '/v2/query/video_generation/', 'MINIMAX_API_KEY'),
    'seedance-api': ('https://ark.cn-beijing.volces.com', '/api/v3/contents/generations/tasks', '/api/v3/contents/generations/tasks/', 'ARK_API_KEY'),
}


def credential(name):
    value=os.environ.get(name)
    if value:return value.strip()
    filename=os.environ.get(name+'_FILE')
    if filename:
        path=Path(filename).expanduser()
        if path.stat().st_mode & 0o077:raise ValueError(name+'_FILE must have permissions 600 or stricter')
        return path.read_text().strip()
    return None


def required_files(params):
    return []


def save(path, data):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush();os.fsync(stream.fileno())
    temporary.replace(path)


def preview(params, pid):
    """Review the actual native request without printing inline media bytes."""
    payload = request(params, pid)
    for item in payload['content']:
        if item['type'] != 'text':
            key=item['type'];url=item[key]['url']
            if url.startswith('data:'):item[key]['url']=url.split(',',1)[0]+',<redacted>'
    return payload


def check_response(response):
    # Avoid logging headers, API secrets, signed URLs or echoed Base64 bodies.
    if response.status_code >= 400:
        hints={401:'check API key',403:'check model access',402:'insufficient credit',429:'rate/concurrency limit'}
        raise RuntimeError(f'Video API HTTP {response.status_code}: {hints.get(response.status_code,"check provider console")}; remote state retained')
    try:data=response.json()
    except ValueError:raise RuntimeError('Video API returned non-JSON response') from None
    base=data.get('base_resp') or {}
    if base.get('status_code') not in (None,0):raise RuntimeError('MiniMax API error code '+str(base['status_code']))
    return data


def query(client, url, headers, deadline):
    failures=0
    while True:
        try:
            response=client.get(url,headers=headers)
            if response.status_code not in {429,500,502,503,504}:return check_response(response)
        except httpx.TransportError:pass
        failures+=1
        if failures>=5 or time.monotonic()>=deadline:raise RuntimeError('Video API query unavailable; resume the saved remote task; do not create another paid request')
        time.sleep(min(2**failures,30))


def download(client, url, dest):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise RuntimeError('API output must be an HTTPS download URL')
    # A fresh request carries no API Authorization to the output CDN.
    partial=dest.with_suffix(dest.suffix+'.part')
    try:
        with client.stream('GET',url,follow_redirects=True) as response:
            if response.status_code!=200:raise RuntimeError(f'Video download HTTP {response.status_code}; remote task retained')
            total=0
            with partial.open('wb') as stream:
                for chunk in response.iter_bytes(1024**2):
                    total+=len(chunk)
                    if total>8*1024**3:raise RuntimeError('Video download exceeds 8 GiB')
                    stream.write(chunk)
        partial.replace(dest)
    except httpx.TransportError:raise RuntimeError('Video download interrupted; resume saved task without regeneration') from None


def run(task, gpu, out):
    from engine import core
    from engine.export import inspect
    p=task['params'];pid=task['project']
    if gpu!=-2:raise ValueError('API generation requires network worker lane -2')
    video.validate(p,pid)
    if not core.config(pid).get('routing',{}).get('paid_api_enabled',False):raise ValueError('Project paid_api_enabled is false; API execution disabled')
    approval=p.get('input_review',{})
    signature=video.fingerprint(p,pid)
    if not approval.get('approved') or approval.get('fingerprint')!=signature:raise ValueError('API task inputs/configuration require review')
    base,create_path,query_path,key_env=ENDPOINTS[p['provider']]
    key=credential(key_env)
    if not key:raise ValueError('Set '+key_env+' in the API worker environment; never store credentials in project YAML')
    headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'}
    state_file=out/'remote-task.json'
    started=time.monotonic();deadline=started+p.get('timeout',86400)
    with httpx.Client(timeout=httpx.Timeout(120,connect=30),follow_redirects=False) as client:
        if state_file.exists():
            state=json.loads(state_file.read_text())
            if state['fingerprint']!=signature or state['provider']!=p['provider']:raise ValueError('Remote request fingerprint/provider mismatch')
            if not state.get('request_id'):raise RuntimeError('Previous API submission has an unknown outcome; check provider console before retrying. Automatic duplicate billing prevented.')
        else:
            payload=request(p,pid)
            save(out/'api-request-preview.json',preview(p,pid))
            state={'provider':p['provider'],'model':p['model'],'fingerprint':signature,
                   'submitted_at':time.time(),'status':'submitting','request_id':None}
            # Persist intent before sending: an interrupted POST is never retried implicitly.
            save(state_file,state)
            try:data=check_response(client.post(base+create_path,headers=headers,json=payload))
            except httpx.TransportError:raise RuntimeError('API submission response lost; check provider console. Automatic POST retry disabled to prevent duplicate billing.') from None
            remote_id=data.get('task_id') if p['provider']=='minimax-h3-api' else data.get('id')
            if not remote_id or not isinstance(remote_id,str):raise RuntimeError('API did not return a task ID; inspect provider console before retry')
            state.update(request_id=remote_id,status='queued');save(state_file,state)
            with core.db() as conn:core.event(conn,pid,task['id'],'remote_submitted',p['provider']+' '+remote_id)
        while True:
            if time.monotonic()>=deadline:raise TimeoutError('API polling deadline exceeded; use studio resume-api to query the same paid task')
            response=query(client,base+query_path+quote(state['request_id'],safe=''),headers,deadline)
            result=response.get('task',{}) if p['provider']=='minimax-h3-api' else response
            status=result.get('status')
            state.update(status=status,updated_at=time.time());save(state_file,state)
            if status=='succeeded':break
            if status in {'failed','cancelled','canceled','expired'}:
                code=(result.get('error') or {}).get('code','unknown')
                raise RuntimeError(f'Remote video task {status}, code={code}; see provider console task {state["request_id"]}')
            if status not in {'queued','pending','running','processing'}:raise RuntimeError('Unknown remote video status; remote task retained')
            time.sleep(p.get('poll_interval',10))
        content=result.get('content') or {}
        url=content.get('url') if p['provider']=='minimax-h3-api' else content.get('video_url')
        if not url:raise RuntimeError('Succeeded video task has no download URL')
        # Store response privately for usage reconciliation and download recovery.
        save(out/'api-result.json',result)
        reported=result.get('resolution')
        if reported and reported!=p['resolution']:raise RuntimeError('API returned a different resolution; no silent downgrade accepted')
        extension=p.get('output_format','mp4')
        dest=out/('video.'+extension)
        if not dest.exists():download(client,url,dest)
    try:info=inspect(dest)
    except Exception:
        dest.replace(dest.with_suffix(dest.suffix+'.invalid-'+str(time.time_ns())))
        raise RuntimeError('Downloaded video could not be decoded; resume saved remote task to download again') from None
    if not info.get('width') or abs(info.get('fps',0)-24)>.1:raise RuntimeError('API output has no video or unexpected FPS')
    if abs(info.get('duration',0)-p['duration'])>.8:raise RuntimeError('API output duration differs from reviewed shot')
    if bool(info.get('audio'))!=p.get('generate_audio',True):raise RuntimeError('API output audio state differs from reviewed request')
    validate_canvas(p,info,reported)
    save(out/'media-validation.json',info)
    metrics=dict(provider=p['provider'],backend_type='official-api',model=p['model'],
                 generation_mode='first-last-frame' if p.get('image_mode')=='first_last' else 'reference-to-video' if any(video.references(p).values()) else 'text-to-video',
                 resolution=p['resolution'],fps=info['fps'],width=info['width'],height=info['height'],duration=info['duration'],
                 whether_native_audio=bool(info['audio']),audio_source='native' if info['audio'] else 'none',
                 audio_sample_rate=info.get('audio_sample_rate'),audio_channels=info.get('audio_channels'),
                 video_codec=info.get('video_codec'),pixel_format=info.get('pixel_format'),lip_sync='unverified',
                 request_id=state['request_id'],inference_seconds=(result.get('timings') or {}).get('inference'),
                 api_elapsed_seconds=round(time.monotonic()-started,3),
                 total_elapsed_seconds=round(time.time()-state['submitted_at'],3),usage=result.get('usage',{}),
                 backend_timings=result.get('timings'),requested_seed=p['spec']['seed'],seed_applied=False,
                 returned_seed=result.get('seed'),steps=None,quality='provider-managed',num_gpus=0)
    return dest,metrics


def validate_canvas(p,info,reported=None):
    """Validate documented native canvases, never resize model outputs to pass."""
    from engine.video_models import RATIOS
    ratio=p['aspect_ratio'];w,h=info['width'],info['height']
    if ratio!='adaptive':
        a,b=map(int,ratio.split(':'))
        if abs(w/h-a/b)>.06:raise RuntimeError('API output aspect ratio differs from reviewed target')
    if p['provider']=='seedance-api' and ratio in RATIOS:
        index=RATIOS.index(ratio)
        canvases={
            '480p':[(992,432),(864,496),(752,560),(640,640),(560,752),(496,864)],
            '720p':[(1470,630),(1280,720),(1112,834),(960,960),(834,1112),(720,1280)],
            '1080p':[(2206,946),(1920,1080),(1664,1248),(1440,1440),(1248,1664),(1080,1920)],
            '4k':[(4398,1886),(3840,2160),(3326,2494),(2880,2880),(2494,3326),(2160,3840)],
        }
        if p['model']=='doubao-seedance-2-5-260628':canvases['480p']=[(992,432),(854,480),(752,560),(640,640),(560,752),(480,854)]
        if (w,h)!=canvases[p['resolution']][index]:raise RuntimeError('API dimensions differ from official resolution/ratio table')
    else:
        # MiniMax labels describe generation tiers, not a universal short-edge
        # formula. Require its official tier response and a plausible actual canvas.
        if reported!=p['resolution']:raise RuntimeError('API response must confirm the requested resolution tier')
        minimum={'480P':256,'768P':432,'2K':1000,'480p':256,'720p':432,'1080p':630,'4k':1600}[p['resolution']]
        if min(w,h)<minimum:raise RuntimeError('API video is smaller than requested tier')
