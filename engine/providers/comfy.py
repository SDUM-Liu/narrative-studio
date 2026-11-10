import json,shutil,time,uuid
from pathlib import Path
import httpx,websocket
from engine.core import ROOT,safe_file
from engine.workflows import image_graph

def endpoint(gpu):return f'http://127.0.0.1:{8190+gpu}'
def required_files(p):
    required=[]
    for key in ['model']:
        if p.get(key):required.append(ROOT/'models/diffusion_models'/p[key])
    for key,folder in [('lora','loras'),('vae','vae'),('text_encoder','text_encoders')]:
        if p.get(key):required.append(ROOT/'models'/folder/p[key])
    return required
def run(task,gpu,out):
    p=dict(task['params']);pid=task['project'];tid=task['id'];client=uuid.uuid4().hex
    p['output_prefix']=f"{pid}/{task['episode']}/{task['shot']}/{tid}"
    p['spec']=dict(p['spec'])
    http=httpx.Client(base_url=endpoint(gpu),timeout=120,trust_env=False)
    def upload(path):
        with Path(path).open('rb') as f:
            r=http.post('/upload/image',files={'image':(f'{tid}-{Path(path).name}',f,'image/png')},data={'type':'input','overwrite':'false'})
        r.raise_for_status();d=r.json();return (d.get('subfolder','')+'/' +d['name']).lstrip('/')
    if task['kind'] not in {'image','edit'}:raise ValueError('Comfy provider only generates reference assets; video uses minimax-h3')
    g=image_graph(p,[upload(safe_file(pid,r)) for r in p['spec']['reference_images']] if task['kind']=='edit' else None)
    (out/'workflow.api.json').write_text(json.dumps(g,ensure_ascii=False,indent=2))
    # The socket supplies completion events; no repeated full-log/history polling.
    ws=websocket.create_connection(f'ws://127.0.0.1:{8190+gpu}/ws?clientId={client}',timeout=45,http_proxy_host=None)
    start=time.monotonic();r=http.post('/prompt',json={'prompt':g,'client_id':client});r.raise_for_status();response=r.json()
    if 'prompt_id' not in response:raise RuntimeError(str(response))
    prompt_id=response['prompt_id'];(out/'comfy-prompt.json').write_text(json.dumps(response))
    deadline=start+p.get('timeout',7200)
    try:
        while time.monotonic()<deadline:
            try:msg=ws.recv()
            except websocket.WebSocketTimeoutException:continue
            if not msg:raise RuntimeError('ComfyUI连接中断；任务保留，可恢复')
            if not isinstance(msg,str):continue
            d=json.loads(msg);data=d.get('data',{})
            if data.get('prompt_id')!=prompt_id:continue
            if d['type']=='execution_error':
                (out/'comfy-error.json').write_text(json.dumps(data,ensure_ascii=False,indent=2))
                raise RuntimeError(data.get('exception_message','ComfyUI推理失败'))
            if d['type']=='execution_interrupted':raise RuntimeError('ComfyUI推理被中断')
            if d['type']=='executing' and data.get('node') is None:break
        else:
            http.post('/interrupt');raise TimeoutError('本项目ComfyUI任务超时')
    finally:ws.close()
    hist=http.get('/history/'+prompt_id);hist.raise_for_status();h=hist.json().get(prompt_id,{})
    (out/'comfy-history.json').write_text(json.dumps(h,ensure_ascii=False,indent=2))
    output=None
    for entry in h.get('outputs',{}).values():
        for key in ['images']:
            vals=entry.get(key,[])
            if isinstance(vals,dict):vals=[vals]
            for item in vals:
                if isinstance(item,dict) and item.get('filename'):
                    file=ROOT/'runtime'/f'comfy-{gpu}-output'/item.get('subfolder','')/item['filename']
                    if file.is_file() and file.suffix.lower() in ['.png','.jpg']:
                        output=file
    if not output:raise RuntimeError('推理结束但未找到输出；查看comfy-history.json')
    dest=out/'frame.png'
    shutil.copy2(output,dest)
    # Release this project's cached models between tasks on the shared host.
    # Unload GPU weights while preserving the CPU model cache. free_memory=True
    # resets PromptExecutor and forces repeated cold loads from the shared HDD.
    http.post('/free',json={'unload_models':True,'free_memory':False});http.close()
    metrics={'provider':'comfy-local','prompt_id':prompt_id,'inference_and_load_seconds':round(time.monotonic()-start,3),'workflow':str((out/'workflow.api.json').relative_to(ROOT))}
    peak=ROOT/'runtime'/f'metrics-{gpu}'/(prompt_id+'.json')
    if peak.exists():metrics.update(json.loads(peak.read_text()))
    return dest,metrics
