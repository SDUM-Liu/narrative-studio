import json,time
from pathlib import Path
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import FileResponse,JSONResponse
from pydantic import BaseModel,Field
from engine import core as c
app=FastAPI(title='叙映工坊 Narrative Studio',docs_url='/api/docs')
c.init()
@app.middleware('http')
async def local_access(request:Request,call_next):
    host=request.headers.get('host','').split(':')[0]
    if host not in ['127.0.0.1','localhost','testserver']:return JSONResponse({'error':'仅允许SSH回环访问'},status_code=403)
    origin=request.headers.get('origin')
    if request.method not in ['GET','HEAD','OPTIONS']:
        if origin and origin not in ['http://127.0.0.1:7868','http://localhost:7868']:return JSONResponse({'error':'跨站操作被拒绝'},status_code=403)
        if 'application/json' not in request.headers.get('content-type',''):return JSONResponse({'error':'需要JSON请求'},status_code=415)
    return await call_next(request)
@app.exception_handler(ValueError)
async def value_error(request,exc):return JSONResponse({'error':str(exc)},status_code=400)
@app.get('/')
def index():return FileResponse(Path(__file__).with_name('index.html'))
@app.get('/font/NotoSansCJKsc-Regular.otf')
def chinese_font():return FileResponse(c.ROOT/'shared-assets/fonts/NotoSansCJKsc-Regular.otf',media_type='font/otf')
@app.get('/api/health')
def health():
    states={}
    for name in ['h3-runtime','h3-models','bootstrap','models','tts']:
        p=c.ROOT/'runtime'/f'{name}.status';states[name]=p.read_text().strip() if p.exists() else '尚未完成'
    caps=c.ROOT/'docs/capabilities.json'
    from scripts.services import load,alive
    services={name:{'alive':alive(item),'pid':item['pid'],'host':item.get('host'),'job_id':item.get('job_id'),'gpu_device':item.get('gpu_device')} for name,item in load().items()}
    paused={p.name.split('-')[1].split('.')[0]:json.loads(p.read_text()) for p in (c.ROOT/'runtime').glob('gpu-*.paused.json')}
    return {'ok':True,'name':'叙映工坊','time':time.time(),'deployment':states,'services':services,'paused_gpus':paused,'capabilities':json.loads(caps.read_text()) if caps.exists() else {},'paid_api_enabled':False}
@app.get('/api/projects')
def projects():
    result=[]
    for p in sorted((c.ROOT/'projects').glob('*/project.yaml')):
        cfg=c.config(p.parent.name);result.append({k:cfg.get(k) for k in ['id','name','genre','language','format','episodes','target_duration']})
    return result
@app.get('/api/projects/{pid}/shots')
def shots(pid:str,episode:str|None=None):return c.list_shots(pid,episode)
@app.get('/api/projects/{pid}/tasks')
def tasks(pid:str,episode:str|None=None,shot:str|None=None):
    c.config(pid);rows=c.tasks(pid,episode,shot,500 if shot else 100)
    if shot:return rows
    fields=['id','project','episode','shot','kind','status','gpu','reason','output','attempt','created','started','finished']
    return [{key:t.get(key) for key in fields} for t in rows]
@app.get('/api/projects/{pid}/stats')
def task_stats(pid:str,episode:str|None=None):
    c.config(pid)
    with c.db() as conn:rows=conn.execute('SELECT status,COUNT(*) AS n FROM tasks WHERE project=?'+(' AND episode=?' if episode else '')+' GROUP BY status',(pid,episode) if episode else (pid,)).fetchall()
    return {r['status']:r['n'] for r in rows}
@app.get('/api/projects/{pid}/config')
def cfg(pid:str):return c.config(pid)
@app.get('/api/projects/{pid}/events')
def events(pid:str,task:str|None=None):
    c.config(pid)
    with c.db() as conn:r=conn.execute('SELECT * FROM events WHERE project=?'+(' AND task=?' if task else '')+' ORDER BY id DESC LIMIT 40',(pid,task) if task else (pid,)).fetchall()
    return [dict(x) for x in r]
@app.get('/media/{pid}/{path:path}')
def media(pid:str,path:str):
    c.config(pid);p=c.safe_file(pid,path)
    if not p.is_file() or p.suffix.lower() not in ['.png','.jpg','.jpeg','.webp','.mp4','.wav','.mp3','.srt','.csv']:raise HTTPException(404)
    return FileResponse(p)
class Review(BaseModel):
    decision:str
    reason:str=''
@app.post('/api/tasks/{tid}/review')
def review(tid:str,body:Review):return c.review(tid,body.decision,body.reason)
@app.post('/api/tasks/{tid}/review-inputs')
def review_inputs(tid:str,body:Review):return c.review_inputs(tid,body.decision,body.reason)
class Submit(BaseModel):
    episode:str
    shot:str
    kind:str
    params:dict=Field(default_factory=dict)
@app.post('/api/projects/{pid}/submit')
def submit(pid:str,body:Submit):return {'task':c.submit(pid,body.episode,body.shot,body.kind,body.params)}
class Retry(BaseModel):params:dict=Field(default_factory=dict)
@app.post('/api/tasks/{tid}/retry')
def retry(tid:str,body:Retry):
    return {'task':c.retry_task(tid,body.params)}
class NewProject(BaseModel):id:str;name:str
@app.post('/api/projects')
def create(body:NewProject):return c.create_project(body.id,body.name)
class Import(BaseModel):shots:list[dict];mapping:dict=Field(default_factory=dict)
@app.post('/api/projects/{pid}/import')
def import_script(pid:str,body:Import):
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w',suffix='.json',dir=c.ROOT/'runtime') as f:
        json.dump(body.shots,f,ensure_ascii=False);f.flush();return c.import_script(pid,f.name,body.mapping)
class Export(BaseModel):episode:str
@app.post('/api/projects/{pid}/export')
def export(pid:str,body:Export):
    from engine.export import export_episode
    return export_episode(pid,body.episode)
