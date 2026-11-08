"""ComfyUI API graphs. Model choices live in each project's YAML."""
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
       '8':node('KSampler',model=['4',0],positive=['5',0],negative=['6',0],latent_image=['7',0],seed=s['seed'],steps=p.get('steps',20),cfg=p.get('cfg',4),sampler_name='euler',scheduler='simple',denoise=1),
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
def video_graph(p,image):
    s=p['spec'];steps=p.get('steps',20);split=p.get('split_step',steps//2);primary=p.get('family','wan22-a14b')=='wan22-a14b'
    g={'2':node('CLIPLoader',clip_name=p['text_encoder'],type='wan',device='cpu'),
       '3':node('VAELoader',vae_name=p['vae']),
       '5':node('CLIPTextEncode',clip=['2',0],text=s['action_prompt'] or s['effective_prompt']),
       '6':node('CLIPTextEncode',clip=['2',0],text=s['effective_negative']),
       '7':node('LoadImage',image=image),
       '10':node('VAEDecodeTiled',samples=['9',0],vae=['3',0],tile_size=256,overlap=64,temporal_size=32,temporal_overlap=8),
       '12':node('SaveImage',images=['10',0],filename_prefix=p['output_prefix']+'/frames')}
    g['18']=node('NarrativeWanConditioningGuard',conditioning=['5',0],expected_dim=4096)
    g['19']=node('NarrativeWanConditioningGuard',conditioning=['6',0],expected_dim=4096)
    if primary:
        g['1']=node('UNETLoader',unet_name=p['high_model'],weight_dtype='default')
        g['13']=node('UNETLoader',unet_name=p['low_model'],weight_dtype='default')
        high=['1',0];low=['13',0]
        if p.get('high_lora'):
            g['14']=node('LoraLoaderModelOnly',model=high,lora_name=p['high_lora'],strength_model=1.);high=['14',0]
            g['15']=node('LoraLoaderModelOnly',model=low,lora_name=p['low_lora'],strength_model=1.);low=['15',0]
        g['4']=node('ModelSamplingSD3',model=high,shift=p.get('shift',5.))
        g['16']=node('ModelSamplingSD3',model=low,shift=p.get('shift',5.))
        g['17']=node('WanImageToVideo',positive=['18',0],negative=['19',0],vae=['3',0],width=p['width'],height=p['height'],length=p['frames'],batch_size=1,start_image=['7',0])
        common=dict(noise_seed=s['seed'],steps=steps,cfg=p.get('cfg',3.5),sampler_name='euler',scheduler='simple',positive=['17',0],negative=['17',1])
        g['8']=node('KSamplerAdvanced',model=['4',0],latent_image=['17',2],add_noise='enable',start_at_step=0,end_at_step=split,return_with_leftover_noise='enable',**common)
        g['9']=node('KSamplerAdvanced',model=['16',0],latent_image=['8',0],add_noise='disable',start_at_step=split,end_at_step=steps,return_with_leftover_noise='disable',**common)
    else:
        g['1']=node('UNETLoader',unet_name=p['model'],weight_dtype='default')
        g['4']=node('ModelSamplingSD3',model=['1',0],shift=p.get('shift',8.))
        g['8']=node('Wan22ImageToVideoLatent',vae=['3',0],width=p['width'],height=p['height'],length=p['frames'],batch_size=1,start_image=['7',0])
        g['9']=node('KSampler',model=['4',0],positive=['5',0],negative=['6',0],latent_image=['8',0],seed=s['seed'],steps=steps,cfg=p.get('cfg',5),sampler_name='uni_pc',scheduler='simple',denoise=1.)
    return g
