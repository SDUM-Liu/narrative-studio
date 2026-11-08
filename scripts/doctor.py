"""Read-only diagnostics; deliberately never serializes environment variables."""
import datetime,json,os,platform,shutil,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def run(args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=30);return {'code':p.returncode,'stdout':p.stdout[:20000],'stderr':p.stderr[:3000]}
 except Exception as e:return {'error':str(e)}
report={'time_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'project_root':str(ROOT),'python':sys.version,'platform':platform.platform(),'uid':os.getuid(),'disk':dict(zip(['total','used','free'],shutil.disk_usage(ROOT))),'commands':{}}
commands={'os':['cat','/etc/os-release'],'cpu':['lscpu'],'memory':['free','-h'],'mounts':['df','-hT'],'gpu':['nvidia-smi'],'topology':['nvidia-smi','topo','-m'],'gpu_details':['nvidia-smi','--query-gpu=index,name,uuid,memory.total,memory.used,memory.free,mig.mode.current,driver_version','--format=csv'],'ports':['ss','-ltnp'],'docker':['docker','version'],'tools':['bash','-c','command -v python python3 uv conda ffmpeg nvcc docker']}
for name,args in commands.items():report['commands'][name]=run(args)
try:
 import torch
 report['pytorch']={'version':torch.__version__,'cuda_runtime':torch.version.cuda,'available':torch.cuda.is_available(),'devices':torch.cuda.device_count()}
except Exception as e:report['pytorch']={'error':str(e)}
report['component_runtimes']={}
for name in ['.venv','.venv-tts','.venv-asr']:
 executable=ROOT/name/'bin/python'
 if executable.exists():report['component_runtimes'][name]=run([str(executable),'-c','import sys,torch; print(sys.version); print(torch.__version__,torch.version.cuda,torch.cuda.is_available())'])
out=ROOT/'runtime/environment.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'report':str(out),'gpu':report['commands']['gpu_details'],'pytorch':report['pytorch']},ensure_ascii=False,indent=2))
