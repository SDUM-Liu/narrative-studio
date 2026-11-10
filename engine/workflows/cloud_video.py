"""Official MiniMax V2 / Ark Contents Generations request contracts."""
import base64
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit
from engine.video_models import profile_for, RATIOS
from engine.workflows.h3 import compose_prompt, references


def remote_url(value, provider):
    if not isinstance(value, str):raise ValueError('Remote media location must be a string')
    parsed = urlsplit(value)
    if parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password:
        return value
    if provider == 'minimax-h3-api' and re.fullmatch(r'mm_file://[A-Za-z0-9_-]+',value):return value
    if provider == 'seedance-api' and re.fullmatch(r'asset://[A-Za-z0-9_-]+',value):return value
    raise ValueError('Use HTTPS media URLs, MiniMax mm_file:// IDs or Ark asset:// IDs')


def validate(p, pid, media=True):
    from engine.core import safe_file
    name, cap = profile_for(p)
    if name == 'h3-local' or p.get('inference_mode') != 'api':raise ValueError('API profile requires inference_mode: api')
    if p.get('gpu') not in (None, -2):raise ValueError('API video tasks use CPU/network lane -2')
    if p.get('resolution') not in cap['resolutions']:raise ValueError(f'{name} resolution must be one of {cap["resolutions"]}; no downgrade')
    if p.get('fps') != 24:raise ValueError('Official video API output is 24 FPS; fps is not a sampling switch')
    if p.get('aspect_ratio') not in RATIOS + ['adaptive']:raise ValueError('Unsupported API aspect ratio')
    duration = p.get('duration')
    if isinstance(duration, bool) or not isinstance(duration, (int,float)) or not float(duration).is_integer() or not cap['duration'][0] <= duration <= cap['duration'][1]:
        raise ValueError(f'{name} requires integer duration in {cap["duration"]}; automatic duration/edit mode is not a shot recipe')
    for key in ('steps','cfg','flow_shift','audio_flow_shift','num_gpus','tp_size','ulysses_degree','model_path','high_model','low_model','high_lora','low_lora','split_step','quality','audio_sample_rate','base_url','api_key','token','headers','seed'):
        if key in p:raise ValueError('Unsupported API parameter: '+key+'; select a complete API profile')
    if p.get('native_audio', True) != p.get('generate_audio', True):raise ValueError('native_audio and generate_audio must agree')
    if not isinstance(p.get('generate_audio', True), bool):raise ValueError('generate_audio must be boolean')
    if p['provider'] == 'minimax-h3-api' and p.get('generate_audio', True) is not True:raise ValueError('MiniMax H3 API has no audio-disable switch')
    if p.get('audio_mode','native') not in {'native','none'}:raise ValueError('API audio_mode must be native or none')
    if (p.get('audio_mode','native') == 'native') != p.get('generate_audio', True):raise ValueError('audio_mode and generate_audio must agree')
    for key in ('watermark','return_last_frame'):
        if not isinstance(p.get(key,False),bool):raise ValueError(key+' must be boolean')
    if p.get('output_format','mp4') not in (['mp4','mov'] if name == 'seedance-2.5' else ['mp4']):raise ValueError('Unsupported output container')
    if p.get('prompt_expansion_mode','disabled') not in {'disabled','balanced','quality'}:raise ValueError('Invalid prompt_expansion_mode')
    if name != 'h3-max-api' and p.get('prompt_expansion_mode','disabled') != 'disabled':raise ValueError('Prompt expansion option is exposed only by MiniMax-H3-Max')
    for key,lo,hi in [('poll_interval',1,60),('timeout',60,172800)]:
        value=p.get(key,10 if key=='poll_interval' else 86400)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not lo<=value<=hi:raise ValueError('Invalid '+key)
    refs=references(p)
    if sum(map(len,refs.values())) > cap['max_files']:raise ValueError('Too many multimodal references')
    if not any(refs.values()) and p['aspect_ratio']=='adaptive' and p['provider']=='minimax-h3-api':raise ValueError('MiniMax text-to-video needs an explicit aspect ratio')
    if name=='seedance-2.0' and refs['audio'] and not (refs['image'] or refs['video']):raise ValueError('Seedance 2.0 audio references require image/video; 2.5 supports audio-only references')
    image_mode=p.get('image_mode','reference')
    if image_mode not in {'reference','first_last'}:raise ValueError('image_mode must be reference or first_last')
    if image_mode=='first_last':
        if not 1<=len(refs['image'])<=2 or refs['video'] or refs['audio'] or p.get('style_reference_images') or p.get('spec',{}).get('style_reference_images'):raise ValueError('first_last requires one or two images, without multimodal reference inputs')
        if p['aspect_ratio']!='adaptive':raise ValueError('first_last requires adaptive ratio to retain the input image canvas')
    if p['provider']=='minimax-h3-api' and (p.get('watermark',False) or p.get('return_last_frame',False)):
        raise ValueError('MiniMax V2 does not expose watermark/return_last_frame switches')
    locations=p.get('reference_locations',{})
    if not isinstance(locations,dict) or set(locations)-{rel for paths in refs.values() for rel in paths}:raise ValueError('reference_locations keys must match bound local reference paths')
    for value in locations.values():remote_url(value,p['provider'])
    ark=p['provider']=='seedance-api'
    for index,(kind,paths) in enumerate(refs.items()):
        configured=p.get('max_reference_'+{'image':'images','video':'videos','audio':'audio'}[kind],cap['references'][index])
        if isinstance(configured,bool) or not isinstance(configured,int) or not 0<=configured<=cap['references'][index]:raise ValueError('Invalid reference limit for '+kind)
        if len(paths)>configured:raise ValueError('Too many '+kind+' references')
        total=0
        extensions={'.jpg','.jpeg','.png','.webp','.heic','.heif'} if kind=='image' else {'.mp4','.mov'} if kind=='video' else {'.wav','.mp3'}
        if ark and kind=='image':extensions|={'.bmp','.tiff','.tif','.gif'}
        for rel in paths:
            path=safe_file(pid,rel)
            if not path.is_file() or path.suffix.lower() not in extensions:raise ValueError('Missing/unsupported reference: '+rel)
            size={'image':30,'video':200 if ark else 50,'audio':15}[kind]
            if path.stat().st_size>size*1024**2:raise ValueError('Reference exceeds official size limit: '+rel)
            if ark and kind=='video' and rel not in locations:raise ValueError('Ark video references require reference_locations HTTPS/asset://; local paths are not remote URLs')
            if not media:continue
            if kind=='image':
                from PIL import Image
                with Image.open(path) as im:w,h=im.size;im.verify()
            else:
                from engine.export import inspect
                info=inspect(path);d=info.get('duration') or 0
                if not 2<=d<=cap['reference_seconds']:raise ValueError('Reference duration outside official limits: '+rel)
                total+=d
                if kind=='audio':
                    if not info['audio']:raise ValueError('Reference has no audio: '+rel)
                    continue
                if info.get('video_codec') not in {'h264','hevc'} or not (24 if ark else 23.976)<=info.get('fps',0)<=60:raise ValueError('Unsupported reference codec/FPS: '+rel)
                codec=info.get('audio_codec','')
                if info['audio'] and codec not in {'aac','mp3','mp3float'} and not (ark and path.suffix.lower()=='.mov' and codec.startswith('pcm_')):raise ValueError('Unsupported reference audio codec: '+rel)
                w,h=info['width'],info['height']
                if ark and not 407696<=w*h<=8295044:raise ValueError('Ark reference video pixel count outside official bounds')
            low,high=(300,6000) if ark else (256,5760)
            if not (low<=w<=high and low<=h<=high and .4<=w/h<=2.5):raise ValueError('Reference dimensions outside official bounds: '+rel)
        if total>cap['reference_seconds']+.05:raise ValueError(kind+' references exceed combined duration limit')
    compose_prompt(p)
    return refs


def media_location(p,pid,rel):
    from engine.core import safe_file
    if rel in p.get('reference_locations',{}):return remote_url(p['reference_locations'][rel],p['provider'])
    path=safe_file(pid,rel)
    mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    if path.suffix.lower()=='.wav':mime='audio/wav'
    if path.suffix.lower()=='.mp3':mime='audio/mp3'
    return f'data:{mime};base64,'+base64.b64encode(path.read_bytes()).decode('ascii')


def request(p,pid):
    refs=validate(p,pid)
    content=[{'type':'text','text':compose_prompt(p)}]
    for kind,paths in refs.items():
        for index,rel in enumerate(paths):
            key=kind+'_url'
            role=('first_frame' if index==0 else 'last_frame') if p.get('image_mode')=='first_last' else 'reference_'+kind
            content.append({'type':key,key:{'url':media_location(p,pid,rel)},'role':role})
    payload={'model':p['model'],'content':content,'resolution':p['resolution'],
             'duration':int(p['duration']),'ratio':p['aspect_ratio']}
    if p['provider']=='minimax-h3-api':
        if p['model']=='MiniMax-H3-Max':payload['extra']={'prompt_expansion_mode':p.get('prompt_expansion_mode','quality')}
    else:
        payload.update(generate_audio=p.get('generate_audio',True),watermark=p.get('watermark',False),
                       return_last_frame=p.get('return_last_frame',False),service_tier='default')
        if p['model']=='doubao-seedance-2-5-260628':
            payload['output_format']=p.get('output_format','mp4')
            if any(refs.values()) and p.get('image_mode','reference')=='reference':payload['omni_reference_task_type']='reference'
    # Neither MiniMax V2 nor Ark Seedance 2.x declares caller-controlled seed/steps.
    if len(json.dumps(payload).encode())>64*1024**2:raise ValueError('API body exceeds 64 MiB; provide reference_locations instead of inline media')
    return payload
