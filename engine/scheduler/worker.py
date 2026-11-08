"""One durable worker per physical GPU. Never kills foreign GPU jobs."""
import argparse,json,os,signal,subprocess,time,fcntl,threading
from pathlib import Path
from engine.core import ROOT,db,dumps,event,get_shot,safe_file,project_dir
from engine.providers import comfy
from engine.providers import resolve

def gpu_stats(gpu):
    r=subprocess.run(['nvidia-smi','--query-gpu=memory.free,memory.used','--format=csv,noheader,nounits',f'--id={gpu}'],capture_output=True,text=True,check=True,timeout=10)
    return [int(x.strip()) for x in r.stdout.strip().split(',')]
def ready(task,shot,c):
    p=json.loads(task['params'])
    if task['kind']=='video':
        image=c.execute("SELECT * FROM tasks WHERE id=? AND status='approved'",(shot['selected_image'],)).fetchone()
        active=c.execute("SELECT 1 FROM tasks WHERE project=? AND episode=? AND shot=? AND kind IN ('image','edit') AND status IN ('pending','running','review')",(task['project'],task['episode'],task['shot'])).fetchone()
        if not image or active:return None
        p['image_task_id']=image['id'];p['image_path']=image['output']
    name=p.get('provider','qwen3-tts' if task['kind']=='tts' else 'comfy')
    required=resolve(name,task['kind']).required_files(p)
    if not all(x.is_file() for x in required):return None
    return p
def claim(gpu,kinds):
    if (ROOT/'runtime'/f'gpu-{gpu}.paused.json').exists():return None
    free,_=gpu_stats(gpu)
    import httpx
    comfy_busy=False
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
            if t['kind'] not in kinds:continue
            provider=json.loads(t['params']).get('provider','qwen3-tts' if t['kind']=='tts' else 'comfy')
            if provider=='comfy' and not comfy_ready:continue
            shot=get_shot(c,t['project'],t['episode'],t['shot']);p=ready(t,shot,c)
            if p is None:continue
            if p.get('gpu') is not None and p['gpu']!=gpu:continue
            need=p.get('min_free_mib',{'image':12000,'edit':14000,'video':20500,'tts':6500}[t['kind']])
            if free<need:continue
            p['free_mib_at_start']=free
            c.execute("UPDATE tasks SET status='running',gpu=?,started=?,params=? WHERE id=?",(gpu,time.time(),dumps(p),t['id']))
            event(c,t['project'],t['id'],'started',f'GPU {gpu}, free {free} MiB')
            return {**t,'params':p}
    return None
def perform(t,gpu):
    out=project_dir(t['project'])/'production'/t['episode']/t['shot']/t['id'];out.mkdir(parents=True,exist_ok=False)
    (out/'task.json').write_text(dumps(t))
    metrics={};start=time.monotonic();samples=[];process_peaks=[];stop=threading.Event()
    services=json.loads((ROOT/'runtime/services.json').read_text())
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
    thread=threading.Thread(target=monitor,daemon=True);thread.start()
    try:
        name=t['params'].get('provider','qwen3-tts' if t['kind']=='tts' else 'comfy')
        output,metrics=resolve(name,t['kind']).run(t,gpu,out)
        metrics.update(wall_seconds=round(time.monotonic()-start,3),physical_gpu=gpu)
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
        (out/'metrics.json').write_text(dumps(failed_metrics))
        with db() as c:
            c.execute("UPDATE tasks SET status='failed',reason=?,finished=?,metrics=? WHERE id=?",(str(e),time.time(),dumps(failed_metrics),t['id']))
            c.execute("UPDATE shots SET status='失败' WHERE project=? AND episode=? AND id=?",(t['project'],t['episode'],t['shot']))
            event(c,t['project'],t['id'],'failed',str(e))
        if any(marker in str(e).lower() for marker in ['out of memory','allocation on device','shapes cannot be multiplied','conditioning dimension mismatch']):
            (ROOT/'runtime'/f'gpu-{gpu}.paused.json').write_text(dumps({'task':t['id'],'time':time.time(),'reason':str(e),'action':'先修复配置，再通过studio resume显式恢复；避免同类系统故障连续消耗队列'}))
    finally:
        stop.set();thread.join(timeout=3)
        note={'time':time.time(),'project':t['project'],'task':t['id']}
        with (ROOT/'runtime/notifications.jsonl').open('a') as f:f.write(dumps(note)+'\n')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--gpu',type=int,required=True);parser.add_argument('--kinds',default='image,edit,video,tts');a=parser.parse_args()
    (ROOT/'runtime').mkdir(exist_ok=True)
    lock=(ROOT/'runtime'/f'gpu-{a.gpu}.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with db() as c:
        # A newly locked lane owns its old running records. Never overwrite their files.
        for r in c.execute("SELECT * FROM tasks WHERE status='running' AND gpu=?",(a.gpu,)).fetchall():
            c.execute("UPDATE tasks SET status='failed',reason='worker重启：保留中断版本；请retry创建新版本',finished=? WHERE id=?",(time.time(),r['id']))
            event(c,r['project'],r['id'],'interrupted','Worker restarted')
    print(f'worker gpu={a.gpu} started',flush=True)
    while True:
        try:
            t=claim(a.gpu,a.kinds.split(','))
            if t:perform(t,a.gpu)
            else:time.sleep(20)
        except Exception as e:print(type(e).__name__,str(e),flush=True);time.sleep(30)
if __name__=='__main__':main()
