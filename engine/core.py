"""Project-scoped production data and durable review/queue state."""
from __future__ import annotations
import csv, json, os, re, shutil, sqlite3, time, uuid
from pathlib import Path
from contextlib import contextmanager
import yaml

ROOT=Path(os.environ.get('NARRATIVE_ROOT',Path(__file__).resolve().parents[1])).resolve()
ID=re.compile(r'^[A-Za-z0-9_-]{1,80}$')
KINDS={'image','edit','video','tts'}
def ident(value):
    if not isinstance(value,str) or not ID.fullmatch(value): raise ValueError(f'非法ID: {value!r}')
    return value
def project_dir(pid): return ROOT/'projects'/ident(pid)
def config(pid):
    p=project_dir(pid)/'project.yaml'
    if not p.is_file(): raise ValueError('项目不存在: '+pid)
    return yaml.safe_load(p.read_text())
def safe_file(pid,rel):
    p=(project_dir(pid)/str(rel)).resolve()
    if not p.is_relative_to(project_dir(pid).resolve()): raise ValueError('拒绝跨项目文件路径')
    return p
def dumps(x): return json.dumps(x,ensure_ascii=False)
@contextmanager
def db():
    (ROOT/'runtime').mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(ROOT/'runtime/studio.sqlite3',timeout=30)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA journal_mode=WAL')
    try: yield c; c.commit()
    except Exception: c.rollback(); raise
    finally:c.close()
def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS shots(
          project TEXT,episode TEXT,id TEXT,spec TEXT,status TEXT DEFAULT '待生成',
          selected_image TEXT,selected_video TEXT,selected_tts TEXT,
          PRIMARY KEY(project,episode,id));
        CREATE TABLE IF NOT EXISTS tasks(
          id TEXT PRIMARY KEY,project TEXT,episode TEXT,shot TEXT,kind TEXT,
          status TEXT,params TEXT,output TEXT,metrics TEXT,reason TEXT DEFAULT '',
          gpu INTEGER,attempt INTEGER DEFAULT 0,created REAL,started REAL,finished REAL,
          FOREIGN KEY(project,episode,shot) REFERENCES shots(project,episode,id));
        CREATE INDEX IF NOT EXISTS queue ON tasks(status,created);
        CREATE INDEX IF NOT EXISTS tasks_by_shot ON tasks(project,episode,shot,created);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
          time REAL,project TEXT,task TEXT,event TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS assets(project TEXT,id TEXT,kind TEXT,path TEXT,
          meta TEXT,PRIMARY KEY(project,id));
        ''')
def event(c,pid,tid,name,detail=''):
    c.execute('INSERT INTO events(time,project,task,event,detail) VALUES(?,?,?,?,?)',(time.time(),pid,tid,name,detail))
def create_project(pid,name=None,template=None):
    ident(pid);dest=project_dir(pid)
    if dest.exists():raise ValueError('项目目录已存在，未覆盖')
    cfg=yaml.safe_load(Path(template or ROOT/'configs/project.template.yaml').read_text())
    cfg['id']=pid;cfg['name']=name or pid
    for d in ['story','assets/characters','assets/scenes','assets/voices','production','exports']:(dest/d).mkdir(parents=True,exist_ok=True)
    (dest/'project.yaml').write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False))
    return cfg
def aslist(v):
    if isinstance(v,list):return v
    if not v:return []
    if isinstance(v,str) and v.strip().startswith('['):return json.loads(v)
    return [x.strip() for x in str(v).split(';') if x.strip()]
def seconds(v):
    if isinstance(v,(int,float)):return float(v)
    parts=str(v).strip().split(':');total=0.
    for x in parts:total=total*60+float(x)
    return total
ALIASES={'episode':'episode_id','shot':'shot_id','duration':'target_duration','positive':'prompt','motion_prompt':'action_prompt','negative':'negative_prompt'}
def import_script(pid,path,mapping=None,validate_only=False):
    cfg=config(pid);p=Path(path)
    rows=json.loads(p.read_text(encoding='utf-8-sig')) if p.suffix.lower()=='.json' else list(csv.DictReader(p.open(encoding='utf-8-sig',newline='')))
    if isinstance(rows,dict): rows=rows.get('shots',[])
    if not isinstance(rows,list) or not rows:raise ValueError('剧本必须包含非空镜头数组')
    mapping={**ALIASES,**(mapping or {})};clean=[];errors=[];seen=set()
    chars=cfg.get('characters',{});voices=cfg.get('voices',{})
    character_aliases={info['name']:key for key,info in chars.items() if info.get('name')}
    for i,raw in enumerate(rows,1):
        try:
            if not isinstance(raw,dict):raise ValueError('镜头应为对象')
            s={mapping.get(k,k):v for k,v in raw.items()};s['original']=raw
            if s.get('project_id',pid)!=pid:raise ValueError('project_id与目标项目不一致')
            s.update(project_id=pid,episode_id=ident(str(s.get('episode_id','01'))),shot_id=ident(str(s.get('shot_id',''))))
            key=(s['episode_id'],s['shot_id'])
            if key in seen:raise ValueError('镜头ID重复')
            seen.add(key)
            for k in ['characters','reference_images','equipment']:s[k]=aslist(s.get(k))
            s['characters']=[character_aliases.get(ch,ch) for ch in s['characters']]
            for ch in s['characters']:
                if ch not in chars:raise ValueError('未注册人物: '+ch)
            for ref in s['reference_images']:
                if not safe_file(pid,ref).is_file():raise ValueError('参考图不存在: '+ref)
            inferred=seconds(s['end'])-seconds(s['start']) if s.get('start') is not None and s.get('end') is not None else cfg['defaults']['duration']
            s['target_duration']=seconds(s.get('target_duration') or inferred)
            if not 0<s['target_duration']<=cfg['budget'].get('max_shot_duration',30):raise ValueError('镜头时长超出项目允许范围')
            if s.get('start') is not None and s.get('end') is not None:
                start,end=seconds(s['start']),seconds(s['end'])
                if start<0 or end<=start or abs(end-start-s['target_duration'])>.15:raise ValueError('start/end与duration不一致')
            for k in ['scene','character_stage','prompt','negative_prompt','action_prompt','dialogue','speaker']:s.setdefault(k,'')
            if not s['action_prompt'] and s.get('action'):s['action_prompt']=s['action']
            s['speaker']=character_aliases.get(s['speaker'],s['speaker'])
            if not isinstance(s['dialogue'],str):raise ValueError('dialogue须为字符串；多段对白请逐镜或逐句导入，未静默丢弃')
            for ch in s['characters']:
                stages=chars[ch].get('stages',{})
                if s['character_stage'] and stages and s['character_stage'] not in stages:raise ValueError(f'人物{ch}无此成长阶段: {s["character_stage"]}')
            if s['dialogue']:
                if s['speaker'] not in voices:raise ValueError('对白角色未配置声线: '+str(s['speaker']))
            s['seed']=int(s.get('seed') or cfg['defaults']['seed'])
            if not 0<=s['seed']<2**63:raise ValueError('seed超出允许范围')
            s['effective_prompt']='，'.join(x for x in [cfg['style'].get('positive_prefix',''),s['prompt']] if x)
            s['effective_negative']='，'.join(x for x in [cfg['style'].get('negative',''),s['negative_prompt']] if x)
            if not s['prompt']:raise ValueError('prompt/positive不能为空')
            clean.append(s)
        except (ValueError,TypeError,KeyError) as e:errors.append({'row':i,'shot':raw.get('shot_id',raw.get('shot')) if isinstance(raw,dict) else None,'error':str(e)})
    with db() as c:
        for s in clean:
            if c.execute('SELECT 1 FROM shots WHERE project=? AND episode=? AND id=?',(pid,s['episode_id'],s['shot_id'])).fetchone():errors.append({'shot':s['shot_id'],'error':'镜头已存在；原剧本未覆盖'})
        if errors:raise ValueError(dumps(errors))
        if not validate_only:
            for s in clean:c.execute('INSERT INTO shots(project,episode,id,spec) VALUES(?,?,?,?)',(pid,s['episode_id'],s['shot_id'],dumps(s)))
            event(c,pid,None,'script_import',f'{len(clean)} shots')
    if not validate_only:
        target=project_dir(pid)/'story'/f'import-{int(time.time())}-{uuid.uuid4().hex[:6]}.json'
        target.write_text(dumps({'source_name':p.name,'shots':clean}))
    return {'valid':True,'shots':len(clean),'imported':not validate_only}
def list_shots(pid,episode=None):
    cfg=config(pid)
    with db() as c:
        rows=c.execute('''SELECT s.*,
          (SELECT output FROM tasks t WHERE t.id=s.selected_image) AS selected_image_output,
          (SELECT output FROM tasks t WHERE t.id=s.selected_video) AS selected_video_output,
          (SELECT output FROM tasks t WHERE t.project=s.project AND t.episode=s.episode AND t.shot=s.id AND t.kind IN ('image','edit') AND t.output IS NOT NULL ORDER BY t.created DESC LIMIT 1) AS preview_image_output
          FROM shots s WHERE s.project=?'''+(' AND s.episode=?' if episode else '')+' ORDER BY s.episode,s.id',(pid,episode) if episode else (pid,)).fetchall()
    return [{**dict(r),'spec':json.loads(r['spec']),'export_trim':cfg.get('editing',{}).get('trims',{}).get(r['episode'],{}).get(r['id'])} for r in rows]
def get_shot(c,pid,ep,sid):
    r=c.execute('SELECT * FROM shots WHERE project=? AND episode=? AND id=?',(pid,ep,sid)).fetchone()
    if not r:raise ValueError('镜头不存在')
    return dict(r)
def tasks(pid=None,ep=None,sid=None,limit=100):
    where=[];args=[]
    for k,v in [('project',pid),('episode',ep),('shot',sid)]:
        if v:where.append(k+'=?');args.append(v)
    with db() as c:rows=c.execute('SELECT * FROM tasks'+(' WHERE '+' AND '.join(where) if where else '')+' ORDER BY created DESC LIMIT ?',(*args,min(limit,500))).fetchall()
    return [{**dict(r),'params':json.loads(r['params']),'metrics':json.loads(r['metrics'] or '{}')} for r in rows]
def submit(pid,ep,sid,kind,overrides=None,retry_of=None):
    if kind not in KINDS:raise ValueError('未知任务类型')
    cfg=config(pid)
    with db() as c:
        c.execute('BEGIN IMMEDIATE');shot=get_shot(c,pid,ep,sid);spec=json.loads(shot['spec'])
        if c.execute("SELECT 1 FROM tasks WHERE project=? AND episode=? AND shot=? AND kind=? AND status IN ('pending','running')",(pid,ep,sid,kind)).fetchone():raise ValueError('已有同类排队/执行任务')
        params={**cfg['format'],**cfg['models'].get(kind,{}),**(overrides or {})}
        params['requested_overrides']=overrides or {}
        from engine.providers import resolve
        resolve(params.get('provider','qwen3-tts' if kind=='tts' else 'comfy'),kind)
        task_changes={}
        for key in ['prompt','negative_prompt','action_prompt','seed']:
            if key not in (overrides or {}):continue
            value=overrides[key]
            if key=='seed':
                value=int(value)
                if not 0<=value<2**63:raise ValueError('seed超出允许范围')
            elif not isinstance(value,str):raise ValueError(key+'必须是字符串')
            task_changes[key]={'before':spec.get(key),'after':value};spec[key]=value
            if key=='prompt':spec['effective_prompt']='，'.join(x for x in [cfg['style'].get('positive_prefix',''),value] if x)
            if key=='negative_prompt':spec['effective_negative']='，'.join(x for x in [cfg['style'].get('negative',''),value] if x)
        if task_changes:params['task_overrides']=task_changes
        if kind=='video':params['frames']=int(params.get('frames',round(spec['target_duration']*params['fps']/4)*4+1))
        if kind!='tts':
            for dimension in ['width','height']:
                n=params.get(dimension,0)
                if not isinstance(n,int) or not 128<=n<=2048 or n%16:raise ValueError('分辨率必须是128至2048之间的16倍数')
            if not 1<=params.get('steps',0)<=60:raise ValueError('steps必须在1至60之间')
        if kind=='video' and (params['frames']%4!=1 or not 9<=params['frames']<=int(cfg['budget']['max_shot_duration']*params['fps'])+1):raise ValueError('视频帧数须为4n+1且不超过项目时长预算')
        count=c.execute('SELECT COUNT(*) FROM tasks WHERE project=? AND episode=? AND shot=?',(pid,ep,sid)).fetchone()[0]
        if count>=cfg['budget']['max_candidates_per_shot']:raise ValueError('达到本镜头候选预算')
        if c.execute('SELECT COUNT(*) FROM tasks WHERE project=?',(pid,)).fetchone()[0]>=cfg['budget']['max_jobs']:raise ValueError('达到项目任务预算')
        if kind=='edit' and not spec['reference_images']:raise ValueError('编辑任务需要参考图')
        if kind=='tts' and not spec['dialogue']:raise ValueError('镜头没有对白')
        attempt=0
        if retry_of:
            old=c.execute('SELECT * FROM tasks WHERE id=?',(retry_of,)).fetchone()
            if not old or (old['project'],old['episode'],old['shot'],old['kind'])!=(pid,ep,sid,kind):raise ValueError('重试归属不一致')
            attempt=old['attempt']+1
            if attempt>cfg['review']['max_retries']:raise ValueError('达到重试上限，请调整项目预算后重试')
            params['retry_of']=retry_of
        tid=uuid.uuid4().hex
        params['spec']=spec;params['config_snapshot']=cfg
        c.execute('INSERT INTO tasks(id,project,episode,shot,kind,status,params,attempt,created) VALUES(?,?,?,?,?,?,?,?,?)',(tid,pid,ep,sid,kind,'pending',dumps(params),attempt,time.time()))
        if kind in ['image','edit']:c.execute("UPDATE shots SET status='待生成' WHERE project=? AND episode=? AND id=?",(pid,ep,sid))
        event(c,pid,tid,'submitted',kind)
    return tid
def retry_task(tid,overrides=None):
    with db() as conn:r=conn.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
    if not r or r['status'] in ['pending','running']:raise ValueError('任务不存在或仍在排队/执行')
    old=json.loads(r['params']);baseline={**old.get('config_snapshot',{}).get('format',{}),**old.get('config_snapshot',{}).get('models',{}).get(r['kind'],{})}
    # Keep explicit creative/sampling choices, use the current validated model
    # recipe, and make a genuinely new candidate instead of replaying its cache.
    keep=['prompt','negative_prompt','action_prompt','steps','cfg','lora','high_lora','low_lora','split_step','width','height','fps','frames','gpu']
    params={k:old[k] for k in keep if k in old and old[k]!=baseline.get(k)}
    for key,value in old.get('task_overrides',{}).items():
        if key!='seed':params[key]=value['after']
    params['seed']=(int(old['spec']['seed'])+1)%(2**63)
    params.update(overrides or {})
    return submit(r['project'],r['episode'],r['shot'],r['kind'],params,retry_of=tid)
def review(tid,decision,reason=''):
    if decision not in ['approve','reject']:raise ValueError('非法审核动作')
    if decision=='reject' and not reason.strip():raise ValueError('退回必须填写原因')
    with db() as c:
        c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
        if not r or r['status'] not in ['review','approved']:raise ValueError('候选未就绪，无法审核')
        shot=get_shot(c,r['project'],r['episode'],r['shot'])
        if r['kind']=='video' and decision=='approve':
            params=json.loads(r['params']);pin=params.get('image_task_id')
            if pin!=shot['selected_image']:raise ValueError('首帧已改变，旧视频不得成为新首帧的通过版本')
            trim=config(r['project']).get('editing',{}).get('trims',{}).get(r['episode'],{}).get(r['shot'])
            if trim:params['approved_edit_range']=trim;c.execute('UPDATE tasks SET params=? WHERE id=?',(dumps(params),tid))
        status='approved' if decision=='approve' else 'rejected'
        c.execute('UPDATE tasks SET status=?,reason=? WHERE id=?',(status,reason,tid))
        if decision=='approve':
            column={'image':'selected_image','edit':'selected_image','video':'selected_video','tts':'selected_tts'}[r['kind']]
            state={'image':'首帧通过','edit':'首帧通过','video':'通过','tts':shot['status']}[r['kind']]
            c.execute(f'UPDATE shots SET {column}=?,status=? WHERE project=? AND episode=? AND id=?',(tid,state,r['project'],r['episode'],r['shot']))
        else:
            column={'image':'selected_image','edit':'selected_image','video':'selected_video','tts':'selected_tts'}[r['kind']]
            c.execute(f"UPDATE shots SET {column}=CASE WHEN {column}=? THEN NULL ELSE {column} END,status='退回重做' WHERE project=? AND episode=? AND id=?",(tid,r['project'],r['episode'],r['shot']))
        event(c,r['project'],tid,decision,reason)
    return {'id':tid,'status':status}
def register_asset(pid,aid,kind,path=None,meta=None,shared=False):
    cfg=config(pid);ident(aid)
    if kind not in ['character','scene','voice','reference','equipment','costume','prop']:raise ValueError('非法资产类型')
    rel=None
    if path:
        src=Path(path).resolve()
        if not src.is_file():raise ValueError('素材不存在')
        if src.is_relative_to(ROOT/'projects') and not src.is_relative_to(project_dir(pid)) and not shared:raise ValueError('跨项目复用需要 --shared 显式选择')
        dest=project_dir(pid)/'assets'/f'{aid}-{uuid.uuid4().hex[:8]}{src.suffix}'
        shutil.copy2(src,dest);rel=str(dest.relative_to(project_dir(pid)))
    with db() as c:
        if c.execute('SELECT 1 FROM assets WHERE project=? AND (id=? OR id=?) AND kind=?',(pid,aid,kind+':'+aid,kind)).fetchone():raise ValueError('同类资产ID已存在')
        c.execute('INSERT INTO assets VALUES(?,?,?,?,?)',(pid,kind+':'+aid,kind,rel,dumps(meta or {})))
        event(c,pid,None,'asset_registered',aid)
    key={'character':'characters','scene':'scenes','voice':'voices'}.get(kind)
    if key:
        cfg.setdefault(key,{})[aid]={**(meta or {}),**({'reference':rel} if rel else {})}
        tmp=project_dir(pid)/'project.yaml.tmp';tmp.write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False));tmp.replace(project_dir(pid)/'project.yaml')
    return {'id':aid,'path':rel}
def bind_references(pid,ep,sid,refs,prompt=None):
    config(pid)
    if not 1<=len(refs)<=3:raise ValueError('参考图数量必须在1至3之间')
    for ref in refs:
        if not safe_file(pid,ref).is_file():raise ValueError('参考图不存在: '+ref)
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE');shot=get_shot(conn,pid,ep,sid)
        if conn.execute("SELECT 1 FROM tasks WHERE project=? AND episode=? AND shot=? AND status IN ('pending','running')",(pid,ep,sid)).fetchone():raise ValueError('镜头有执行或排队任务，绑定前请完成当前任务')
        spec=json.loads(shot['spec']);spec.setdefault('production_revisions',[]).append({'time':time.time(),'reference_images':spec['reference_images'],'effective_prompt':spec['effective_prompt']})
        spec['reference_images']=refs
        if prompt:spec['effective_prompt']=prompt
        conn.execute('UPDATE shots SET spec=? WHERE project=? AND episode=? AND id=?',(dumps(spec),pid,ep,sid));event(conn,pid,None,'references_bound',f'{ep}/{sid}: {dumps(refs)}')
    return {'shot':sid,'references':refs,'original_preserved':True}
