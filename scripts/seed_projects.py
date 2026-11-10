"""Create explicitly labelled acceptance drafts; never modify an existing work."""
import copy,json,yaml
from pathlib import Path
from engine.core import ROOT,create_project,config,import_script,init,project_dir
init()
if not project_dir('sky-island').exists():
 c=create_project('sky-island','空岛 · 最小闭环验收')
 c.update(genre='方块世界生存短剧',language='Chinese',format={'aspect_ratio':'9:16','width':768,'height':1344,'fps':24},episodes=1,target_duration=36)
 c['style']={
 'description':'真人写实人物；方块世界保留几何结构，采用写实材质与电影光照。韩漫审美仅指导造型，最终为真人摄影感。8K为审美描述，非原生推理分辨率。负面马赛克不消除方块结构。',
 'portrait_prompt':'超写实真人质感，韩系精致审美，人像精修级皮肤质感，发丝清晰，电影级柔和打光，边缘轮廓光，画面通透干净，无手绘笔触，无漫画线条，高清8K细节，人物比例正常自然。',
 'positive_prefix':'竖屏9:16，超写实真人画风，韩漫精致审美，电影级柔光氛围，人像精修质感，人物写实自然，无漫画笔触，构图完整居中，人物全身完整无裁切。',
 'negative':'卡通，二次元，Q版，厚涂，手绘线条，漫画感，人物变形，五官怪异，画面截断，模糊，低画质，马赛克。'}
 c['characters']={
 'lu-jingchuan':{'name':'陆景川','age':22,'stages':{'early':'白净偏瘦、懂穿搭','later':'随生存劳动逐渐健壮可靠、肤色变小麦色'},'wardrobe':'前期深蓝短夹克、米白T恤、黑色长裤、白色运动鞋','equipment':[]},
 'xu-zhixia':{'name':'许知夏','age':21,'stages':{'early':'冷萌脸、游戏小白','later':'逐渐成长为独立可靠的伙伴'},'wardrobe':'米白开衫、浅蓝T恤、深灰长裤、白色运动鞋','equipment':[]}}
 c['voices']={'lu-jingchuan':{'speaker':'Dylan','source':'Qwen3-TTS官方预设声线','language':'Chinese'},'xu-zhixia':{'speaker':'Serena','source':'Qwen3-TTS官方预设声线','language':'Chinese'}}
 c['scenes']={'room':'现代简洁卧室，书桌靠左，窗户在右','island':'悬浮草方块岛、泥土侧壁、橡树，写实草和岩土纹理，远处云海'}
 c['continuity']=['两人现实中原本互不认识','阶段early的服装保持配置一致','方块保留直角几何，不变成圆滑山地','人物面部和肢体必须人工复核；固定种子不能替代参考图编辑']
 c['models']['image']['min_free_mib']=60000
 (project_dir('sky-island')/'project.yaml').write_text(yaml.safe_dump(c,allow_unicode=True,sort_keys=False))
 man='A photorealistic 22-year-old East Asian man, slim fair face, short neatly styled black hair, navy cropped jacket over ivory T-shirt, black trousers, white sneakers'
 woman='A photorealistic 21-year-old East Asian woman, cool cute oval face, straight shoulder-length black hair, ivory cardigan over light blue T-shirt, dark grey trousers, white sneakers'
 shots=[]
 data=[
 ('001','room',['lu-jingchuan'],man+', standing in a modern bedroom beside a desk at dawn, window on the right, medium wide full body composition, live action cinema','The young man slowly turns from the desk toward the bright window, looks surprised, subtle natural breathing, slow camera push in.','这里怎么突然变亮了？','lu-jingchuan'),
 ('002','island',['lu-jingchuan','xu-zhixia'],man+' and '+woman+', both standing apart on one floating island of cubic grass blocks above clouds, an oak tree behind them, realistic grass and earth textures, wide full body live action shot','The man and woman cautiously look around the floating island, wind gently moves their hair and clothing, slow sideways camera tracking.','你也被传送到这里了？','xu-zhixia'),
 ('003','island',['lu-jingchuan'],man+', crouching at the edge of a cubic grass floating island, holding one brown cubic dirt block with both hands, one empty cubic gap directly in front, realistic block textures','The man lowers the cubic dirt block into the single gap at the edge of the island, releases his hands and pulls his arms back. The placed block remains fixed. One continuous shot.','',''),
 ('004','island',['lu-jingchuan','xu-zhixia'],man+' holding a wooden stick and '+woman+' behind him, on a cubic grass island at dusk, one large eight-legged black spider approaches from the foreground, cinematic action composition','The spider crawls two steps toward the man. He takes one defensive step forward and swings the wooden stick once. The woman steps back. Keep all bodies coherent. One short exchange, no cuts.','',''),
 ('005','island',['lu-jingchuan'],man+', standing on the floating cubic grass island, looking toward the woman outside the frame, soft morning backlight, live action medium shot','The man faces the woman, makes a small reassuring open hand gesture and relaxes his shoulders. Natural subtle face motion, static camera.','先别怕，我们一起想办法。','lu-jingchuan'),
 ('006','island',['xu-zhixia'],woman+', standing on the same floating cubic grass island, looking toward the man outside the frame, soft morning backlight, live action medium shot','The woman looks toward the man, hesitates then nods once, her tense posture softens. Natural subtle face motion, static camera.','好，那我负责守住这里。','xu-zhixia')]
 for sid,scene,chars,prompt,motion,line,speaker in data:
  shots.append({'project_id':'sky-island','episode_id':'01','shot_id':sid,'scene':scene,'characters':chars,'character_stage':'early','reference_images':[],'equipment':['wooden-stick'] if sid=='004' else [],'prompt':prompt,'negative_prompt':'extra limbs, duplicate people, malformed hands','action_prompt':motion,'dialogue':line,'speaker':speaker,'target_duration':6,'seed':20261108+int(sid)})
 p=project_dir('sky-island')/'story/acceptance-draft.json';p.write_text(json.dumps(shots,ensure_ascii=False,indent=2));import_script('sky-island',p)
 refs=[]
 for sid,ch,text in [('male','lu-jingchuan',man),('female','xu-zhixia',woman)]:
  refs.append({'episode_id':'references','shot_id':sid,'characters':[ch],'prompt':c['style']['portrait_prompt']+' '+text+', full body character reference, neutral grey studio backdrop, front three quarter view, hands relaxed, no text','target_duration':1,'seed':20261120 if sid=='male' else 20261121})
 p=project_dir('sky-island')/'story/reference-draft.json';p.write_text(json.dumps(refs,ensure_ascii=False,indent=2));import_script('sky-island',p)
 (project_dir('sky-island')/'story/NOTE.md').write_text('这些镜头是部署验收草稿，由本次任务新建，不是用户已有导演稿。仅验证能力，不代表完整分集、剧情定稿或商业成片。\n')
if not project_dir('harbor-tea').exists():
 c=create_project('harbor-tea','港口茶馆 · 横屏广告隔离验证')
 c.update(genre='茶饮产品广告',language='English',format={'aspect_ratio':'16:9','width':1344,'height':768,'fps':24},episodes=1,target_duration=6)
 c['style']={'description':'水彩纸质感、柔和暖色','positive_prefix':'Watercolor illustration, warm amber tones, textured paper, wide horizontal composition','negative':'photorealistic people, text, watermark'}
 (project_dir('harbor-tea')/'project.yaml').write_text(yaml.safe_dump(c,allow_unicode=True,sort_keys=False))
 p=project_dir('harbor-tea')/'story/acceptance-draft.csv'
 p.write_text('episode,shot,duration,scene,positive,motion_prompt,negative\n01,001,6,harbor,"A white ceramic teacup on a wooden cafe table overlooking a quiet harbor at sunrise, watercolor painting, no people","Soft steam curls upward from the teacup while boats gently sway in the harbor. Slow camera push in. Continuous watercolor animation.",text\n')
 import_script('harbor-tea',p)
print('Projects ready. Acceptance drafts only.')
