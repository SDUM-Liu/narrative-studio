"""Versioned capabilities from upstream API schemas, checked 2026-11-10."""
from copy import deepcopy

RATIOS = ['21:9', '16:9', '4:3', '1:1', '3:4', '9:16']
MINIMAX_DOC = 'https://platform.minimax.io/docs/api-reference/video/generation/api/v2-video-generation.json'
ARK_DOC = 'https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh'
PROFILES = {
    'h3-local': dict(label='MiniMax H3 Base · 本地双卡', provider='minimax-h3', model='MiniMax-H3',
                     resolutions=['768P'], default_resolution='768P', duration=[4, 15],
                     references=[9, 3, 3], max_files=12, reference_seconds=15, source=MINIMAX_DOC),
    'h3-api': dict(label='MiniMax H3 标准版 · 官方 API', provider='minimax-h3-api', model='MiniMax-H3',
                   resolutions=['768P', '2K'], default_resolution='2K', duration=[4, 15],
                   references=[9, 3, 3], max_files=12, reference_seconds=15, source=MINIMAX_DOC),
    'h3-max-api': dict(label='MiniMax H3 Max · MiniMax 官方 API', provider='minimax-h3-api', model='MiniMax-H3-Max',
                       resolutions=['480P', '768P'], default_resolution='768P', duration=[5, 15],
                       references=[9, 3, 3], max_files=12, reference_seconds=15, source=MINIMAX_DOC),
    'seedance-2.0': dict(label='Seedance 2.0 · 火山方舟 API', provider='seedance-api', model='doubao-seedance-2-0-260128',
                         resolutions=['480p', '720p', '1080p', '4k'], default_resolution='4k', duration=[4, 15],
                         references=[9, 3, 3], max_files=12, reference_seconds=15, source=ARK_DOC),
    'seedance-2.5': dict(label='Seedance 2.5 · 火山方舟 API', provider='seedance-api', model='doubao-seedance-2-5-260628',
                         resolutions=['480p', '720p', '1080p'], default_resolution='1080p', duration=[4, 30],
                         references=[30, 10, 10], max_files=50, reference_seconds=30, source=ARK_DOC),
}


def catalog():
    return {'checked_at': '2026-11-10', 'profiles': {
        k: {**deepcopy(v), 'fps': [24], 'aspect_ratios': RATIOS + ([] if k == 'h3-local' else ['adaptive']),
            'output_formats': ['mp4', 'mov'] if k == 'seedance-2.5' else ['mp4'],
            'api_key_env': None if k == 'h3-local' else 'ARK_API_KEY' if k.startswith('seedance') else 'MINIMAX_API_KEY',
            'seed_control': k == 'h3-local'} for k, v in PROFILES.items()}}


def profile_for(p):
    for name, profile in PROFILES.items():
        if (p.get('provider'), p.get('model')) == (profile['provider'], profile['model']):
            return name, profile
    raise ValueError('Unsupported video provider/model; use studio video-models')


def defaults(name, resolution=None):
    if name not in PROFILES:raise ValueError('Unknown video profile: ' + name)
    cap = PROFILES[name]
    resolution = resolution or cap['default_resolution']
    if resolution not in cap['resolutions']:raise ValueError(f'{name} resolution must be one of {cap["resolutions"]}')
    if name == 'h3-local':
        from engine import core
        import yaml
        return deepcopy(yaml.safe_load((core.ROOT/'configs/project.template.yaml').read_text())['models']['video'])
    return dict(provider=cap['provider'], model=cap['model'], inference_mode='api', resolution=resolution,
                native_audio=True, audio_mode='native', timeout=86400, poll_interval=10,
                output_format='mp4', prompt_expansion_mode='quality' if name == 'h3-max-api' else 'disabled',
                generate_audio=True, watermark=False, return_last_frame=False, image_mode='reference',
                max_reference_images=cap['references'][0], max_reference_videos=cap['references'][1],
                max_reference_audio=cap['references'][2])


def configure(pid, name, resolution=None, ratio=None, duration=None, options=None):
    """Change only future submissions; queued candidates retain reviewed snapshots."""
    from engine import core
    import time, uuid, yaml, shutil
    cfg = core.config(pid)
    model = defaults(name, resolution)
    allowed_options={'output_format','generate_audio','prompt_expansion_mode','watermark','return_last_frame','image_mode'}
    if set(options or {})-allowed_options:raise ValueError('Unsupported video model options')
    if name=='h3-local' and options:raise ValueError('API options do not apply to the local H3 profile')
    model.update(options or {})
    if name!='h3-local':
        model['native_audio']=model['generate_audio']
        model['audio_mode']='native' if model['generate_audio'] else 'none'
    cap = PROFILES[name]
    ratio = ratio or cfg['format'].get('aspect_ratio', '16:9')
    allowed = RATIOS + ([] if name == 'h3-local' else ['adaptive'])
    if ratio not in allowed:raise ValueError('Unsupported aspect ratio')
    duration = duration if duration is not None else cap['duration'][1]
    if isinstance(duration, bool) or not isinstance(duration, int) or not cap['duration'][0] <= duration <= cap['duration'][1]:
        raise ValueError('Default duration outside model limits')
    if name!='h3-local':
        # first/last mode is checked against actual references at submission.
        from engine.workflows.cloud_video import validate
        check={**cfg['format'],**model,'aspect_ratio':ratio,'duration':duration,'spec':{'prompt':'Profile validation','seed':0}}
        check['image_mode']='reference'
        if model.get('image_mode') not in {'reference','first_last'}:raise ValueError('Invalid image_mode')
        if model.get('image_mode')=='first_last' and ratio!='adaptive':raise ValueError('first_last requires adaptive ratio')
        if ratio=='adaptive':check['aspect_ratio']='16:9'
        validate(check,pid,media=False)
    path = core.project_dir(pid)/'project.yaml'
    backup = path.with_name(f'project.pre-video-{int(time.time())}-{uuid.uuid4().hex[:6]}.yaml')
    shutil.copy2(path, backup)
    cfg['models']['video'] = model
    cfg['format'].update(aspect_ratio=ratio, fps=24)
    if name == 'h3-local':
        canvases = {'21:9':(1792,768), '16:9':(1344,768), '4:3':(1024,768), '1:1':(768,768), '3:4':(768,1024), '9:16':(768,1344)}
        cfg['format'].update(zip(('width','height'),canvases[ratio]))
    cfg['defaults']['duration'] = duration
    cfg['budget']['max_shot_duration'] = max(cfg['budget'].get('max_shot_duration', 15), cap['duration'][1])
    cfg['routing']['paid_api_enabled'] = name != 'h3-local'
    cfg['routing']['production'] = 'local' if name == 'h3-local' else 'api'
    # Retain each API's actual canvas, codec and bit depth through final export.
    cfg['export']['preserve_source_format'] = name != 'h3-local'
    tmp = path.with_suffix('.yaml.tmp')
    tmp.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False));tmp.replace(path)
    return {'project': pid, 'profile': name, 'video': model, 'backup': str(backup), 'existing_tasks_unchanged': True}
