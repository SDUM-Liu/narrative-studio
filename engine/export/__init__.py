import csv,json,subprocess,time,uuid,shutil
from pathlib import Path
from engine.core import ROOT,config,db,list_shots,project_dir,safe_file,inputs_current
def inspect(path):
    import av
    with av.open(str(path)) as c:
        video=next((s for s in c.streams if s.type=='video'),None);audio=next((s for s in c.streams if s.type=='audio'),None)
        duration=float(c.duration/av.time_base) if c.duration else max((float(s.duration*s.time_base) for s in c.streams if s.duration is not None),default=0.)
        data={'duration':duration,'audio':audio is not None,'has_audio':audio is not None}
        if video:data.update(width=video.width,height=video.height,fps=float(video.average_rate or 0),frames=sum(1 for _ in c.decode(video)))
        if video:data['video_codec']=video.codec_context.name
        if audio:data.update(audio_codec=audio.codec_context.name,audio_sample_rate=audio.rate,audio_channels=audio.codec_context.layout.nb_channels)
        return data
def export_episode(pid,ep,draft=False):
    import imageio_ffmpeg
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe();cfg=config(pid);shots=list_shots(pid,ep)
    if not shots:raise ValueError('分集不存在')
    selected=[]
    with db() as c:
        for s in shots:
            if draft:
                video=c.execute("SELECT * FROM tasks WHERE project=? AND episode=? AND shot=? AND kind='video' AND output IS NOT NULL AND status IN ('review','approved','rejected') ORDER BY created DESC LIMIT 1",(pid,ep,s['id'])).fetchone()
            else:video=c.execute("SELECT * FROM tasks WHERE id=? AND status='approved'",(s['selected_video'],)).fetchone()
            if not video:raise ValueError(f"{s['id']}: 视频尚未通过审核")
            if not inputs_current(json.loads(video['params']),s,pid):raise ValueError(f"{s['id']}: 视频参考集合/任务输入审核已过期")
            voice=c.execute("SELECT * FROM tasks WHERE id=? AND status='approved'",(s['selected_tts'],)).fetchone()
            policy=s['spec'].get('audio_policy',cfg.get('export',{}).get('audio_policy','native'))
            if policy not in {'native','external_tts','mix'}:raise ValueError('audio_policy must be native, external_tts or mix')
            if policy in {'external_tts','mix'} and not voice:raise ValueError(f"{s['id']}: 显式外部配音策略需要已审核TTS")
            selected.append((s,dict(video),dict(voice) if voice else None))
    dest=project_dir(pid)/'exports'/f'{ep}-{int(time.time())}-{uuid.uuid4().hex[:8]}';dest.mkdir(parents=True)
    clips=[];timeline=[];subtitle=[];position=0.;fmt=cfg['format'];options=cfg.get('export',{})
    def stamp(t):
        ms=round(t*1000);return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}'
    def run(args):
        r=subprocess.run([ffmpeg,'-hide_banner','-nostdin','-y',*args],capture_output=True,text=True,timeout=1200)
        with (dest/'ffmpeg.log').open('a') as f:f.write(r.stderr)
        if r.returncode:raise RuntimeError('FFmpeg失败，查看导出目录ffmpeg.log')
    for i,(s,v,a) in enumerate(selected):
        vp=safe_file(pid,v['output']);info=inspect(vp)
        duration=info['duration']
        if not info.get('width') or not duration:raise ValueError(f"{s['id']}: 无有效视频流/时长")
        if duration<s['spec']['target_duration']-.75:raise ValueError(f"{s['id']}: 实际视频短于目标时长，拒绝循环或静帧补足")
        policy=s['spec'].get('audio_policy',options.get('audio_policy','native'))
        has_native=bool(info['audio'])
        if policy in {'native','mix'} and not has_native and not options.get('allow_silent',False):
            raise ValueError(f"{s['id']}: 原生音轨缺失；明确选择外部TTS策略或重新生成")
        if policy=='native':a=None
        trim=cfg.get('editing',{}).get('trims',{}).get(ep,{}).get(s['id'],{})
        approved_range=json.loads(v['params']).get('approved_edit_range')
        if approved_range is not None and (trim.get('in',0)!=approved_range.get('in',0) or trim.get('out')!=approved_range.get('out')):raise ValueError(f"{s['id']}: 当前剪辑范围与审核通过范围不一致，需要重新审核")
        edit_in=float(trim.get('in',0));edit_out=float(trim.get('out',duration))
        if not 0<=edit_in<edit_out<=duration+.05:raise ValueError(f"{s['id']}: 剪辑入出点超出实际视频")
        if edit_in and s['spec']['dialogue'] and policy!='native':raise ValueError('外部配音镜头非零入点需先重新校验配音')
        duration=edit_out-edit_in
        args=['-ss',str(edit_in),'-i',str(vp)]
        ai=None
        if a:
            ap=safe_file(pid,a['output']);ai=inspect(ap)
            if not ai['audio']:raise ValueError('已选TTS没有音频流')
            if ai['duration']>duration+.1:raise ValueError(f"{s['id']}: 配音长于视频，请调整镜头或重做，未截断台词")
            args+=['-i',str(ap)]
        elif not has_native:
            args+=['-f','lavfi','-i','anullsrc=r=32000:cl=stereo']
        clip=dest/f'{i:03}.mp4'
        unchanged=edit_in==0 and abs(edit_out-info['duration'])<.001
        matches=info['width']==fmt['width'] and info['height']==fmt['height'] and abs(info['fps']-fmt['fps'])<.01
        if policy=='native' and has_native and unchanged and matches and info.get('video_codec')=='h264' and info.get('audio_codec')=='aac':
            shutil.copy2(vp,clip)
        else:
            args+=['-map','0:v:0']
            if policy=='mix':
                if not has_native:raise ValueError('混音需要原生音轨')
                args+=['-filter_complex','[0:a:0][1:a:0]amix=inputs=2:duration=first:normalize=0,apad[mix]','-map','[mix]']
            else:args+=['-map','0:a:0' if policy=='native' and has_native else '1:a:0']
            if unchanged and matches:args+=['-c:v','copy']
            else:
                args+=['-vf',f"scale={fmt['width']}:{fmt['height']}:force_original_aspect_ratio=decrease,pad={fmt['width']}:{fmt['height']}:(ow-iw)/2:(oh-ih)/2,fps={fmt['fps']}",
                       '-c:v','libx264','-preset',options.get('video_preset','slow'),'-crf',str(options.get('video_crf',16)),'-pix_fmt','yuv420p','-threads','6']
            if policy=='native' and has_native and info.get('audio_codec')=='aac' and info.get('audio_sample_rate')==32000 and info.get('audio_channels')==2:
                args+=['-c:a','copy']
            else:
                if policy!='mix':args+=['-af','apad']
                args+=['-c:a','aac','-b:a',options.get('audio_bitrate','320k'),'-ar','32000','-ac','2']
            args+=['-t',str(duration),'-movflags','+faststart',str(clip)]
            run(args)
        clips.append(clip)
        audio_source='native' if policy=='native' and has_native else 'mix' if policy=='mix' else 'external_tts' if a else 'explicit_silence'
        timeline.append({'shot':s['id'],'start':position,'duration':duration,'planned_duration':s['spec']['target_duration'],
                         'edit_in':edit_in,'edit_out':edit_out,'edit_reason':trim.get('reason',''),
                         'video':str(vp),'audio':str(safe_file(pid,a['output'])) if a else str(vp) if has_native else None,
                         'video_task':v['id'],'video_review_status':v['status'],'voice_task':a['id'] if a else None,
                         'dialogue':s['spec']['dialogue'],'whether_native_audio':has_native,'audio_source':audio_source,
                         'audio_policy':policy,'lip_sync':'unverified' if policy=='native' else 'requires_review',
                         'input_review_fingerprint':json.loads(v['params'])['input_review']['fingerprint']})
        if s['spec']['dialogue']:
            subtitle.extend([str(len([x for x in timeline if x['dialogue']])),f'{stamp(position)} --> {stamp(position+(min(duration,ai["duration"]) if ai else duration))}',s['spec']['dialogue'],''])
        position+=duration
    (dest/'subtitles.srt').write_text('\n'.join(subtitle),encoding='utf-8')
    (dest/'timeline.json').write_text(json.dumps({'project':pid,'episode':ep,'fps':fmt['fps'],'clips':timeline,'lip_sync':'unverified','draft':draft},ensure_ascii=False,indent=2))
    with (dest/'timeline.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(timeline[0]));w.writeheader();w.writerows(timeline)
    concat=dest/'concat.txt';concat.write_text('\n'.join(f"file '{p.name}'" for p in clips))
    run(['-f','concat','-safe','1','-i',str(concat),'-c','copy','-movflags','+faststart',str(dest/'clean.mp4')])
    if subtitle or draft:
        filters=[]
        if subtitle:filters.append(f"subtitles={dest/'subtitles.srt'}:fontsdir={ROOT/'shared-assets/fonts'}:force_style='Fontname=Noto Sans CJK SC,Fontsize=18,MarginV=28'")
        if draft:
            label=dest/'draft.ass'
            label.write_text('[Script Info]\nScriptType: v4.00+\nPlayResX: '+str(fmt['width'])+'\nPlayResY: '+str(fmt['height'])+'\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Draft,Noto Sans CJK SC,15,&H00FFFFFF,&H000000FF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,3,1,0,8,10,10,14,1\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\nDialogue: 0,0:00:00.00,1:00:00.00,Draft,,0,0,0,,动态技术预览 · 未完成影视质量验收\n')
            filters.append(f"ass={label}:fontsdir={ROOT/'shared-assets/fonts'}")
        run(['-i',str(dest/'clean.mp4'),'-vf',','.join(filters),'-c:v','libx264','-preset',options.get('video_preset','slow'),'-crf',str(options.get('video_crf',16)),'-threads','6','-c:a','copy','-movflags','+faststart',str(dest/'preview.mp4')])
    else:
        shutil.copy2(dest/'clean.mp4',dest/'preview.mp4')
    result=inspect(dest/'preview.mp4');result['draft']=draft;result['audio_sources']=[x['audio_source'] for x in timeline];result['clips']=timeline;result['lip_sync']='unverified';(dest/'validation.json').write_text(json.dumps(result,indent=2));return {'path':str(dest),'media':result}
def contact_sheet(pid,ep):
    from PIL import Image,ImageDraw
    with db() as c:rows=c.execute("SELECT shot,output,id FROM tasks WHERE project=? AND episode=? AND kind IN ('image','edit') AND output IS NOT NULL ORDER BY created",(pid,ep)).fetchall()
    if not rows:raise ValueError('暂无首帧')
    canvas=Image.new('RGB',(800,260*((len(rows)+3)//4)),(20,25,35));draw=ImageDraw.Draw(canvas)
    for i,r in enumerate(rows):
        im=Image.open(safe_file(pid,r['output'])).convert('RGB');im.thumbnail((190,225));x=(i%4)*200;y=(i//4)*260;canvas.paste(im,(x,y));draw.text((x,y+230),f"{r['shot']} {r['id'][:8]}",fill='white')
    dest=project_dir(pid)/'exports'/f'contact-{ep}-{int(time.time())}.jpg';canvas.save(dest);return str(dest)
