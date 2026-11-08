"""Remove the legacy scaled_fp8 control name without touching tensor payloads.

The marker is retained under an inert provenance name. Header rewrite preserves
the original header length and all tensor offsets; safetensors validates it.
"""
import json,os,struct,time
from pathlib import Path
from safetensors import safe_open
R=Path(__file__).resolve().parents[1];results=[]
for role in ['high','low']:
 src=R/'models/diffusion_models'/f'wan2.2_i2v_{role}_noise_14B_fp8_scaled-dequant-bf16.safetensors'
 dest=src.with_name(src.stem+'-clean.safetensors')
 if dest.exists():continue
 deadline=time.monotonic()+1800
 while not src.exists():
  if time.monotonic()>deadline:raise TimeoutError('等待转换文件超时: '+src.name)
  time.sleep(5)
 with src.open('r+b') as f:
  length=struct.unpack('<Q',f.read(8))[0];header=json.loads(f.read(length))
  if 'scaled_fp8' in header:header['source_f8_']=header.pop('scaled_fp8')
  data=json.dumps(header,separators=(',',':')).encode();assert len(data)<=length
  f.seek(8);f.write(data+b' '*(length-len(data)));f.flush();os.fsync(f.fileno())
 with safe_open(src,framework='pt',device='cpu') as f:
  assert 'scaled_fp8' not in f.keys();assert f.get_slice('blocks.0.cross_attn.k.weight').get_dtype()=='BF16'
 src.replace(dest);results.append({'path':str(dest),'marker':'scaled_fp8 renamed source_f8_; payload unchanged'})
 print('COMPLETE',dest.name,flush=True)
(R/'model-manifests/dequant-finalization.json').write_text(json.dumps(results,indent=2));(R/'runtime/dequant.status').write_text('complete\n')
