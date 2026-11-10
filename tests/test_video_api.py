"""Official API contracts and recovery, with HTTP fixtures and real media."""
import json
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest
import yaml
from PIL import Image
from engine import core, export
from engine.video_models import catalog, configure, defaults
from engine.workflows import cloud_video, video
from engine.providers import cloud_video as provider
from engine.scheduler import worker


@pytest.fixture
def project(tmp_path, monkeypatch):
    root=tmp_path/'studio';(root/'configs').mkdir(parents=True)
    shutil.copy(core.ROOT/'configs/project.template.yaml',root/'configs/project.template.yaml')
    for module in (core,export,worker):monkeypatch.setattr(module,'ROOT',root)
    core.init();core.create_project('api-film')
    script=root/'story.json';script.write_text(json.dumps([{'episode':'01','shot':'001','duration':6,'prompt':'A quiet lake'}]))
    core.import_script('api-film',script)
    return root


def submit(profile='h3-api',overrides=None):
    configure('api-film',profile)
    tid=core.submit('api-film','01','001','video',overrides)
    core.review_inputs(tid,'approve')
    return next(t for t in core.tasks('api-film') if t['id']==tid)


def output_dir(task):
    path=core.project_dir(task['project'])/'production'/task['episode']/task['shot']/task['id']
    path.mkdir(parents=True,exist_ok=True)
    return path


@pytest.mark.parametrize('profile,resolution', [('h3-api','2K'),('h3-max-api','768P'),('seedance-2.0','4k'),('seedance-2.5','1080p')])
def test_official_profile_defaults_and_native_payload(project,profile,resolution):
    t=submit(profile);p=t['params'];payload=cloud_video.request(p,'api-film')
    assert p['resolution']==resolution and 'steps' not in p and 'num_gpus' not in p
    assert payload['resolution']==resolution and payload['duration']==6
    assert 'seed' not in payload and 'fps' not in payload
    assert not provider.required_files(p)
    assert core.config('api-film')['export']['preserve_source_format']
    if profile=='h3-max-api':assert payload['extra']=={'prompt_expansion_mode':'quality'}
    if profile.startswith('seedance'):assert payload['generate_audio'] and payload['service_tier']=='default'


@pytest.mark.parametrize('profile,resolution', [('h3-api','1080P'),('h3-max-api','2K'),('h3-max-api','1080P'),('seedance-2.5','4k')])
def test_invalid_resolution_rejected_without_downgrade(project,profile,resolution):
    with pytest.raises(ValueError,match='resolution'):configure('api-film',profile,resolution)
    assert core.config('api-film')['models']['video']['provider']=='minimax-h3'


def test_api_disabled_unless_project_explicitly_enabled(project):
    with pytest.raises(ValueError,match='explicitly selected'):core.submit('api-film','01','001','video',defaults('h3-api'))


def test_profile_change_preserves_pending_snapshot_and_approval(project):
    task=submit('h3-api');original=task['params']
    configure('api-film','seedance-2.5')
    current=core.tasks('api-film')[0]['params']
    assert current==original
    with core.db() as conn:assert core.inputs_current(current,core.get_shot(conn,'api-film','01','001'),'api-film')


def test_multimodal_encoding_and_reference_review(project):
    image=core.project_dir('api-film')/'assets/hero.png';Image.new('RGB',(320,320),'red').save(image)
    core.bind_references('api-film','01','001',['assets/hero.png'])
    task=submit('h3-api');payload=cloud_video.request(task['params'],'api-film')
    assert payload['content'][1]['role']=='reference_image'
    assert payload['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert '<redacted>' in provider.preview(task['params'],'api-film')['content'][1]['image_url']['url']
    Image.new('RGB',(320,320),'blue').save(image)
    with core.db() as conn:assert not core.inputs_current(task['params'],core.get_shot(conn,'api-film','01','001'),'api-film')


def test_first_last_roles_are_separate_from_semantic_references(project):
    for name in ['first','last']:
        Image.new('RGB',(320,320),'red').save(core.project_dir('api-film')/f'assets/{name}.png')
    core.bind_references('api-film','01','001',['assets/first.png','assets/last.png'])
    configure('api-film','h3-api',ratio='adaptive',options={'image_mode':'first_last'})
    tid=core.submit('api-film','01','001','video')
    p=next(t for t in core.tasks('api-film') if t['id']==tid)['params']
    assert [i.get('role') for i in cloud_video.request(p,'api-film')['content'][1:]]==['first_frame','last_frame']
    with pytest.raises(ValueError,match='adaptive'):video.validate({**p,'aspect_ratio':'16:9'},'api-film')


def test_seedance_video_uses_explicit_remote_asset_location(project):
    path=core.project_dir('api-film')/'assets/motion.mp4';path.write_bytes(b'fixture')
    p={**core.config('api-film')['format'],**defaults('seedance-2.5'),'duration':6,'spec':{'prompt':'scene','seed':0,'reference_videos':['assets/motion.mp4']}}
    with pytest.raises(ValueError,match='reference_locations'):cloud_video.validate(p,'api-film',media=False)
    p['reference_locations']={'assets/motion.mp4':'asset://asset-test'}
    assert cloud_video.validate(p,'api-film',media=False)['video']==['assets/motion.mp4']
    p['reference_locations']['assets/motion.mp4']='file:///tmp/private.mp4'
    with pytest.raises(ValueError,match='HTTPS'):cloud_video.validate(p,'api-film',media=False)


def test_cpu_api_worker_never_queries_cuda_and_gpu_worker_skips_api(project,monkeypatch):
    task=submit()
    monkeypatch.setattr(worker,'gpu_stats',lambda *_: (_ for _ in ()).throw(AssertionError('GPU access')))
    assert worker.claim(-1,['video']) is None
    claim=worker.claim(-2,['video'])
    assert claim['id']==task['id'] and claim['gpu']==-2


@pytest.mark.parametrize('override', [{'steps':4},{'seed':42},{'fps':16},{'api_key':'secret'},{'base_url':'https://untrusted.invalid'},{'duration':3},{'generate_audio':False}])
def test_unsupported_or_secret_parameters_never_enter_queue(project,override):
    configure('api-film','h3-api')
    with pytest.raises(ValueError):core.submit('api-film','01','001','video',override)
    assert core.tasks('api-film')==[]


def install_mock(monkeypatch,handler):
    actual=httpx.Client
    transport=httpx.MockTransport(handler)
    monkeypatch.setattr(provider.httpx,'Client',lambda **kw:actual(transport=transport,**kw))
    monkeypatch.setenv('MINIMAX_API_KEY','fixture-api-key')
    monkeypatch.setenv('ARK_API_KEY','fixture-ark-key')


def test_async_submit_poll_download_and_resume_never_reposts(project,monkeypatch):
    import imageio_ffmpeg
    source=project/'fixture.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i','color=s=1344x768:r=24:d=6',
                    '-f','lavfi','-i','sine=sample_rate=32000:duration=6','-c:v','libx264','-preset','ultrafast','-threads','2','-ac','2','-c:a','aac',str(source)],check=True)
    task=submit('h3-api',{'resolution':'768P'});out=output_dir(task);calls=[]
    def handler(req):
        calls.append((req.method,req.url.path))
        if req.method=='POST':
            assert req.headers['authorization']=='Bearer fixture-api-key'
            assert json.loads(req.content)['resolution']=='768P'
            return httpx.Response(200,json={'task_id':'remote-001'})
        if req.url.host=='cdn.test':
            assert 'authorization' not in req.headers
            return httpx.Response(200,content=source.read_bytes())
        return httpx.Response(200,json={'task':{'status':'succeeded','resolution':'768P','content':{'url':'https://cdn.test/video.mp4'},'usage':{'output_seconds':6}}})
    install_mock(monkeypatch,handler)
    dest,metrics=provider.run(task,-2,out)
    assert dest.read_bytes()==source.read_bytes() and metrics['whether_native_audio']
    assert metrics['request_id']=='remote-001' and not metrics['seed_applied']
    assert 'fixture-api-key' not in ''.join(p.read_text() for p in out.glob('*.json'))
    provider.run(task,-2,out)
    assert sum(m=='POST' for m,_ in calls)==1
    assert json.loads((out/'remote-task.json').read_text())['request_id']=='remote-001'


def test_ambiguous_submission_is_not_automatically_retried(project,monkeypatch):
    task=submit();out=output_dir(task);calls=[]
    def handler(req):
        calls.append(req.method)
        raise httpx.ReadTimeout('lost response',request=req)
    install_mock(monkeypatch,handler)
    with pytest.raises(RuntimeError,match='response lost'):provider.run(task,-2,out)
    with pytest.raises(RuntimeError,match='unknown outcome'):provider.run(task,-2,out)
    assert calls==['POST']


def test_auth_error_is_actionable_and_does_not_expose_body(project,monkeypatch):
    task=submit();out=output_dir(task)
    install_mock(monkeypatch,lambda req:httpx.Response(401,json={'echo':'fixture-api-key'}))
    with pytest.raises(RuntimeError,match='check API key') as error:provider.run(task,-2,out)
    assert 'fixture-api-key' not in str(error.value)


def test_resume_reuses_same_candidate_and_recipe(project):
    task=submit();out=output_dir(task)
    provider.save(out/'remote-task.json',{'request_id':'remote-001'})
    with core.db() as conn:conn.execute("UPDATE tasks SET status='failed' WHERE id=?",(task['id'],))
    result=core.resume_api(task['id'])
    assert not result['new_generation'] and core.tasks('api-film')[0]['id']==task['id']
    assert core.tasks('api-film')[0]['params']==task['params']


def test_seedance_high_resolution_validation_rejects_downgrade():
    p={'provider':'seedance-api','model':'doubao-seedance-2-0-260128','aspect_ratio':'16:9','resolution':'4k'}
    provider.validate_canvas(p,{'width':3840,'height':2160},'4k')
    with pytest.raises(RuntimeError,match='dimensions'):provider.validate_canvas(p,{'width':1920,'height':1080},'4k')
    assert catalog()['profiles']['seedance-2.5']['references']==[30,10,10]


def test_retry_after_profile_change_preserves_original_api_recipe(project):
    task=submit('h3-api',{'resolution':'768P'})
    with core.db() as conn:conn.execute("UPDATE tasks SET status='failed' WHERE id=?",(task['id'],))
    configure('api-film','seedance-2.5')
    new_id=core.retry_task(task['id'])
    p=next(t for t in core.tasks('api-film') if t['id']==new_id)['params']
    assert p['model']=='MiniMax-H3' and p['resolution']=='768P' and 'steps' not in p
    assert 'input_review' not in p and p['spec']['seed']==task['params']['spec']['seed']


@pytest.mark.parametrize('profile,container,audio_codec,pixel,codec', [
    ('seedance-2.5','mp4','aac','yuv420p10le','libx265'),
    ('seedance-2.5','mov','pcm_s16le','yuv444p','libx264'),
])
def test_export_preserves_real_source_codec_bit_depth_container_and_audio(project,profile,container,audio_codec,pixel,codec):
    import imageio_ffmpeg
    configure('api-film',profile,options={'output_format':container})
    with core.db() as conn:
        spec=json.loads(conn.execute("SELECT spec FROM shots WHERE project='api-film'").fetchone()[0]);spec['dialogue']='hello'
        conn.execute("UPDATE shots SET spec=? WHERE project='api-film'",(json.dumps(spec),))
    tid=core.submit('api-film','01','001','video');core.review_inputs(tid,'approve')
    source=core.project_dir('api-film')/('production/source.'+container)
    command=[imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i','color=c=blue:s=320x180:r=24:d=6',
             '-f','lavfi','-i','sine=sample_rate=48000:duration=6','-c:v',codec,'-preset','ultrafast','-threads','2',
             '-pix_fmt',pixel,'-c:a',audio_codec,'-ac','2']
    if codec=='libx265':command+=['-x265-params','pools=2:frame-threads=1']
    subprocess.run(command+[str(source)],check=True,capture_output=True)
    with core.db() as conn:conn.execute("UPDATE tasks SET status='review',output=? WHERE id=?",(str(source.relative_to(core.project_dir('api-film'))),tid))
    core.review(tid,'approve')
    result=export.export_episode('api-film','01')
    info=export.inspect(result['clean'])
    assert (info['width'],info['height'])==(320,180)  # actual source, not the project's legacy 1344x768 canvas
    assert info['pixel_format']==pixel and info['audio_sample_rate']==48000
    assert info['audio_codec']==audio_codec and Path(result['clean']).suffix=='.'+container
    assert info['video_codec']==('hevc' if codec=='libx265' else 'h264')
    preview_info=export.inspect(result['preview'])
    assert preview_info['pixel_format']==pixel and preview_info['audio_codec']==audio_codec
    assert Path(result['path'],'000.'+container).read_bytes()==source.read_bytes()
    timeline=json.loads(Path(result['path'],'timeline.json').read_text())['clips'][0]
    assert timeline['audio_source']=='native' and timeline['pixel_format']==pixel


def test_control_api_catalog_and_model_configuration(project):
    from fastapi.testclient import TestClient
    from engine.review.app import app
    client=TestClient(app)
    assert client.get('/api/video-models').json()['profiles']['h3-max-api']['resolutions']==['480P','768P']
    response=client.post('/api/projects/api-film/video-model',json={'profile':'seedance-2.5','resolution':'1080p','duration':30,'options':{'output_format':'mov'}})
    assert response.status_code==200 and response.json()['video']['output_format']=='mov'
    assert client.get('/api/health').json()['paid_api_enabled'] is True
    with core.db() as conn:assert conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]==0


def test_explicit_secret_file_is_private_and_never_auto_discovered(tmp_path,monkeypatch):
    monkeypatch.delenv('MINIMAX_API_KEY',raising=False);monkeypatch.delenv('MINIMAX_API_KEY_FILE',raising=False)
    path=tmp_path/'.minimaxH3apikey';path.write_text('fixture-private-key\n');path.chmod(0o600)
    assert provider.credential('MINIMAX_API_KEY') is None
    monkeypatch.setenv('MINIMAX_API_KEY_FILE',str(path))
    assert provider.credential('MINIMAX_API_KEY')=='fixture-private-key'
    path.chmod(0o644)
    with pytest.raises(ValueError,match='permissions'):provider.credential('MINIMAX_API_KEY')


def test_ark_async_protocol_and_transient_polling_error(project,monkeypatch):
    task=submit('seedance-2.5');out=output_dir(task);posts=[];gets=[]
    def handler(req):
        if req.method=='POST':
            payload=json.loads(req.content);posts.append(payload)
            assert req.url.path=='/api/v3/contents/generations/tasks'
            assert req.headers['authorization']=='Bearer fixture-ark-key'
            return httpx.Response(200,json={'id':'ark-001'})
        if req.url.host=='cdn.test':
            assert 'authorization' not in req.headers
            return httpx.Response(200,content=b'media-fixture')
        gets.append(req.url.path)
        if len(gets)==1:return httpx.Response(429,json={'error':{'code':'TooManyRequests'}})
        if len(gets)==2:return httpx.Response(200,json={'status':'running'})
        return httpx.Response(200,json={'status':'succeeded','content':{'video_url':'https://cdn.test/video.mp4'},'resolution':'1080p','usage':{'completion_tokens':100},'seed':19})
    install_mock(monkeypatch,handler)
    monkeypatch.setattr(provider.time,'sleep',lambda _:None)
    monkeypatch.setattr(export,'inspect',lambda _:{'width':1920,'height':1080,'fps':24,'duration':6,'audio':True,'audio_sample_rate':48000,'audio_channels':2,'video_codec':'hevc','pixel_format':'yuv420p10le'})
    dest,metrics=provider.run(task,-2,out)
    assert len(posts)==1 and len(gets)==3 and dest.read_bytes()==b'media-fixture'
    assert metrics['returned_seed']==19 and metrics['usage']['completion_tokens']==100
    assert metrics['pixel_format']=='yuv420p10le' and metrics['num_gpus']==0


def test_services_api_mode_never_discovers_gpus(project,monkeypatch):
    from scripts import services
    import sys
    (project/'logs').mkdir()
    monkeypatch.setattr(services,'ROOT',project)
    monkeypatch.setattr(services,'visible_devices',lambda: (_ for _ in ()).throw(AssertionError('CUDA discovery')))
    calls=[];monkeypatch.setattr(services,'start',lambda *args,**kw:calls.append(args))
    monkeypatch.setattr(sys,'argv',['services','start','--only','api','review'])
    services.main()
    assert {args[0] for args in calls}=={'api','review'}
