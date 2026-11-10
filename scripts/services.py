"""Manage only this project's own processes; no sudo, no global service mutation."""
import argparse,json,os,signal,subprocess,sys,time,socket,fcntl
from contextlib import contextmanager
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'runtime/services.json'
HOST=socket.gethostname()
def alive(item):
 if item.get('host',HOST)!=HOST:
  heartbeat=ROOT/'runtime'/f"slurm-{item.get('job_id')}.heartbeat"
  return heartbeat.exists() and time.time()-heartbeat.stat().st_mtime<30
 try:
  parts=Path(f"/proc/{item['pid']}/stat").read_text().split()
  return parts[2]!='Z' and parts[21]==item['start_ticks']
 except (OSError,IndexError):return False
def load():return json.loads(STATE.read_text()) if STATE.exists() else {}
@contextmanager
def registry():
 (ROOT/'runtime').mkdir(exist_ok=True)
 with (ROOT/'runtime/services.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  state=load()
  yield state
  tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2));tmp.replace(STATE)
def start(name,args,env=None):
 with registry() as state:
  if name in state and alive(state[name]):return
  log=(ROOT/'logs'/f'{name}.log').open('a')
  threads=os.environ.get('OMP_NUM_THREADS','4')
  proc=subprocess.Popen(args,cwd=ROOT,env={**os.environ,'OMP_NUM_THREADS':threads,'MKL_NUM_THREADS':threads,'HF_HOME':str(ROOT/'models/hf-cache'),**(env or {})},stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
  state[name]={'pid':proc.pid,'start_ticks':Path(f'/proc/{proc.pid}/stat').read_text().split()[21],'argv':args,'started':time.time(),'host':HOST,'job_id':os.environ.get('SLURM_JOB_ID'),'gpu_device':(env or {}).get('NARRATIVE_GPU_DEVICE')}
  log.close()
def stop(names=None):
 state=load()
 for name,item in state.items():
  if names and name not in names:continue
  if item.get('host',HOST)!=HOST:continue
  if alive(item):
   os.killpg(item['pid'],signal.SIGTERM)
   deadline=time.monotonic()+12
   while alive(item) and time.monotonic()<deadline:time.sleep(.2)
def visible_devices():
 # CUDA ordinals and NVML indices differ under Slurm cgroups. Resolve UUIDs in
 # the unmodified allocation mask, then give each child exactly one UUID.
 code='import json,torch; print(json.dumps([str(torch.cuda.get_device_properties(i).uuid) for i in range(torch.cuda.device_count())]))'
 result=subprocess.run([str(ROOT/('.venv-h3' if (ROOT/'.venv-h3/bin/python').exists() else '.venv')/'bin/python'),'-c',code],capture_output=True,text=True,check=True)
 devices=json.loads(result.stdout)
 if not devices:raise RuntimeError('No allocated CUDA GPU is visible')
 return [device if device.startswith(('GPU-','MIG-')) else 'GPU-'+device for device in devices]
def supervise(names):
 ending=False
 def finish(*_):
  nonlocal ending
  ending=True
 signal.signal(signal.SIGTERM,finish);signal.signal(signal.SIGINT,finish)
 heartbeat=ROOT/'runtime'/f"slurm-{os.environ.get('SLURM_JOB_ID','local')}.heartbeat"
 try:
  while not ending:
   state=load()
   failed=[name for name in names if name not in state or not alive(state[name])]
   if failed:raise RuntimeError('Services exited: '+', '.join(failed))
   heartbeat.touch()
   time.sleep(5)
 finally:
  stop(names);heartbeat.unlink(missing_ok=True)
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['start','run','stop','status','restart']);p.add_argument('--only',nargs='*');p.add_argument('--gpus',type=int,nargs='+');p.add_argument('--lane-offset',type=int,default=0);a=p.parse_args()
 if a.action=='status':print(json.dumps({k:{**v,'alive':alive(v)} for k,v in load().items()},indent=2));return
 if a.action=='stop':stop(a.only);return
 if a.action=='restart':stop(a.only)
 (ROOT/'runtime').mkdir(exist_ok=True);(ROOT/'logs').mkdir(exist_ok=True)
 devices=[] if a.only==['review'] else visible_devices()
 if a.gpus is None:a.gpus=list(range(len(devices)))
 if any(gpu<0 or gpu>=len(devices) for gpu in a.gpus):p.error('--gpus must be ordinals within the allocated CUDA devices')
 compute=[f'{kind}-{gpu+a.lane_offset}' for gpu in a.gpus for kind in ['comfy','worker']]
 selected=compute if a.only==['compute'] else a.only or ['review','h3']
 if os.environ.get('SLURM_JOB_ID'):(ROOT/'runtime'/f"slurm-{os.environ['SLURM_JOB_ID']}.heartbeat").touch()
 # Generate absolute model paths at installation location; never ship host paths.
 import yaml
 (ROOT/'configs/model-paths.yaml').write_text(yaml.safe_dump({'narrative':{'base_path':str(ROOT/'models'),'diffusion_models':'diffusion_models','text_encoders':'text_encoders','vae':'vae','loras':'loras'}}))
 py=str(ROOT/'.venv-control/bin/python')
 if 'h3' in selected:
  h3_devices=[devices[i] for i in a.gpus]
  if len(h3_devices)<2:raise RuntimeError('H3 needs the complete GPU group (at least two cards)')
  start('h3',[py,'-m','engine.scheduler.worker','--gpu','-1','--kinds','video'],{'CUDA_VISIBLE_DEVICES':','.join(h3_devices),'NARRATIVE_H3_DEVICES':json.dumps(h3_devices)})
 if 'review' in selected:start('review',[py,'-m','uvicorn','engine.review.app:app','--host','127.0.0.1','--port','7868','--no-access-log'])
 for ordinal in a.gpus:
  gpu=ordinal+a.lane_offset
  name=f'comfy-{gpu}'
  if name in selected:
   for folder in ['input','output','user']:(ROOT/'runtime'/f'{name}-{folder}').mkdir(exist_ok=True)
   mode=os.environ.get('NARRATIVE_COMFY_VRAM','normal')
   if mode not in ['low','normal','high']:raise ValueError('NARRATIVE_COMFY_VRAM must be low, normal or high')
   args=[str(ROOT/'.venv/bin/python'),str(ROOT/'vendor/ComfyUI/main.py'),'--listen','127.0.0.1','--port',str(8190+gpu),f'--{mode}vram','--reserve-vram',os.environ.get('NARRATIVE_RESERVE_VRAM','2'),'--preview-method','none','--offline','--disable-assets','--disable-all-custom-nodes','--extra-model-paths-config',str(ROOT/'configs/model-paths.yaml'),'--input-directory',str(ROOT/'runtime'/f'{name}-input'),'--output-directory',str(ROOT/'runtime'/f'{name}-output'),'--user-directory',str(ROOT/'runtime'/f'{name}-user')]
   extension=ROOT/'vendor/ComfyUI/custom_nodes/narrative_metrics'
   extension.mkdir(exist_ok=True)
   link=extension/'__init__.py'
   if not link.exists():link.symlink_to(ROOT/'engine/providers/comfy_metrics.py')
   args+=['--whitelist-custom-nodes','narrative_metrics','--disable-dynamic-vram','--disable-async-offload']
   start(name,args,{'CUDA_VISIBLE_DEVICES':devices[ordinal],'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','NARRATIVE_GPU_DEVICE':devices[ordinal],'NARRATIVE_PHYSICAL_GPU':devices[ordinal],'NARRATIVE_METRICS_DIR':str(ROOT/'runtime'/f'metrics-{gpu}')})
  if f'worker-{gpu}' in selected:start(f'worker-{gpu}',[py,'-m','engine.scheduler.worker','--gpu',str(gpu),'--kinds',os.environ.get('NARRATIVE_WORKER_KINDS','image,edit,tts')],{'CUDA_VISIBLE_DEVICES':devices[ordinal],'NARRATIVE_GPU_DEVICE':devices[ordinal]})
 if a.action=='run':supervise(selected)
if __name__=='__main__':main()
