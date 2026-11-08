import json,os,re,sqlite3,time,unicodedata
from pathlib import Path
import torch,numpy as np,soundfile as sf,librosa
from qwen_asr import Qwen3ASRModel
R=Path(__file__).resolve().parents[1];torch.set_num_threads(6)
c=sqlite3.connect(R/'runtime/studio.sqlite3');c.row_factory=sqlite3.Row
rows=[dict(r) for r in c.execute("SELECT * FROM tasks WHERE kind='tts' AND output IS NOT NULL ORDER BY created")];c.close()
paths=[str(R/'projects'/r['project']/r['output']) for r in rows]
t=time.monotonic();model=Qwen3ASRModel.from_pretrained(str(R/'models/asr'),dtype=torch.float32,device_map='cpu',max_inference_batch_size=2,max_new_tokens=128,attn_implementation='sdpa')
results=model.transcribe(audio=paths,language='Chinese')
def normalize(s):return ''.join(ch for ch in unicodedata.normalize('NFKC',s) if ch.isalnum())
checks=[]
for row,path,result in zip(rows,paths,results):
 p=json.loads(row['params']);expected=p['spec']['dialogue'];data,sr=sf.read(path);data=data.mean(axis=1) if data.ndim==2 else data
 f0=librosa.yin(data,fmin=65,fmax=450,sr=sr);rms=librosa.feature.rms(y=data)[0];n=min(len(f0),len(rms));voiced=f0[:n][rms[:n]>max(.015,np.max(rms)*.2)]
 check={'task':row['id'],'project':row['project'],'shot':row['shot'],'expected':expected,'recognized':result.text,'language':result.language,'normalized_exact_match':normalize(expected)==normalize(result.text),'duration':len(data)/sr,'rms':float(np.sqrt(np.mean(data**2))),'clipped_fraction':float(np.mean(np.abs(data)>=.999)),'median_pitch_hz':float(np.median(voiced)) if len(voiced) else None,'speaker':p['config_snapshot']['voices'][p['spec']['speaker']]['speaker']}
 checks.append(check)
print(json.dumps(checks,ensure_ascii=False,indent=2))
(R/'runtime/audio-check.json').write_text(json.dumps({'model':'Qwen/Qwen3-ASR-0.6B','device':'CPU','wall_seconds':time.monotonic()-t,'checks':checks,'limit':'ASR与声学规则检验不能替代人的听审；未声称口型同步。'},ensure_ascii=False,indent=2))
