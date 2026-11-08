import json,shutil,time,uuid,subprocess
from pathlib import Path
import httpx,websocket
from engine.core import ROOT,safe_file
from engine.workflows import image_graph,video_graph

def endpoint(gpu):return f'http://127.0.0.1:{8190+gpu}'
def required_files(p):
    required=[]
    for key in ['model','high_model','low_model']:
        if p.get(key):required.append(ROOT/'models/diffusion_models'/p[key])
    for key,folder in [('lora','loras'),('high_lora','loras'),('low_lora','loras'),('vae','vae'),('text_encoder','text_encoders')]:
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
    if task['kind']=='video' and p.get('reuse_comfy_graph'):
        g=json.loads(safe_file(pid,p['reuse_comfy_graph']).read_text())
        g.pop('11',None)
        g['12']={'class_type':'SaveImage','inputs':{'images':['10',0],'filename_prefix':p['output_prefix']+'/frames'}}
    elif task['kind']=='video':g=video_graph(p,upload(safe_file(pid,p['image_path'])))
    else:g=image_graph(p,[upload(safe_file(pid,r)) for r in p['spec']['reference_images']] if task['kind']=='edit' else None)
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
    output=None;frame_paths=[]
    for entry in h.get('outputs',{}).values():
        for key in ['images','video','videos','gifs']:
            vals=entry.get(key,[])
            if isinstance(vals,dict):vals=[vals]
            for item in vals:
                if isinstance(item,dict) and item.get('filename'):
                    file=ROOT/'runtime'/f'comfy-{gpu}-output'/item.get('subfolder','')/item['filename']
                    if file.is_file() and file.suffix.lower() in ['.png','.jpg','.mp4','.webm','.mov']:
                        output=file
                        if file.suffix.lower() in ['.png','.jpg']:frame_paths.append(file)
    if not output:raise RuntimeError('推理结束但未找到输出；查看comfy-history.json')
    dest=out/('video.mp4' if task['kind']=='video' else 'frame.png')
    if task['kind']=='video' and frame_paths:
        import imageio_ffmpeg
        if len(frame_paths)!=p['frames']:raise RuntimeError(f'实际解码帧数{len(frame_paths)}与配方{p["frames"]}不一致')
        with (out/'ffmpeg-encode.log').open('w') as log:
            process=subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-nostdin','-y','-f','image2pipe','-framerate',str(p['fps']),'-vcodec','png','-i','pipe:0','-c:v','libx264','-preset','fast','-crf','19','-pix_fmt','yuv420p','-threads','6','-movflags','+faststart',str(dest)],stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log)
            try:
                for path in frame_paths:
                    with path.open('rb') as f:shutil.copyfileobj(f,process.stdin,1024*1024)
                process.stdin.close();code=process.wait(timeout=600)
            except Exception:
                process.kill();process.wait();raise
            if code:raise RuntimeError('生成帧编码失败；查看ffmpeg-encode.log')
        from engine.export import inspect
        media=inspect(dest)
        if media.get('frames')!=p['frames'] or abs(media.get('fps',0)-p['fps'])>.01:raise RuntimeError('MP4帧数或帧率验收失败')
        (out/'media-validation.json').write_text(json.dumps(media,indent=2))
        (out/'frame-index.json').write_text(json.dumps([str(f.relative_to(ROOT)) for f in frame_paths],indent=2))
    else:shutil.copy2(output,dest)
    # Release this project's cached models between tasks on the shared host.
    # Unload GPU weights while preserving the CPU model cache. free_memory=True
    # resets PromptExecutor and forces repeated cold loads from the shared HDD.
    http.post('/free',json={'unload_models':True,'free_memory':False});http.close()
    metrics={'provider':'comfy-local','prompt_id':prompt_id,'inference_and_load_seconds':round(time.monotonic()-start,3),'workflow':str((out/'workflow.api.json').relative_to(ROOT))}
    if p.get('reuse_comfy_graph'):
        original=safe_file(pid,p['reuse_comfy_graph']).parent
        original_prompt=json.loads((original/'comfy-prompt.json').read_text()).get('prompt_id')
        original_metrics=ROOT/'runtime'/f'metrics-{gpu}'/(str(original_prompt)+'.json')
        metrics['sampling_cache_reused']=True
        metrics['original_compute_prompt_id']=original_prompt
        if original_metrics.exists():metrics['original_compute_metrics']=json.loads(original_metrics.read_text())
    peak=ROOT/'runtime'/f'metrics-{gpu}'/(prompt_id+'.json')
    if peak.exists():metrics.update(json.loads(peak.read_text()))
    return dest,metrics
