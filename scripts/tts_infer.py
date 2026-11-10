"""Official Qwen3 CustomVoice offline inference. No voice cloning or paid API."""
import json,sys,time,re,os
from pathlib import Path
root=Path(__file__).resolve().parents[1]
if (root/'vendor/sox/usr/bin/sox').exists():
 os.environ['PATH']=str(root/'vendor/sox/usr/bin')+os.pathsep+os.environ.get('PATH','')
 previous_ld_path=os.environ.get('LD_LIBRARY_PATH','')
 os.environ['LD_LIBRARY_PATH']=str(root/'vendor/sox/usr/lib/x86_64-linux-gnu')+(os.pathsep+previous_ld_path if previous_ld_path else '')
import torch,soundfile as sf,numpy as np
from qwen_tts import Qwen3TTSModel
task=json.loads(Path(sys.argv[1]).read_text());out=Path(sys.argv[2]);p=task['params'];s=p['spec'];cfg=p['config_snapshot']
torch.set_num_threads(int(os.environ.get('OMP_NUM_THREADS','4')));torch.manual_seed(s['seed']);torch.cuda.reset_peak_memory_stats()
started=time.monotonic()
print('Loading Qwen3-TTS on allocated GPU',flush=True)
dtype=torch.bfloat16 if torch.cuda.is_bf16_supported(including_emulation=False) else torch.float32
model=Qwen3TTSModel.from_pretrained(str(root/p['model_path']),device_map='cuda:0',dtype=dtype,attn_implementation='sdpa')
loaded=time.monotonic();voice=cfg['voices'][s['speaker']]
print('Model loaded',round(loaded-started,2),'seconds',flush=True)
sentences=[line.strip() for line in re.split(r'(?<=[。！？!?])\s*',s['dialogue']) if line.strip()]
pause_ms=int(voice.get('pause_ms',cfg.get('defaults',{}).get('pause_ms',180)))
if not 0<=pause_ms<=2000:raise ValueError('pause_ms应在0至2000之间')
audio=[];segments=[];cursor=0.
for index,text in enumerate(sentences):
 print('Generating sentence',index,flush=True)
 waves,sr=model.generate_custom_voice(text=text,language=cfg['language'],speaker=voice['speaker'],do_sample=False,max_new_tokens=2048)
 wave=waves[0];duration=len(wave)/sr
 sf.write(out/f'sentence-{index:03}.wav',wave,sr)
 segments.append({'index':index,'text':text,'start':cursor,'end':cursor+duration,'speaker':voice['speaker'],'file':f'sentence-{index:03}.wav'})
 audio.append(wave);cursor+=duration
 if index<len(sentences)-1:
  silence=np.zeros(round(sr*pause_ms/1000),dtype=wave.dtype);audio.append(silence);cursor+=len(silence)/sr
combined=np.concatenate(audio);sf.write(out/'voice.wav',combined,sr)
metrics={'provider':'qwen3-tts-local','dtype':str(dtype),'gpu_device':os.environ.get('NARRATIVE_GPU_DEVICE'),'slurm_job_id':os.environ.get('SLURM_JOB_ID'),'speaker':voice['speaker'],'sample_rate':sr,'duration':len(combined)/sr,'load_seconds':loaded-started,'inference_seconds':time.monotonic()-loaded,'torch_peak_allocated_mib':torch.cuda.max_memory_allocated()/1024**2,'text':s['dialogue'],'seed':s['seed'],'segments':segments,'pause_ms':pause_ms}
(out/'tts-metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2))
