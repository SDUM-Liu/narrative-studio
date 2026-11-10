"""Durable H3 allocation worker (-1), or an optional single-GPU asset worker."""
import argparse,json,os,signal,subprocess,time,fcntl,threading
from pathlib import Path
from engine.core import ROOT,db,dumps,event,get_shot,safe_file,project_dir,inputs_current
from engine.providers import comfy
from engine.providers import resolve,default_provider

def gpu_stats(gpu):
    device=os.environ.get('NARRATIVE_GPU_DEVICE',str(gpu))
    r=subprocess.run(['nvidia-smi','--query-gpu=memory.free,memory.used','--format=csv,noheader,nounits',f'--id={device}'],capture_output=True,text=True,check=True,timeout=10)
    return [int(x.strip()) for x in r.stdout.strip().split(',')]
def ready(task,shot,c):
    p=json.loads(task['params'])
    if task['kind']=='video':
        try:
            if not inputs_current(p,shot,task['project']):return None
        except (OSError,ValueError):return None
    name=p.get('provider',default_provider(task['kind']))
    required=resolve(name,task['kind']).required_files(p)
    if not all(x.is_file() for x in required):return None
    return p
def claim(gpu,kinds,project=None,episode=None):
    if (ROOT/'runtime'/f'gpu-{gpu}.paused.json').exists():return None
    free,_=gpu_stats(gpu) if gpu>=0 else (0,0)
    import httpx
    comfy_busy=False
    comfy_ready=False
    if gpu>=0:
        try:
            comfy_ready=httpx.get(comfy.endpoint(gpu)+'/system_stats',trust_env=False,timeout=3).status_code==200
            if comfy_ready:
                q=httpx.get(comfy.endpoint(gpu)+'/queue',trust_env=False,timeout=3).json();comfy_busy=bool(q.get('queue_running') or q.get('queue_pending'))
        except Exception:comfy_ready=False
    if comfy_busy:return None
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        for row in c.execute("SELECT * FROM tasks WHERE status='pending' ORDER BY CASE kind WHEN 'video' THEN 0 WHEN 'tts' THEN 1 ELSE 2 END,created"):
            t=dict(row)
            if project and t['project']!=project:continue
            if episode and t['episode']!=episode:continue
            if t['kind'] not in kinds:continue
            provider=json.loads(t['params']).get('provider',default_provider(t['kind']))
            if (provider=='minimax-h3') != (gpu==-1):continue
            if provider=='comfy' and not comfy_ready:continue
            shot=get_shot(c,t['project'],t['episode'],t['shot']);p=ready(t,shot,c)
            if p is None:continue
            if p.get('gpu') is not None and p['gpu']!=gpu:continue
            if provider=='minimax-h3':
                devices=json.loads(os.environ.get('NARRATIVE_H3_DEVICES','[]'))
                if len(devices)!=p.get('num_gpus',2):continue
                stats=[]
                for device in devices:
                    result=subprocess.run(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits',f'--id={device}'],capture_output=True,text=True,check=True,timeout=10)
                    stats.append(int(result.stdout.strip()))
                free=min(stats)
            need=p.get('min_free_mib',{'image':60000,'edit':60000,'tts':6500}.get(t['kind'],70000))
            if free<need:continue
            p['free_mib_at_start']=free
            c.execute("UPDATE tasks SET status='running',gpu=?,started=?,params=? WHERE id=?",(gpu,time.time(),dumps(p),t['id']))
            event(c,t['project'],t['id'],'started',f'allocation lane {gpu}, minimum free {free} MiB')
            return {**t,'params':p,'gpu':gpu}
    return None
def perform(t,gpu):
    out=project_dir(t['project'])/'production'/t['episode']/t['shot']/t['id'];out.mkdir(parents=True,exist_ok=False)
    (out/'task.json').write_text(dumps(t))
    metrics={};start=time.monotonic();samples=[];process_peaks=[];stop=threading.Event()
    services_path=ROOT/'runtime/services.json'
    services=json.loads(services_path.read_text()) if services_path.exists() else {}
    comfy_pid=services.get(f'comfy-{gpu}',{}).get('pid')
    def monitor():
        while not stop.wait(2):
            try:
                samples.append(gpu_stats(gpu))
                if comfy_pid and t['kind']!='tts':
                    result=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10,check=True)
                    for line in result.stdout.splitlines():
                        parts=[v.strip() for v in line.split(',')]
                        if len(parts)==2 and parts[0]==str(comfy_pid) and parts[1].isdigit():process_peaks.append(int(parts[1]))
            except Exception:pass
    thread=threading.Thread(target=monitor,daemon=True)
    if gpu>=0:thread.start()
    try:
        name=t['params'].get('provider',default_provider(t['kind']))
        output,metrics=resolve(name,t['kind']).run(t,gpu,out)
        metrics.update(wall_seconds=round(time.monotonic()-start,3),physical_gpu=gpu if gpu>=0 else None)
        if samples:
            metrics['host_gpu_used_peak_mib']=max(s[1] for s in samples)
            metrics['host_gpu_free_min_mib']=min(s[0] for s in samples)
        if process_peaks:metrics['process_gpu_peak_sampled_mib']=max(process_peaks)
        # This metric includes other users; never present it as this job's allocation.
        (out/'metrics.json').write_text(dumps(metrics))
        with db() as c:
            c.execute("UPDATE tasks SET status='review',output=?,metrics=?,finished=? WHERE id=?",(str(output.relative_to(project_dir(t['project']))),dumps(metrics),time.time(),t['id']))
            state={'image':'首帧待审','edit':'首帧待审','video':'视频待审'}.get(t['kind'])
            if state:c.execute('UPDATE shots SET status=? WHERE project=? AND episode=? AND id=?',(state,t['project'],t['episode'],t['shot']))
            event(c,t['project'],t['id'],'completed',str(output.name))
    except Exception as e:
        import traceback
        error=traceback.format_exc();(out/'failure.log').write_text(error)
        failed_metrics={'wall_seconds':round(time.monotonic()-start,3),'physical_gpu':gpu}
        prompt_file=out/'comfy-prompt.json'
        if prompt_file.exists():
            prompt=json.loads(prompt_file.read_text()).get('prompt_id');peak=ROOT/'runtime'/f'metrics-{gpu}'/(str(prompt)+'.json')
            if peak.exists():failed_metrics.update(json.loads(peak.read_text()))
        if process_peaks:failed_metrics['process_gpu_peak_sampled_mib']=max(process_peaks)
        if samples:failed_metrics['host_gpu_free_min_mib']=min(s[0] for s in samples)
        if gpu==-1:
            # Local child failures are often reported only through their device log.
            log=out/'h3-inference.log'
            tail=log.read_text(errors='replace')[-16000:] if log.exists() else ''
            if any(marker in tail.lower() for marker in ['out of memory','outofmemoryerror','cuda error']):
                (ROOT/'runtime'/f'gpu-{gpu}.paused.json').write_text(dumps({'task':t['id'],'reason':'H3 GPU failure; inspect h3-inference.log and resource limits before resume','time':time.time()}))
        (out/'metrics.json').write_text(dumps(failed_metrics))
        with db() as c:
            c.execute("UPDATE tasks SET status='failed',reason=?,finished=?,metrics=? WHERE id=?",(str(e),time.time(),dumps(failed_metrics),t['id']))
            c.execute("UPDATE shots SET status='失败' WHERE project=? AND episode=? AND id=?",(t['project'],t['episode'],t['shot']))
            event(c,t['project'],t['id'],'failed',str(e))
        if any(marker in str(e).lower() for marker in ['out of memory','allocation on device','shapes cannot be multiplied','conditioning dimension mismatch']):
            (ROOT/'runtime'/f'gpu-{gpu}.paused.json').write_text(dumps({'task':t['id'],'time':time.time(),'reason':str(e),'action':'先修复配置，再通过studio resume显式恢复；避免同类系统故障连续消耗队列'}))
    finally:
        stop.set()
        if thread.is_alive():thread.join(timeout=3)
        note={'time':time.time(),'project':t['project'],'task':t['id']}
        with (ROOT/'runtime/notifications.jsonl').open('a') as f:f.write(dumps(note)+'\n')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gpu',type=int,required=True);parser.add_argument('--kinds',default='image,edit,tts')
    parser.add_argument('--project');parser.add_argument('--episode');parser.add_argument('--exit-when-idle',action='store_true')
    a=parser.parse_args()
    if a.exit_when_idle and not (a.project and a.episode):parser.error('Finite production requires --project and --episode')
    (ROOT/'runtime').mkdir(exist_ok=True)
    lock=(ROOT/'runtime'/f'gpu-{a.gpu}.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    def terminate(signum,frame):raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,terminate)
    with db() as c:
        # A newly locked lane owns its old running records. Never overwrite their files.
        for r in c.execute("SELECT * FROM tasks WHERE status='running' AND gpu=?",(a.gpu,)).fetchall():
            c.execute("UPDATE tasks SET status='failed',reason='worker重启：保留中断版本；请retry创建新版本',finished=? WHERE id=?",(time.time(),r['id']))
            event(c,r['project'],r['id'],'interrupted','Worker restarted')
    print(f'worker gpu={a.gpu} started',flush=True)
    while True:
        try:
            t=claim(a.gpu,a.kinds.split(','),a.project,a.episode)
            if t:
                perform(t,a.gpu)
                if a.exit_when_idle:
                    with db() as conn:status=conn.execute('SELECT status FROM tasks WHERE id=?',(t['id'],)).fetchone()[0]
                    if status!='review':raise SystemExit('Episode stopped after failed task '+t['id']+'; no automatic downgrade or retry')
            elif a.exit_when_idle:
                with db() as conn:
                    blocked=conn.execute("""SELECT COUNT(*) FROM tasks t WHERE project=? AND episode=? AND kind='video'
                      AND (status IN ('pending','running') OR (status='failed' AND NOT EXISTS(
                        SELECT 1 FROM tasks newer WHERE newer.project=t.project AND newer.episode=t.episode
                        AND newer.shot=t.shot AND newer.kind=t.kind AND newer.created>t.created)))""",(a.project,a.episode)).fetchone()[0]
                if blocked:raise SystemExit('Episode has blocked/failed tasks; inspect inputs, weights, GPU allocation and logs')
                return
            else:time.sleep(20)
        except Exception as e:
            print(type(e).__name__,str(e),flush=True)
            if a.exit_when_idle:raise
            time.sleep(30)
if __name__=='__main__':main()
