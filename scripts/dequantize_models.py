"""CPU-only conversion of selected FP8 storage into plain BF16 tensors.

This restores no lost precision. It avoids runtime mixed-format GPU requantizing
when applying the acceleration adapter, and makes low-VRAM estimates reliable.
"""
import gc,json,time,hashlib
from pathlib import Path
import torch
from safetensors import safe_open
from safetensors.torch import save_file
R=Path(__file__).resolve().parents[1];torch.set_num_threads(6)
names=['qwen_image_edit_2511_fp8mixed','wan2.2_i2v_high_noise_14B_fp8_scaled','wan2.2_i2v_low_noise_14B_fp8_scaled']
reports=[]
for name in names:
 src=R/'models/diffusion_models'/(name+'.safetensors');out=src.with_name(name+'-dequant-bf16.safetensors');start=time.monotonic()
 if out.exists() or out.with_name(out.stem+'-clean.safetensors').exists():continue
 state={};quantized=0
 with safe_open(src,framework='pt',device='cpu') as f:
  keys=set(f.keys())
  for k in sorted(keys):
   if any(k.endswith(s) for s in ['.comfy_quant','.input_scale','.weight_scale','.scale_input','.scale_weight']):continue
   x=f.get_tensor(k)
   if x.dtype in [torch.float8_e4m3fn,torch.float8_e5m2]:
    prefix=k.removesuffix('.weight');scale_key=next((key for key in [prefix+'.weight_scale',prefix+'.scale_weight'] if key in keys),None)
    scale=f.get_tensor(scale_key).float() if scale_key else torch.tensor(1.)
    if scale.numel()!=1:raise ValueError('非标量缩放，不能按本配方转换: '+k)
    state[k]=(x.float()*scale).to(torch.bfloat16).contiguous();quantized+=1
   else:state[k]=x.to(torch.bfloat16).contiguous() if x.is_floating_point() else x
 print('Saving',out.name,'tensors',len(state),'dequantized',quantized,flush=True)
 part=out.with_suffix('.writing');save_file(state,str(part),metadata={'source':src.name,'conversion':'FP8 storage to BF16, scalar weight dequantization; original quantization error retained'});part.replace(out)
 report={'source':str(src),'derived':str(out),'quantized_tensors':quantized,'bytes':out.stat().st_size,'seconds':time.monotonic()-start,'precision_note':'BF16容器来自FP8解量化，不是原生BF16精度'}
 reports.append(report);del state;gc.collect()
 (R/'model-manifests/dequantized.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2))
 print('COMPLETE',out.name,round(time.monotonic()-start,1),flush=True)
