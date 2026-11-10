"""Project-owned ComfyUI instrumentation extension, GPL-3.0-or-later.

Runs only in this project's two ComfyUI processes. Measures CUDA allocation in
that process, rather than attributing other users' GPU memory to this job.
"""
import contextvars,functools,json,os,time
from pathlib import Path
import torch,execution,comfy.model_management
from comfy_api.latest import ComfyExtension,io
_ctx=contextvars.ContextVar('narrative_metrics',default=None)
_original=execution.PromptExecutor.execute
_node_original=execution.execute
_load_original=comfy.model_management.load_models_gpu
@functools.wraps(_load_original)
def measured_load(*args,**kwargs):
    state=_ctx.get();started=time.monotonic()
    try:return _load_original(*args,**kwargs)
    finally:
        if state is not None:state.setdefault('model_load_events',[]).append({'seconds':round(time.monotonic()-started,4)})
@functools.wraps(_node_original)
async def measured_node(*args,**kwargs):
    started=time.monotonic()
    try:return await _node_original(*args,**kwargs)
    finally:
        state=_ctx.get()
        if state is not None:
            item=args[3] if len(args)>3 else kwargs.get('current_item')
            state['node_seconds'][str(item)]=round(time.monotonic()-started,4)
@functools.wraps(_original)
def measured_prompt(self,prompt,prompt_id,*args,**kwargs):
    torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
    started=time.monotonic();data={'prompt_id':prompt_id,'node_seconds':{},'physical_gpu':os.environ.get('NARRATIVE_PHYSICAL_GPU')};token=_ctx.set(data)
    try:return _original(self,prompt,prompt_id,*args,**kwargs)
    finally:
        torch.cuda.synchronize();data.update(wall_seconds=round(time.monotonic()-started,4),torch_peak_allocated_mib=torch.cuda.max_memory_allocated()/1024**2,torch_peak_reserved_mib=torch.cuda.max_memory_reserved()/1024**2)
        data['node_classes']={k:v.get('class_type') for k,v in prompt.items()}
        data['model_load_seconds']=sum(v['seconds'] for v in data.get('model_load_events',[]))
        p=Path(os.environ['NARRATIVE_METRICS_DIR']);p.mkdir(parents=True,exist_ok=True);(p/(prompt_id+'.json')).write_text(json.dumps(data,indent=2));_ctx.reset(token)
execution.execute=measured_node
execution.PromptExecutor.execute=measured_prompt
comfy.model_management.load_models_gpu=measured_load
class NarrativeExtension(ComfyExtension):
    async def get_node_list(self):return []
async def comfy_entrypoint():return NarrativeExtension()
