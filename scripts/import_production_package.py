"""Import a local director/asset ZIP into a new isolated H3 production project."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile

import yaml
from PIL import Image
from engine import core as c


def member_name(info):
    name=info.filename
    if not info.flag_bits & 0x800:
        try:name=name.encode('cp437').decode('gb18030')
        except UnicodeError:pass
    name=name.replace('\\','/')
    parts=PurePosixPath(name).parts
    if not parts or name.startswith('/') or '..' in parts or re.match(r'^[A-Za-z]:',name):
        raise ValueError('Unsafe ZIP path')
    if stat.S_ISLNK(info.external_attr>>16):raise ValueError('ZIP symlinks are not supported')
    return '/'.join(parts)


def single(entries,suffix):
    matches=[(n,i) for n,i in entries.items() if n.endswith('/'+suffix)]
    if len(matches)!=1:raise ValueError('Expected one package member: '+suffix)
    return matches[0]


def prepare(archive,pid,episode=1,name=None):
    c.init();archive=Path(archive).resolve();ep=f'{int(episode):02}'
    if c.project_dir(pid).exists():raise ValueError('Project already exists; production packages never overwrite projects')
    with zipfile.ZipFile(archive) as z:
        entries={}
        for info in z.infolist():
            key=member_name(info)
            if key in entries:raise ValueError('Duplicate ZIP member after filename decoding')
            entries[key]=info
        if sum(i.file_size for i in entries.values())>4*1024**3:raise ValueError('Production package exceeds 4 GiB expanded')
        director_name,director_info=single(entries,'全26集_逐镜数据_V2.json')
        master_name,master_info=single(entries,'资产主表.json')
        mapping_name,mapping_info=single(entries,'逐镜资产映射_制作版.json')
        director=json.loads(z.read(director_info));assets=json.loads(z.read(master_info))
        maps={s['shot']:s for s in json.loads(z.read(mapping_info)) if int(s['episode'])==int(episode)}
        shots=[s for s in director if int(s['episode'])==int(episode)]
        if not shots or len(maps)!=len(shots):raise ValueError('Director and production mapping disagree')
        if any(s['duration']<4 or s['duration']>15 for s in shots):raise ValueError('Package contains shots outside local H3 duration limits; split explicitly before import')
        asset_root=PurePosixPath(master_name).parent
        by_id={a['id']:a for a in assets}
        cfg=c.create_project(pid,name or f'{archive.stem} · H3 第{ep}集')
        root=c.project_dir(pid);package=root/'assets/package'
        try:
            for member,info in entries.items():
                dest=package/member
                if info.is_dir():dest.mkdir(parents=True,exist_ok=True);continue
                dest.parent.mkdir(parents=True,exist_ok=True)
                with z.open(info) as src,dest.open('wb') as target:shutil.copyfileobj(src,target,1024**2)
            cfg['format'].update(aspect_ratio='9:16',width=768,height=1344,fps=24)
            cfg['target_duration']=sum(s['duration'] for s in shots)
            cfg['episodes']=1;cfg['genre']='真人写实空岛漫剧'
            cfg['style']={'description':'真人写实人物、电影光照；游戏世界保留立方几何和真实材质。8K是审美描述，不宣称原生8K。',
                          'positive_prefix':'','negative':''}
            chars=sorted({a for m in maps.values() for a in m['production_layers'].get('characters',[])})
            cfg['characters']={aid:{'name':by_id[aid]['name'],'description':by_id[aid]['description']} for aid in chars}
            cfg['budget']['max_jobs']=max(200,len(shots)*3)
            (root/'project.yaml').write_text(yaml.safe_dump(cfg,allow_unicode=True,sort_keys=False))
            rows=[];bindings=[];used={}
            for raw in shots:
                m=maps[raw['shot']];layers=m['production_layers'];selected=[]
                for category in ['background','background_parts','characters','mobs','props','structure_details']:
                    for aid in layers.get(category,[]):
                        if aid not in selected:selected.append(aid)
                if len(selected)>9:raise ValueError(raw['shot']+': visual reference set exceeds H3 limit; select explicitly')
                # A menu "button" is not a Minecraft wooden/stone button prop.
                deferred=[aid for aid in selected if by_id.get(aid,{}).get('name')=='按钮'
                          and any(word in raw.get('action','') for word in ('菜单','断开连接'))]
                selected=[aid for aid in selected if aid not in deferred]
                refs=[];definitions=[]
                for aid in selected:
                    if aid not in by_id:raise ValueError('Unregistered asset: '+aid)
                    asset=by_id[aid];rel=PurePosixPath(asset['file'])
                    if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe asset path')
                    path=package/asset_root/rel
                    if not path.is_file():raise ValueError('Missing production asset: '+aid)
                    with Image.open(path) as image:w,h=image.size;image.verify()
                    if not 256<=w<=5760 or not 256<=h<=5760 or not .4<=w/h<=2.5:raise ValueError('Reference outside H3 input limits: '+aid)
                    reference=str(path.relative_to(root));refs.append(reference);used[aid]=(asset,reference)
                    definitions.append(f'<Picture {len(refs)}> = {aid} {asset["name"]}: {asset["description"]}')
                onscreen=layers.get('characters',[])
                dialogue=raw.get('dialogue','');nonverbal=dialogue.startswith(('无对白','无新对白'))
                sound=raw.get('sound','')+(('；'+dialogue) if nonverbal else '')
                dialogue='' if nonverbal else dialogue
                continuity='\n'.join(definitions)+'\n参考人物只锁定脸、发型和服装；背景只锁定空间与光照；物品只锁定造型。不得照抄定妆棚背景或拼成素材板。'
                continuity+='\n出镜角色仅限：'+('、'.join(by_id[x]['name'] for x in onscreen) or '无')+'；画外对白不新增出镜人物。保持衣服、人物身份、装备获得顺序和伤痕连续。'
                continuity+='\n对白标签是表演指令，不读出角色名。景川用清晰自然的青年男声；知夏用清晰自然的青年女声；系统是中性画外提示音。'
                if '现实' in raw['scene']:
                    continuity+='\n现实场景使用真实家具建筑；只有导演指定穿越发生的瞬间才出现方块化，禁止提前变成游戏地形。'
                else:continuity+='\n游戏场景位于虚空悬浮方块岛，没有地面大陆；保留规则立方几何与真实材质。'
                if layers.get('background_parts'):continuity+='\n现实双地点使用同屏并列或原导演规定的交替画面；两人此时不在同一房间，不得提前会面。'
                continuity+='\nUI、字幕和告示牌准确文字由后期叠加，原生视频不要生成可读HUD或乱码。'
                row={'episode_id':ep,'shot_id':raw['shot'],'target_duration':raw['duration'],
                     'start':raw['start'],'end':raw['end'],'scene':raw['scene'],'characters':onscreen,
                     'prompt':raw['positive'],'motion_prompt':raw.get('action',''),
                     'camera_prompt':raw.get('camera',''),'continuity_prompt':continuity,
                     'negative_prompt':raw.get('negative',''),'dialogue':dialogue,'speaker':'',
                     'sound_intent':sound,'reference_images':refs,'reference_videos':[],'reference_audio':[],
                     'seed':20261110+int(raw['shot'].split('-')[-1]),
                     'production_layers':m,'source_director':raw,
                     'production_adjustments':[{'asset':aid,'reason':'菜单按钮由后期UI处理，不把木/石实体按钮放入场景'} for aid in deferred],
                     'source_asset_ids':selected,'expression':raw.get('expression',''),'blocking':raw.get('blocking','')}
                rows.append(row);bindings.append({'shot':raw['shot'],'references':selected,'production_mapping':m,'adjustments':row['production_adjustments']})
            story=root/'story';path=story/f'episode-{ep}-h3.json';path.write_text(json.dumps(rows,ensure_ascii=False,indent=2))
            (story/f'episode-{ep}-director-original.json').write_text(json.dumps(shots,ensure_ascii=False,indent=2))
            (story/f'episode-{ep}-asset-bindings.json').write_text(json.dumps(bindings,ensure_ascii=False,indent=2))
            # Preserve the archive's complete original director and asset metadata unchanged.
            c.import_script(pid,path)
            with c.db() as conn:
                for aid,(asset,reference) in used.items():
                    conn.execute('INSERT INTO assets VALUES(?,?,?,?,?)',(pid,'reference:'+aid,'reference',reference,c.dumps(asset)))
            checksum=hashlib.sha256()
            with archive.open('rb') as stream:
                for block in iter(lambda:stream.read(8*1024**2),b''):checksum.update(block)
            result={'project':pid,'episode':ep,'shots':len(rows),'duration':cfg['target_duration'],
                    'reference_assets':len(used),'archive_sha256':checksum.hexdigest(),
                    'source_director':director_name,'source_mapping':mapping_name,
                    'postproduction_ui':'preserved in production_layers; not generated as baked-in text',
                    'output_review_required':True}
            (story/'production-package.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            return result
        except Exception:
            # Keep any failed import evidence but do not submit generation tasks.
            (root/'story/import-failed.txt').write_text('Import failed; inspect source and error. No production tasks were submitted.\n')
            raise


def submit_episode(pid,episode,approve_inputs=False):
    result=[]
    for shot in c.list_shots(pid,episode):
        tid=c.submit(pid,episode,shot['id'],'video')
        if approve_inputs:c.review_inputs(tid,'approve','按用户明确制作授权核查导演稿、逐镜资产映射及本地最终质量配置；输出仍须独立审核')
        result.append({'shot':shot['id'],'task':tid})
    (c.project_dir(pid)/'story'/f'episode-{episode}-tasks.json').write_text(json.dumps(result,indent=2))
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive');parser.add_argument('--project',required=True);parser.add_argument('--episode',type=int,default=1)
    parser.add_argument('--name');parser.add_argument('--submit',action='store_true');parser.add_argument('--approve-inputs',action='store_true')
    args=parser.parse_args();result=prepare(args.archive,args.project,args.episode,args.name)
    if args.approve_inputs and not args.submit:parser.error('--approve-inputs requires --submit')
    if args.submit:result['tasks']=submit_episode(args.project,f'{args.episode:02}',args.approve_inputs)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
