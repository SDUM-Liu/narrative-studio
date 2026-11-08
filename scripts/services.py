"""Manage only this project's own processes; no sudo, no global service mutation."""
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'runtime/services.json'
def alive(item):
 try:
  parts=Path(f"/proc/{item['pid']}/stat").read_text().split()
  return parts[2]!='Z' and parts[21]==item['start_ticks']
 except (OSError,IndexError):return False
def load():return json.loads(STATE.read_text()) if STATE.exists() else {}
def start(name,args,env=None):
 state=load()
 if name in state and alive(state[name]):return
 log=(ROOT/'logs'/f'{name}.log').open('a')
 proc=subprocess.Popen(args,cwd=ROOT,env={**os.environ,'OMP_NUM_THREADS':'6','MKL_NUM_THREADS':'6','HF_HOME':str(ROOT/'models/hf-cache'),**(env or {})},stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
 state[name]={'pid':proc.pid,'start_ticks':Path(f'/proc/{proc.pid}/stat').read_text().split()[21],'argv':args,'started':time.time()}
 tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(state,indent=2));tmp.replace(STATE);log.close()
def stop(names=None):
 state=load()
 for name,item in state.items():
  if names and name not in names:continue
  if alive(item):
   os.killpg(item['pid'],signal.SIGTERM)
   deadline=time.monotonic()+12
   while alive(item) and time.monotonic()<deadline:time.sleep(.2)
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['start','stop','status','restart']);p.add_argument('--only',nargs='*');p.add_argument('--gpus',type=int,nargs='+',choices=[0,1],default=[0,1]);a=p.parse_args()
 if a.action=='status':print(json.dumps({k:{**v,'alive':alive(v)} for k,v in load().items()},indent=2));return
 if a.action=='stop':stop(a.only);return
 if a.action=='restart':stop(a.only)
 (ROOT/'runtime').mkdir(exist_ok=True);(ROOT/'logs').mkdir(exist_ok=True)
 selected=a.only or ['review']+[f'{kind}-{gpu}' for gpu in a.gpus for kind in ['comfy','worker']]
 # Generate absolute model paths at installation location; never ship host paths.
 import yaml
 (ROOT/'configs/model-paths.yaml').write_text(yaml.safe_dump({'narrative':{'base_path':str(ROOT/'models'),'diffusion_models':'diffusion_models','text_encoders':'text_encoders','vae':'vae','loras':'loras'}}))
 py=str(ROOT/'.venv-control/bin/python')
 if 'review' in selected:start('review',[py,'-m','uvicorn','engine.review.app:app','--host','127.0.0.1','--port','7868','--no-access-log'])
 for gpu in a.gpus:
  name=f'comfy-{gpu}'
  if name in selected:
   for folder in ['input','output','user']:(ROOT/'runtime'/f'{name}-{folder}').mkdir(exist_ok=True)
   args=[str(ROOT/'.venv/bin/python'),str(ROOT/'vendor/ComfyUI/main.py'),'--listen','127.0.0.1','--port',str(8190+gpu),'--lowvram','--reserve-vram','5','--disable-smart-memory','--disable-pinned-memory','--preview-method','none','--offline','--disable-assets','--disable-all-custom-nodes','--extra-model-paths-config',str(ROOT/'configs/model-paths.yaml'),'--input-directory',str(ROOT/'runtime'/f'{name}-input'),'--output-directory',str(ROOT/'runtime'/f'{name}-output'),'--user-directory',str(ROOT/'runtime'/f'{name}-user')]
   extension=ROOT/'vendor/ComfyUI/custom_nodes/narrative_metrics'
   extension.mkdir(exist_ok=True)
   link=extension/'__init__.py'
   if not link.exists():link.symlink_to(ROOT/'engine/providers/comfy_metrics.py')
   args+=['--whitelist-custom-nodes','narrative_metrics','--disable-dynamic-vram','--disable-async-offload']
   start(name,args,{'CUDA_VISIBLE_DEVICES':str(gpu),'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','NARRATIVE_PHYSICAL_GPU':str(gpu),'NARRATIVE_METRICS_DIR':str(ROOT/'runtime'/f'metrics-{gpu}')})
  if f'worker-{gpu}' in selected:start(f'worker-{gpu}',[py,'-m','engine.scheduler.worker','--gpu',str(gpu),'--kinds','image,edit,video,tts'])
if __name__=='__main__':main()
