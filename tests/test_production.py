"""State/validation tests only. Synthetic fixtures never count as inference acceptance."""
import importlib,json,shutil
import pytest,yaml
from engine import core as c
from engine.scheduler.worker import ready
@pytest.fixture
def studio(tmp_path,monkeypatch):
    root=tmp_path/'studio';(root/'configs').mkdir(parents=True);(root/'projects').mkdir()
    shutil.copy(c.ROOT/'configs/project.template.yaml',root/'configs/project.template.yaml')
    monkeypatch.setattr(c,'ROOT',root);c.init()
    for pid in ['one','two']:
        cfg=c.create_project(pid);cfg['characters']={'actor':{}};cfg['voices']={'actor':{'speaker':'Dylan'}}
        (c.project_dir(pid)/'project.yaml').write_text(yaml.safe_dump(cfg))
    return root
def script(studio,rows,pid='one'):
    p=studio/'input.json';p.write_text(json.dumps(rows));return c.import_script(pid,p)
def shot(sid='001',**kw):return {'episode':'01','shot':sid,'duration':6,'positive':'test scene','characters':['actor'],**kw}
def complete(tid,pid='one'):
    with c.db() as conn:conn.execute("UPDATE tasks SET status='review',output='production/test.png' WHERE id=?",(tid,))
def test_import_atomic_and_original(studio):
    with pytest.raises(ValueError,match='row'):script(studio,[shot(),shot('002',dialogue='hello',speaker='missing')])
    assert c.list_shots('one')==[]
    script(studio,[shot()]);s=c.list_shots('one')[0]['spec'];assert s['original']['positive']=='test scene';assert s['prompt']=='test scene'
    with pytest.raises(ValueError,match='重复'):script(studio,[shot('002'),shot('002')])
def test_timecode_and_cross_project(studio):
    with pytest.raises(ValueError,match='不一致'):script(studio,[shot(start='00:00:10',end='00:00:13')])
    with pytest.raises(ValueError,match='project_id'):script(studio,[shot(project_id='two')])
    with pytest.raises(ValueError,match='跨项目'):c.safe_file('one','../two/project.yaml')
def test_review_gate_and_rejection(studio):
    script(studio,[shot()]);vid=c.submit('one','01','001','video')
    with c.db() as conn:
        v=dict(conn.execute('SELECT * FROM tasks WHERE id=?',(vid,)).fetchone());s=c.get_shot(conn,'one','01','001');assert ready(v,s,conn) is None
    image=c.submit('one','01','001','image');complete(image)
    with pytest.raises(ValueError,match='原因'):c.review(image,'reject','')
    c.review(image,'reject','wrong face')
    with c.db() as conn:assert c.get_shot(conn,'one','01','001')['selected_image'] is None
    image2=c.submit('one','01','001','image',retry_of=image);complete(image2);c.review(image2,'approve')
    with c.db() as conn:
        s=c.get_shot(conn,'one','01','001');assert s['selected_image']==image2
        v['params']=json.dumps({'spec':{},'width':768,'height':432})
        assert ready(v,s,conn)['image_task_id']==image2
def test_restart_versions_isolation(studio):
    for pid in ['one','two']:script(studio,[shot()],pid)
    a=c.submit('one','01','001','image');complete(a);c.review(a,'approve');c.init()
    assert c.list_shots('one')[0]['selected_image']==a;assert c.list_shots('two')[0]['selected_image'] is None
    b=c.submit('one','01','001','image',retry_of=a)
    assert a!=b;assert c.list_shots('one')[0]['selected_image']==a
    with c.db() as conn:assert conn.execute('SELECT status FROM tasks WHERE id=?',(a,)).fetchone()[0]=='approved'
def test_stale_video_cannot_pass(studio):
    script(studio,[shot()]);a=c.submit('one','01','001','image');complete(a);c.review(a,'approve');v=c.submit('one','01','001','video');complete(v)
    with c.db() as conn:
        p=json.loads(conn.execute('SELECT params FROM tasks WHERE id=?',(v,)).fetchone()[0]);p['image_task_id']='old-image';conn.execute('UPDATE tasks SET params=? WHERE id=?',(json.dumps(p),v))
    with pytest.raises(ValueError,match='首帧已改变'):c.review(v,'approve')
def test_no_silent_overwrite_and_budget(studio):
    script(studio,[shot()]);a=c.submit('one','01','001','image')
    with pytest.raises(ValueError,match='同类'):c.submit('one','01','001','image')
    with pytest.raises(ValueError,match='已存在'):script(studio,[shot()])
    with pytest.raises(ValueError,match='分辨率'):c.submit('one','01','001','video',{'width':99999})
    with pytest.raises(ValueError,match='帧数'):c.submit('one','01','001','video',{'frames':100})
def test_media_and_cross_origin(studio):
    from fastapi.testclient import TestClient
    from engine.review.app import app
    client=TestClient(app)
    assert client.get('/api/health').status_code==200
    assert client.get('/media/one/project.yaml').status_code==404
    assert client.post('/api/projects',json={'id':'bad','name':'bad'},headers={'Origin':'https://external.example'}).status_code==403
    assert client.get('/',headers={'Host':'external.example'}).status_code==403
    assert client.get('/').status_code==200
def test_name_alias_stage_and_voice_registration(studio):
    cfg=c.config('one');cfg['characters']['actor']={'name':'演员','stages':{'early':'初期'}}
    (c.project_dir('one')/'project.yaml').write_text(yaml.safe_dump(cfg,allow_unicode=True))
    with pytest.raises(ValueError,match='成长阶段'):script(studio,[shot(character_stage='missing')])
    script(studio,[shot(characters=['演员'],speaker='演员',dialogue='你好',character_stage='early')])
    assert c.list_shots('one')[0]['spec']['speaker']=='actor'
    c.register_asset('one','same','character',meta={'name':'same'})
    c.register_asset('one','same','voice',meta={'speaker':'Dylan'})
    assert 'same' in c.config('one')['characters'] and 'same' in c.config('one')['voices']
def test_systemic_failure_pauses_lane(studio,monkeypatch):
    import engine.scheduler.worker as worker
    monkeypatch.setattr(worker,'ROOT',studio)
    (studio/'runtime/services.json').write_text('{}')
    script(studio,[shot()]);tid=c.submit('one','01','001','image')
    task=next(t for t in c.tasks('one') if t['id']==tid)
    def fail(*args):raise RuntimeError('Allocation on device: test fault without GPU allocation')
    monkeypatch.setattr(worker.comfy,'run',fail)
    worker.perform(task,0)
    assert (studio/'runtime/gpu-0.paused.json').exists()
    assert c.tasks('one')[0]['status']=='failed'
    assert worker.claim(0,['image']) is None
def test_task_prompt_override_keeps_script(studio):
    script(studio,[shot(motion_prompt='original action',seed=1)])
    tid=c.submit('one','01','001','video',{'action_prompt':'revised action','seed':42})
    task=next(t for t in c.tasks('one') if t['id']==tid)
    assert task['params']['spec']['action_prompt']=='revised action'
    assert task['params']['spec']['seed']==42
    assert task['params']['task_overrides']['action_prompt']['before']=='original action'
    assert c.list_shots('one')[0]['spec']['action_prompt']=='original action'
def test_unregistered_provider_does_not_fall_back(studio):
    script(studio,[shot()])
    with pytest.raises(ValueError,match='未注册'):c.submit('one','01','001','video',{'provider':'unconfigured-cloud-api'})
def test_blocked_videos_do_not_starve_first_frames(studio,monkeypatch):
    import engine.scheduler.worker as worker
    import httpx
    monkeypatch.setattr(worker,'ROOT',studio)
    monkeypatch.setattr(worker,'gpu_stats',lambda gpu:[50000,0])
    monkeypatch.setattr(worker.comfy,'required_files',lambda params:[])
    class Reply:
        status_code=200
        def json(self):return {'queue_running':[],'queue_pending':[]}
    monkeypatch.setattr(httpx,'get',lambda *args,**kwargs:Reply())
    cfg=c.config('one');cfg['budget']['max_jobs']=1000;(c.project_dir('one')/'project.yaml').write_text(yaml.safe_dump(cfg))
    script(studio,[shot(str(i)) for i in range(201)])
    for i in range(201):c.submit('one','01',str(i),'video')
    image=c.submit('one','01','0','image')
    claimed=worker.claim(0,['video','image'])
    assert claimed and claimed['id']==image
def test_retry_uses_current_recipe_and_new_seed(studio):
    script(studio,[shot(seed=1)]);a=c.submit('one','01','001','video',{'text_encoder':'obsolete.pth'})
    with c.db() as conn:conn.execute("UPDATE tasks SET status='failed' WHERE id=?",(a,))
    cfg=c.config('one');cfg['models']['video']['text_encoder']='verified.safetensors';(c.project_dir('one')/'project.yaml').write_text(yaml.safe_dump(cfg))
    b=c.retry_task(a)
    rows={t['id']:t for t in c.tasks('one')}
    assert rows[b]['params']['text_encoder']=='verified.safetensors'
    assert rows[b]['params']['spec']['seed']==2
    assert rows[a]['params']['text_encoder']=='obsolete.pth'
    assert c.list_shots('one')[0]['spec']['seed']==1
