"""Offline contract, approval integrity, allocation and real audio export regressions."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from PIL import Image

from engine import core as c
from engine.workflows import h3
from engine.providers import minimax_h3 as provider
from engine import export
from engine.scheduler import worker


@pytest.fixture
def project(tmp_path, monkeypatch):
    root=tmp_path/'studio'
    (root/'configs').mkdir(parents=True)
    shutil.copy(c.ROOT/'configs/project.template.yaml',root/'configs/project.template.yaml')
    for module in (c,provider,export,worker):monkeypatch.setattr(module,'ROOT',root)
    c.init();c.create_project('film')
    source=root/'input.json'
    source.write_text(json.dumps([{'episode':'01','shot':'001','duration':4,'prompt':'A quiet lake',
                                   'dialogue':'你好','speaker':'unregistered-native-speaker',
                                   'camera_prompt':'slow dolly','sound_intent':'lake ambience'}]))
    c.import_script('film',source)
    return root


def params(tid):return next(t for t in c.tasks('film') if t['id']==tid)['params']


def video_file(path, tone=880, audio=True):
    import imageio_ffmpeg
    args=[imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i','color=c=blue:s=1344x768:r=24:d=4']
    if audio:args+=['-f','lavfi','-i',f'sine=frequency={tone}:sample_rate=32000:duration=4','-ac','2','-c:a','aac']
    args+=['-c:v','libx264','-preset','ultrafast','-threads','2','-pix_fmt','yuv420p','-t','4',str(path)]
    subprocess.run(args,check=True,capture_output=True)
    return path


def complete_video(project, audio=True):
    tid=c.submit('film','01','001','video');c.review_inputs(tid,'approve')
    path=c.project_dir('film')/'production/native.mp4';video_file(path,audio=audio)
    with c.db() as conn:conn.execute("UPDATE tasks SET status='review',output='production/native.mp4' WHERE id=?",(tid,))
    c.review(tid,'approve')
    return tid,path


def test_local_quality_defaults_and_no_comfy_video(project):
    from engine.providers import resolve
    cfg=c.config('film');v=cfg['models']['video']
    assert v['provider']=='minimax-h3' and v['inference_mode']=='local'
    assert v['steps']==50 and v['quality']=='exact' and cfg['format']['fps']==24
    assert cfg['defaults']['duration']==15 and not cfg['routing']['paid_api_enabled']
    with pytest.raises(ValueError,match='视频仅支持'):resolve('comfy','video')
    for override in [{'steps':4},{'fps':16},{'inference_mode':'api'},{'resolution':'2K'}, {'quality':'high'}]:
        with pytest.raises(ValueError):c.submit('film','01','001','video',override)


def test_reference_bytes_and_configuration_require_reapproval(project,monkeypatch):
    ref=c.project_dir('film')/'assets/hero.png';Image.new('RGB',(256,256),'red').save(ref)
    c.bind_references('film','01','001',['assets/hero.png'])
    tid=c.submit('film','01','001','video')
    c.review_inputs(tid,'approve')
    monkeypatch.setattr(provider,'required_files',lambda p:[])
    def ready():
        with c.db() as conn:
            t=dict(conn.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone())
            return worker.ready(t,c.get_shot(conn,'film','01','001'),conn)
    assert ready()
    Image.new('RGB',(256,256),'green').save(ref)
    assert ready() is None
    c.review_inputs(tid,'approve');assert ready()
    with c.db() as conn:
        p=params(tid);p['steps']=80
        conn.execute('UPDATE tasks SET params=? WHERE id=?',(json.dumps(p),tid))
    assert ready() is None


def test_multimodal_request_and_real_format_validation(project):
    ref=c.project_dir('film')/'assets/hero.png';Image.new('RGB',(256,256),'red').save(ref)
    clip=video_file(c.project_dir('film')/'assets/motion.mp4')
    import imageio_ffmpeg
    voice=c.project_dir('film')/'assets/voice.wav'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-i',str(clip),'-vn',str(voice)],check=True)
    for field,rel in [('reference_images','assets/hero.png'),('reference_videos','assets/motion.mp4'),('reference_audio','assets/voice.wav')]:
        c.bind_references('film','01','001',[rel],field=field)
    tid=c.submit('film','01','001','video',{'seed':1234})
    body=h3.request(params(tid),'film')
    assert body['task']=='ref2va' and body['seed']==1234
    assert [r['type'] for r in body['conditions']]==['image','video','audio']
    assert all(r['uri'].startswith('file://') for r in body['conditions'])
    assert body['quality']=='exact' and body['num_inference_steps']==50
    assert all(t in body['prompt'] for t in ['slow dolly','你好','lake ambience'])
    c.review_inputs(tid,'reject','test')
    with pytest.raises(ValueError,match='format'):
        c.submit('film','01','001','video',{'reference_audio':['assets/hero.png']})
    with pytest.raises(ValueError,match='跨项目'):
        c.submit('film','01','001','video',{'reference_videos':['../other/movie.mp4']})


def test_native_track_survives_export_even_with_selected_tts(project):
    tid,original=complete_video(project)
    # An approved unrelated external candidate must never replace native audio implicitly.
    tts=c.submit('film','01','001','image')
    with c.db() as conn:
        conn.execute("UPDATE tasks SET kind='tts',status='approved',output='production/missing-external.wav' WHERE id=?",(tts,))
        conn.execute('UPDATE shots SET selected_tts=? WHERE project=?',(tts,'film'))
    result=export.export_episode('film','01')
    dest=Path(result['path']);timeline=json.loads((dest/'timeline.json').read_text())['clips'][0]
    assert timeline['audio_source']=='native' and timeline['whether_native_audio']
    assert timeline['voice_task'] is None
    # The normalized source clip is byte-identical; no PNG round-trip or replacement.
    assert (dest/'000.mp4').read_bytes()==original.read_bytes()
    assert export.inspect(dest/'clean.mp4')['audio']
    def packets(path):
        import av
        with av.open(str(path)) as container:return [bytes(p) for p in container.demux(audio=0) if p.size]
    assert packets(dest/'clean.mp4')==packets(original)
    assert result['media']['audio_sources']==['native']


def test_missing_native_audio_fails_instead_of_silence(project):
    complete_video(project,audio=False)
    with pytest.raises(ValueError,match='原生音轨缺失'):export.export_episode('film','01')


def test_explicit_external_tts_policy_replaces_native(project):
    complete_video(project)
    cfg=c.config('film');cfg['voices']={'unregistered-native-speaker':{'speaker':'Dylan'}}
    cfg['export']['audio_policy']='external_tts'
    (c.project_dir('film')/'project.yaml').write_text(yaml.safe_dump(cfg))
    tid=c.submit('film','01','001','tts')
    import imageio_ffmpeg
    ap=c.project_dir('film')/'production/tts.wav'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
                    'sine=frequency=440:sample_rate=32000:duration=3.5',str(ap)],check=True)
    with c.db() as conn:conn.execute("UPDATE tasks SET status='review',output='production/tts.wav' WHERE id=?",(tid,))
    c.review(tid,'approve')
    result=export.export_episode('film','01')
    assert result['media']['audio_sources']==['external_tts']
    assert result['media']['clips'][0]['voice_task']==tid


def test_provider_calls_local_runner_and_retains_container(project,monkeypatch):
    tid=c.submit('film','01','001','video');c.review_inputs(tid,'approve')
    source=video_file(project/'source.mp4')
    py=project/'.venv-h3/bin/python';py.parent.mkdir(parents=True);py.touch()
    monkeypatch.setattr(provider,'required_files',lambda p:[])
    class Process:
        pid=12345
        def __init__(self,command,**kw):
            assert command[0]==str(py) and command[1].endswith('scripts/h3_infer.py')
            assert kw['env']['HF_HUB_OFFLINE']=='1'
            request=Path(command[-1]);data=json.loads(request.read_text())
            assert data['num_gpus']==2 and data['tp_size']==2
            shutil.copy(source,request.parent/'video.mp4')
        def wait(self,**kw):return 0
    monkeypatch.setattr(provider.subprocess,'Popen',Process)
    out=c.project_dir('film')/'production'/tid;out.mkdir()
    task=next(t for t in c.tasks('film') if t['id']==tid)
    path,metrics=provider.run(task,-1,out)
    assert path.read_bytes()==source.read_bytes()
    assert metrics['whether_native_audio'] and metrics['backend_type']=='sglang-local'
    assert metrics['request_id']==tid and metrics['audio_sample_rate']==32000


def test_h3_group_claim_needs_allocation_and_does_not_query_comfy(project,monkeypatch):
    tid=c.submit('film','01','001','video');c.review_inputs(tid,'approve')
    monkeypatch.setattr(provider,'required_files',lambda p:[])
    monkeypatch.setenv('NARRATIVE_H3_DEVICES','["GPU-a","GPU-b"]')
    def query(command,**kw):
        assert command[0]=='nvidia-smi'
        return type('Result',(),{'stdout':'81000\n'})()
    monkeypatch.setattr(worker.subprocess,'run',query)
    task=worker.claim(-1,['video'])
    assert task and task['id']==tid
    assert task['params']['steps']==50 and task['gpu']==-1


def test_explicit_migration_backs_up_and_retires_legacy_queue(project):
    cfg=c.config('film');cfg['models']['video']={'provider':'comfy','family':'wan22-a14b','steps':4}
    path=c.project_dir('film')/'project.yaml';path.write_text(yaml.safe_dump(cfg))
    with c.db() as conn:
        conn.execute("INSERT INTO tasks(id,project,episode,shot,kind,status,params,created) VALUES('legacy','film','01','001','video','pending','{}',0)")
    result=c.migrate_h3('film')
    assert Path(result['backup']).exists()
    assert c.config('film')['models']['video']['provider']=='minimax-h3'
    assert c.tasks('film')[0]['status']=='failed'
    tid=c.submit('film','01','001','video')
    assert params(tid)['steps']==50
    with c.db() as conn:conn.execute("UPDATE tasks SET status='running' WHERE id=?",(tid,))
    with pytest.raises(ValueError,match='运行任务'):c.migrate_h3('film')


def test_h3_device_error_pauses_group_without_degrading_recipe(project,monkeypatch):
    tid=c.submit('film','01','001','video');c.review_inputs(tid,'approve')
    task=next(t for t in c.tasks('film') if t['id']==tid)
    def fail(task,gpu,out):
        (out/'h3-inference.log').write_text('torch.OutOfMemoryError: CUDA out of memory')
        raise RuntimeError('Local H3 exited 1; see h3-inference.log')
    monkeypatch.setattr(provider,'run',fail)
    worker.perform(task,-1)
    assert (project/'runtime/gpu--1.paused.json').exists()
    assert c.tasks('film')[0]['status']=='failed'
    assert params(tid)['steps']==50 and params(tid)['quality']=='exact'


def test_edit_range_change_requires_reapproval(project):
    complete_video(project)
    cfg=c.config('film');cfg['editing']={'trims':{'01':{'001':{'in':1,'out':3}}}}
    (c.project_dir('film')/'project.yaml').write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError,match='重新审核'):export.export_episode('film','01')


def test_explicit_mix_preserves_native_and_uses_external(project):
    complete_video(project)
    cfg=c.config('film');cfg['voices']={'unregistered-native-speaker':{'speaker':'Dylan'}}
    cfg['export']['audio_policy']='mix'
    (c.project_dir('film')/'project.yaml').write_text(yaml.safe_dump(cfg))
    tid=c.submit('film','01','001','tts')
    import imageio_ffmpeg
    ap=c.project_dir('film')/'production/tts.wav'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
                    'sine=frequency=440:sample_rate=32000:duration=3.5',str(ap)],check=True)
    with c.db() as conn:conn.execute("UPDATE tasks SET status='review',output='production/tts.wav' WHERE id=?",(tid,))
    c.review(tid,'approve')
    result=export.export_episode('film','01')
    assert result['media']['audio_sources']==['mix']
    assert result['media']['clips'][0]['whether_native_audio']
