"""Local H3 Base sampling contract, media validation and review fingerprints.

SGLang native BF16/FP32, exact attention, all denoising steps; no hosted services.
"""
import hashlib
import json
from pathlib import Path

REFERENCE_FIELDS = ('reference_images', 'style_reference_images', 'reference_videos',
                    'reference_audio', 'sound_reference')
LIMITS = {'image': (9, 30, {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'}),
          'video': (3, 50, {'.mp4', '.mov'}), 'audio': (3, 15, {'.wav', '.mp3'})}


def references(params):
    spec = params.get('spec', {})
    def items(field):
        values = params.get(field, spec.get(field, []))
        if isinstance(values, str):values = [values] if values else []
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError(field + ' must be a list of project-relative paths')
        return values
    return {'image': items('reference_images') + items('style_reference_images'),
            'video': items('reference_videos'),
            'audio': items('reference_audio') + items('sound_reference')}


def compose_prompt(p):
    s = p['spec']
    parts = [('Scene', p.get('effective_prompt', s.get('effective_prompt') or s.get('prompt', ''))),
             ('Motion', p.get('motion_prompt') or s.get('motion_prompt') or p.get('action_prompt') or s.get('action_prompt')),
             ('Camera', p.get('camera_prompt') or s.get('camera_prompt') or s.get('camera')),
             ('Continuity', p.get('continuity_prompt') or s.get('continuity_prompt')),
             ('Style', p.get('style_prompt') or s.get('style_prompt')),
             ('Dialogue (speak verbatim)', p.get('dialogue', s.get('dialogue'))),
             ('Sound', p.get('sound_intent') or s.get('sound_intent') or s.get('sound')),
             ('Avoid', p.get('effective_negative', s.get('effective_negative')))]
    text = '\n'.join(f'{label}: {value}' for label, value in parts if value)
    if not parts[0][1] or len(text) > 7000:raise ValueError('H3 prompt requires a scene and must be <=7000 characters')
    return text


def validate(p, pid, media=True):
    from engine.core import safe_file
    if p.get('model') != 'MiniMax-H3':raise ValueError('Only the official MiniMax-H3 Base model is supported')
    if p.get('inference_mode', 'local') != 'local':raise ValueError('Only local H3 inference is enabled')
    if p.get('quality', 'exact') != 'exact':raise ValueError('H3 quality must be exact (no approximate acceleration)')
    if p.get('resolution') != '768P':raise ValueError('Local official H3 Base quality recipe is 768P; 2K regeneration is not open locally')
    topology=[p.get('num_gpus',2),p.get('tp_size',2),p.get('ulysses_degree',1)]
    if p.get('gpu') not in (None,-1):raise ValueError('H3 uses allocation lane -1, not a single GPU lane')
    if any(isinstance(n,bool) or not isinstance(n,int) or n<1 for n in topology) or topology[0]!=topology[1]*topology[2] or topology[0]<2:
        raise ValueError('H3 GPU topology requires num_gpus = tp_size * ulysses_degree, at least 2 GPUs')
    if isinstance(p.get('steps'), bool) or not isinstance(p.get('steps'), int) or not 50 <= p['steps'] <= 200:raise ValueError('H3 final quality requires 50–200 steps')
    duration = p.get('duration')
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not float(duration).is_integer() or not 4 <= duration <= 15:
        raise ValueError('H3 duration must be an integer from 4 to 15 seconds')
    if p.get('fps') != 24:raise ValueError('H3 native FPS is fixed at 24; frame interpolation is not generation quality')
    if p.get('native_audio', True) is not True or p.get('audio_mode', 'native') != 'native' or p.get('audio_sample_rate', 32000) != 32000:
        raise ValueError('H3 generates native 32kHz stereo audio; local H3 has no independent audio mode/sample-rate switch')
    for key in ('frames', 'high_model', 'low_model', 'high_lora', 'low_lora', 'split_step', 'family', 'lora', 'cfg', 'text_encoder', 'vae', 'reuse_comfy_graph'):
        if key in p:raise ValueError('Removed Wan/accelerator parameter: ' + key)
    ratio = p.get('aspect_ratio', '16:9')
    if ratio not in {'21:9', '16:9', '4:3', '1:1', '3:4', '9:16'}:raise ValueError('Invalid H3 aspect ratio')
    # Native H3 canvas uses multiples of 32 at a 768-pixel short edge.
    width, height = p.get('width'), p.get('height')
    if any(isinstance(n, bool) or not isinstance(n, int) or not 256 <= n <= 5760 or n % 32 for n in (width, height)):
        raise ValueError('分辨率 / export width and height must be multiples of 32 in [256,5760]')
    a, b = map(int, ratio.split(':'))
    if min(width, height) != 768:raise ValueError('Native H3 Base canvas requires a 768-pixel short edge')
    if abs(width / height - a / b) > .05:raise ValueError('Export canvas and H3 aspect_ratio disagree')
    canvases={'21:9':(1792,768),'16:9':(1344,768),'4:3':(1024,768),
              '1:1':(768,768),'3:4':(768,1024),'9:16':(768,1344)}
    if (width,height)!=canvases[ratio]:raise ValueError('width/height must match the native H3 canvas for aspect_ratio')
    refs = references(p)
    if sum(map(len, refs.values())) > 12:raise ValueError('H3 accepts at most 12 reference files in total')
    for kind, paths in refs.items():
        count, size, extensions = LIMITS[kind]
        configured = p.get('max_reference_' + ('images' if kind == 'image' else 'videos' if kind == 'video' else 'audio'), count)
        if len(paths) > min(count, configured):raise ValueError(f'Too many {kind} references (maximum {count})')
        total = 0.
        for rel in paths:
            path = safe_file(pid, rel)
            if not path.is_file():raise ValueError('Reference does not exist: ' + rel)
            if path.suffix.lower() not in extensions:raise ValueError('Invalid reference format: ' + rel)
            if path.stat().st_size > size * 1024**2:raise ValueError(f'{kind} reference exceeds {size} MiB: {rel}')
            if not media:continue
            if kind == 'image':
                from PIL import Image
                with Image.open(path) as im:w, h = im.size; im.verify()
            else:
                from engine.export import inspect
                info = inspect(path); d = info.get('duration') or 0
                if not 2 <= d <= 15:raise ValueError('Reference duration must be 2–15 seconds: ' + rel)
                total += d
                if kind == 'audio':
                    if not info['audio']:raise ValueError('No audio stream: ' + rel)
                    continue
                if not info.get('width') or info.get('video_codec') not in {'h264', 'hevc'} or not 23.976 <= info['fps'] <= 60:
                    raise ValueError('Reference video requires H264/HEVC at 23.976–60 FPS: ' + rel)
                if info['audio'] and info.get('audio_codec') not in {'aac', 'mp3', 'mp3float'}:raise ValueError('Reference video audio requires AAC/MP3')
                w, h = info['width'], info['height']
            if not (256 <= w <= 5760 and 256 <= h <= 5760 and .4 <= w/h <= 2.5):
                raise ValueError('Reference dimensions/aspect ratio outside H3 limits: ' + rel)
        if total > 15.05:raise ValueError(kind + ' references exceed 15 seconds total')
    compose_prompt(p)
    return refs


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def fingerprint(p, pid):
    from engine.core import safe_file
    clean = {k:v for k,v in p.items() if k not in {'input_review', 'free_mib_at_start', 'execution_id', 'approved_edit_range'}}
    hashes = {}
    for paths in references(p).values():
        for rel in paths:
            with safe_file(pid, rel).open('rb') as stream:
                hashes[rel] = hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else _hash(stream)
    return digest({'params': clean, 'files': hashes})


def _hash(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(1024**2), b''):h.update(block)
    return h.hexdigest()


def request(p, pid):
    from engine.core import safe_file
    refs = validate(p, pid)
    conditions = [{'type':kind, 'uri':safe_file(pid, rel).as_uri(), 'role':'reference'}
                  for kind, paths in refs.items() for rel in paths]
    return {'prompt':compose_prompt(p), 'task':'ref2va' if conditions else 't2va',
            'conditions':conditions, 'target':{'short_edge':768,
            'aspect_ratio':p.get('aspect_ratio', '16:9'), 'duration_seconds':float(p['duration'])},
            'num_outputs_per_prompt':1, 'num_inference_steps':p['steps'],
            'quality':'exact', 'output_quality':'maximum',
            'flow_shift':p.get('flow_shift', 12.0), 'audio_flow_shift':p.get('audio_flow_shift', 3.0),
            'reference_resize_mode':'max', 'seed':p['spec']['seed']}
