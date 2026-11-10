import argparse,json,sys
from pathlib import Path
from engine import core as c
def main():
    p=argparse.ArgumentParser(description='叙映工坊生产CLI');sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init');sub.add_parser('doctor');sub.add_parser('status')
    a=sub.add_parser('create');a.add_argument('project');a.add_argument('--name');a.add_argument('--template')
    a=sub.add_parser('migrate-h3');a.add_argument('project')
    a=sub.add_parser('inspect');a.add_argument('task')
    a=sub.add_parser('task');a.add_argument('task')
    for name in ['import','validate']:
        a=sub.add_parser(name);a.add_argument('project');a.add_argument('file');a.add_argument('--mapping')
    a=sub.add_parser('asset');a.add_argument('project');a.add_argument('id');a.add_argument('kind');a.add_argument('--file');a.add_argument('--meta',default='{}');a.add_argument('--shared',action='store_true')
    a=sub.add_parser('bind');a.add_argument('project');a.add_argument('episode');a.add_argument('shot');a.add_argument('references',nargs='*');a.add_argument('--prompt');a.add_argument('--field',choices=['reference_images','reference_videos','reference_audio','style_reference_images','sound_reference'],default='reference_images')
    a=sub.add_parser('resume');a.add_argument('--gpu',type=int,required=True)
    for name in ['submit','produce']:
        a=sub.add_parser(name);a.add_argument('project');a.add_argument('episode');a.add_argument('--shot');a.add_argument('--kind',choices=sorted(c.KINDS),default='image');a.add_argument('--params',default='{}')
    for name in ['queue','shots','sheet','export']:
        a=sub.add_parser(name);a.add_argument('project');a.add_argument('--episode')
        if name=='export':a.add_argument('--draft',action='store_true')
    a=sub.add_parser('review');a.add_argument('task');a.add_argument('decision',choices=['approve','reject']);a.add_argument('--reason',default='')
    a=sub.add_parser('review-inputs');a.add_argument('task');a.add_argument('decision',choices=['approve','reject']);a.add_argument('--reason',default='')
    a=sub.add_parser('retry');a.add_argument('task');a.add_argument('--params',default='{}')
    a=sub.add_parser('log');a.add_argument('task');a.add_argument('--tail',type=int,default=30)
    a=p.parse_args();c.init()
    try:
        if a.cmd=='init':result={'root':str(c.ROOT),'database':'ready'}
        elif a.cmd=='status':
            with c.db() as conn:
                result={'root':str(c.ROOT),'tasks':[dict(r) for r in conn.execute('SELECT project,kind,status,COUNT(*) AS count FROM tasks GROUP BY project,kind,status')],'last_event':dict(conn.execute('SELECT * FROM events ORDER BY id DESC LIMIT 1').fetchone() or {})}
        elif a.cmd=='doctor':
            import subprocess
            return subprocess.call([sys.executable,str(c.ROOT/'scripts/doctor.py')])
        elif a.cmd=='create':result=c.create_project(a.project,a.name,a.template)
        elif a.cmd=='migrate-h3':result=c.migrate_h3(a.project)
        elif a.cmd=='task':
            with c.db() as conn:row=conn.execute('SELECT * FROM tasks WHERE id=?',(a.task,)).fetchone()
            if not row:raise ValueError('任务不存在')
            result={**dict(row),'params':json.loads(row['params']),'metrics':json.loads(row['metrics'] or '{}')}
        elif a.cmd=='inspect':
            with c.db() as conn:task=conn.execute('SELECT * FROM tasks WHERE id=?',(a.task,)).fetchone()
            if not task or not task['output']:raise ValueError('任务尚无媒体输出')
            from engine.export import inspect
            result=inspect(c.safe_file(task['project'],task['output']))
        elif a.cmd in ['import','validate']:result=c.import_script(a.project,a.file,json.loads(Path(a.mapping).read_text()) if a.mapping else None,a.cmd=='validate')
        elif a.cmd=='asset':result=c.register_asset(a.project,a.id,a.kind,a.file,json.loads(a.meta),a.shared)
        elif a.cmd=='bind':result=c.bind_references(a.project,a.episode,a.shot,a.references,a.prompt,a.field)
        elif a.cmd=='resume':
            marker=c.ROOT/'runtime'/f'gpu-{a.gpu}.paused.json'
            if marker.exists():marker.unlink()
            result={'gpu':a.gpu,'queue':'resumed'}
        elif a.cmd in ['submit','produce']:
            result=[]
            for s in c.list_shots(a.project,a.episode):
                if a.shot and a.shot!=s['id']:continue
                kinds=[a.kind] if a.cmd=='submit' else ['video']
                for kind in kinds:result.append({'shot':s['id'],'kind':kind,'task':c.submit(a.project,a.episode,s['id'],kind,json.loads(a.params))})
            if not result:raise ValueError('未匹配到镜头')
        elif a.cmd=='queue':
            result=[{k:t.get(k) for k in ['id','project','episode','shot','kind','status','gpu','attempt','output','reason','created','started','finished']} for t in c.tasks(a.project,a.episode)]
        elif a.cmd=='shots':result=c.list_shots(a.project,a.episode)
        elif a.cmd=='review':result=c.review(a.task,a.decision,a.reason)
        elif a.cmd=='review-inputs':result=c.review_inputs(a.task,a.decision,a.reason)
        elif a.cmd=='retry':
            result=c.retry_task(a.task,json.loads(a.params))
        elif a.cmd=='log':
            with c.db() as conn:rows=conn.execute('SELECT * FROM events WHERE task=? ORDER BY id DESC LIMIT ?',(a.task,a.tail)).fetchall()
            result=[dict(r) for r in rows]
        elif a.cmd in ['sheet','export']:
            if not a.episode:raise ValueError('需要 --episode')
            from engine.export import contact_sheet,export_episode
            result=contact_sheet(a.project,a.episode) if a.cmd=='sheet' else export_episode(a.project,a.episode,a.draft)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except Exception as e:print(json.dumps({'error':str(e)},ensure_ascii=False),file=sys.stderr);return 1
if __name__=='__main__':raise SystemExit(main())
