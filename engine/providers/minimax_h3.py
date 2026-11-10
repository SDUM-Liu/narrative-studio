"""Local-only H3 Base adapter. One SGLang process owns the entire GPU allocation."""
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

from engine.core import ROOT
from engine.workflows import h3


def model_path(p):
    path = (ROOT / p.get('model_path', 'models/minimax-h3')).resolve()
    if not path.is_relative_to((ROOT/'models').resolve()):raise ValueError('H3 model_path must stay under models/')
    return path


def required_files(p):
    manifest = ROOT/'model-manifests/models.json'
    if not manifest.is_file():return [model_path(p)/'model_index.json']
    items = json.loads(manifest.read_text())
    spec = p.get('spec', {})
    variant = 'Ref2VA' if any(h3.references({**p, 'spec':spec}).values()) else 'FL2VA'
    files = [model_path(p)/Path(i['path']).relative_to('models/minimax-h3') for i in items
            if i['group'] == 'h3' and (f'/{variant}/' in i['path'] or '/FL2VA/' not in i['path'] and '/Ref2VA/' not in i['path'])]
    return files or [model_path(p)/'model_index.json']


def run(task, gpu, out):
    from engine.export import inspect
    p = task['params']; pid = task['project']
    h3.validate(p, pid)
    approval = p.get('input_review', {})
    if not approval.get('approved') or approval.get('fingerprint') != h3.fingerprint(p, pid):
        raise ValueError('H3 input review missing or materials/configuration changed; approve inputs again')
    missing = [str(f) for f in required_files(p) if not f.is_file()]
    if missing:raise FileNotFoundError('H3 weights not installed: ' + ', '.join(missing[:3]))
    python = ROOT/'.venv-h3/bin/python'
    if not python.is_file():raise FileNotFoundError('Run bash scripts/setup_h3.sh first')
    request = h3.request(p, pid)
    request.update(output_path=str(out), output_file_name='video.mp4', save_output=True,
                   request_id=task['id'])
    execution = {'id':uuid.uuid4().hex, 'model_path':str(model_path(p)),
                 'variant':'ref2va' if request['task'] == 'ref2va' else 'fl2va',
                 'num_gpus':p.get('num_gpus', 2), 'tp_size':p.get('tp_size', 2),
                 'ulysses_degree':p.get('ulysses_degree', 1), 'sampling':request}
    (out/'h3-request.json').write_text(json.dumps(execution, ensure_ascii=False, indent=2))
    # Offline flags stop implicit Hub downloads. No HTTP client or cloud credentials.
    env = {**os.environ, 'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1',
           'HF_DATASETS_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1',
           'SGLANG_CACHE_DIT_ENABLED':'false'}
    for key in list(env):
        if key.startswith(('SGLANG_CACHE_DIT_', 'SGLANG_DIFFUSION_MINIMAX_H3_SPECTRUM')) and key != 'SGLANG_CACHE_DIT_ENABLED':env.pop(key)
    start = time.monotonic()
    with (out/'h3-inference.log').open('w') as log:
        process = subprocess.Popen([str(python), str(ROOT/'scripts/h3_infer.py'),
                                    '--request', str(out/'h3-request.json')],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        try:code = process.wait(timeout=p.get('timeout', 86400))
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:os.killpg(process.pid, signal.SIGKILL);process.wait()
            raise
    if code:raise RuntimeError(f'Local H3 exited {code}; see h3-inference.log; quality was not reduced')
    dest = out/'video.mp4'
    if not dest.is_file():raise RuntimeError('H3 returned no video.mp4; see h3-inference.log')
    info = inspect(dest)
    if not info.get('width') or not info.get('audio'):raise RuntimeError('H3 output lacks video or native audio; no silent substitute')
    # Native temporal VAE aligns frames; retain the real duration in metrics/timeline.
    if abs(info.get('fps', 0)-24) > .1 or abs((info.get('duration') or 0)-p['duration']) > .75:
        raise RuntimeError('H3 native FPS/duration outside reviewed target tolerance')
    if (info['width'],info['height'])!=(p['width'],p['height']):raise RuntimeError('H3 output dimensions differ from reviewed native canvas; no quality fallback')
    if info.get('audio_sample_rate') != 32000 or info.get('audio_channels') != 2:
        raise RuntimeError('H3 output must carry native 32kHz stereo audio')
    (out/'media-validation.json').write_text(json.dumps(info, indent=2))
    performance = out/'h3-performance.json'
    metrics = json.loads(performance.read_text()) if performance.exists() else {}
    metrics.update(provider='minimax-h3', backend_type='sglang-local', model='MiniMax-H3',
                   generation_mode=request['task'], resolution='768P', width=info['width'], height=info['height'],
                   fps=info['fps'], duration=info['duration'], whether_native_audio=True,
                   audio_source='native', audio_sample_rate=32000, audio_channels=2,
                   lip_sync='unverified', quality='exact', steps=p['steps'], seed=p['spec']['seed'],
                   inference_seconds=round(time.monotonic()-start, 3), request_id=task['id'],
                   execution_id=execution['id'], slurm_job_id=os.environ.get('SLURM_JOB_ID'),
                   gpu_allocation=json.loads(os.environ.get('NARRATIVE_H3_DEVICES','[]')),
                   num_gpus=execution['num_gpus'], tp_size=execution['tp_size'],
                   ulysses_degree=execution['ulysses_degree'])
    return dest, metrics
