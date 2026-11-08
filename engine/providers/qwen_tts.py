import json,os,subprocess
import httpx
from engine.core import ROOT
from engine.providers.comfy import endpoint
def required_files(p):
    base=ROOT/p.get('model_path','models/tts')
    return [base/'model.safetensors',base/'speech_tokenizer/model.safetensors',ROOT/'.venv-tts/bin/python',ROOT/'runtime/tts.status']
def run(task,gpu,out):
    try:httpx.post(endpoint(gpu)+'/free',json={'unload_models':True,'free_memory':False},trust_env=False,timeout=30)
    except httpx.TransportError:pass
    env={**os.environ,'CUDA_VISIBLE_DEVICES':str(gpu),'OMP_NUM_THREADS':'6','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
    with (out/'provider.log').open('w') as log:
        subprocess.run([str(ROOT/'.venv-tts/bin/python'),str(ROOT/'scripts/tts_infer.py'),str(out/'task.json'),str(out)],env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=task['params'].get('timeout',1200))
    return out/'voice.wav',json.loads((out/'tts-metrics.json').read_text())
