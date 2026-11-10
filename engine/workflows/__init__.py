"""Qwen reference-asset graphs. H3 video requests live in workflows.h3."""
def node(name,**inputs):return {'class_type':name,'inputs':inputs}
def image_graph(p,refs=None):
    s=p['spec'];edit=bool(refs);model=p['model'];w=p['width'];h=p['height']
    g={'1':node('UNETLoader',unet_name=model,weight_dtype='default'),
       '2':node('CLIPLoader',clip_name=p['text_encoder'],type='qwen_image',device='cpu'),
       '3':node('VAELoader',vae_name=p['vae']),
       '4':node('ModelSamplingAuraFlow',model=['1',0],shift=p.get('shift',3.1)),
       '5':node('CLIPTextEncode',clip=['2',0],text=s['effective_prompt']),
       '6':node('CLIPTextEncode',clip=['2',0],text=s['effective_negative']),
       '7':node('EmptySD3LatentImage',width=w,height=h,batch_size=1),
       '8':node('KSampler',model=['4',0],positive=['5',0],negative=['6',0],latent_image=['7',0],seed=s['seed'],steps=p.get('steps',50),cfg=p.get('cfg',4),sampler_name='euler',scheduler='simple',denoise=1),
       '9':node('VAEDecode',samples=['8',0],vae=['3',0]),
       '10':node('SaveImage',images=['9',0],filename_prefix=p['output_prefix'])}
    if p.get('lora'):
        g['11']=node('LoraLoaderModelOnly',model=['1',0],lora_name=p['lora'],strength_model=1.)
        g['4']['inputs']['model']=['11',0]
    if edit:
        if len(refs)>3:raise ValueError('当前编辑工作流最多支持3张参考图')
        for i,name in enumerate(refs):g[str(20+i)]=node('LoadImage',image=name)
        extra={f'image{i+1}':[str(20+i),0] for i in range(len(refs))}
        g['5']=node('TextEncodeQwenImageEditPlus',clip=['2',0],vae=['3',0],prompt=s['effective_prompt'],**extra)
        g['6']=node('TextEncodeQwenImageEditPlus',clip=['2',0],vae=['3',0],prompt=s['effective_negative'],**extra)
    return g
