"""Package ingestion preserves director/assets, rejects paths, and excludes UI from native references."""
import io
import json
from pathlib import Path
import shutil
import zipfile

from PIL import Image
import pytest

from engine import core
from scripts import import_production_package as package
from engine.scheduler import worker


@pytest.fixture
def root(tmp_path,monkeypatch):
    base=tmp_path/'studio';(base/'configs').mkdir(parents=True)
    shutil.copy(core.ROOT/'configs/project.template.yaml',base/'configs/project.template.yaml')
    monkeypatch.setattr(core,'ROOT',base);monkeypatch.setattr(worker,'ROOT',base);core.init()
    return base


def source_zip(path):
    image=io.BytesIO();Image.new('RGB',(256,256),'grey').save(image,format='PNG')
    director=[{'episode':1,'shot':'01-001','start':0,'end':8,'duration':8,'scene':'现实男主房间',
               'positive':'一个人物在现实房间','action':'整理衣领','camera':'慢推','dialogue':'景川：你好。','sound':'房间底噪'}]
    assets=[{'id':'BG001','file':'图片/BG001.png','name':'卧室','description':'真实家具'},
            {'id':'CH001','file':'图片/CH001.png','name':'景川前期','description':'同一张脸'}]
    mapping=[{'episode':1,'shot':'01-001','production_layers':{'background':['BG001'],'characters':['CH001'],
               'interface':['UI115']},'required_assets':['BG001','CH001','UI115']}]
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('导演/全26集_逐镜数据_V2.json',json.dumps(director))
        z.writestr('资产/资产主表.json',json.dumps(assets))
        z.writestr('资产/逐镜资产映射_制作版.json',json.dumps(mapping))
        for aid in ['BG001','CH001']:z.writestr('资产/图片/'+aid+'.png',image.getvalue())
    return director


def test_prepare_package_and_submit_native_inputs(root,tmp_path):
    path=tmp_path/'package.zip';director=source_zip(path)
    result=package.prepare(path,'production')
    assert result['shots']==1 and result['duration']==8
    shot=core.list_shots('production','01')[0]['spec']
    assert shot['source_director']==director[0]
    assert shot['source_asset_ids']==['BG001','CH001']
    assert len(shot['reference_images'])==2
    assert shot['production_layers']['production_layers']['interface']==['UI115']
    assert core.config('production')['format']=={'aspect_ratio':'9:16','width':768,'height':1344,'fps':24}
    tasks=package.submit_episode('production','01',True)
    assert len(tasks)==1
    assert core.tasks('production')[0]['params']['input_review']['approved']
    with pytest.raises(ValueError,match='already exists'):package.prepare(path,'production')


def test_zip_traversal_is_rejected_before_project_creation(root,tmp_path):
    path=tmp_path/'unsafe.zip'
    with zipfile.ZipFile(path,'w') as z:z.writestr('../outside','payload')
    with pytest.raises(ValueError,match='Unsafe ZIP'):package.prepare(path,'bad')
    assert not core.project_dir('bad').exists()


def test_legacy_chinese_zip_filename_decoding():
    info=zipfile.ZipInfo('资产/定妆.png'.encode('gb18030').decode('cp437'))
    assert package.member_name(info)=='资产/定妆.png'


def test_finite_worker_fails_when_approved_episode_is_blocked(root,monkeypatch):
    core.create_project('film')
    source=root/'director.json';source.write_text(json.dumps([{'shot':'001','prompt':'scene','duration':8}]))
    core.import_script('film',source)
    core.submit('film','01','001','video')
    monkeypatch.setattr('sys.argv',['worker','--gpu','-1','--kinds','video','--project','film','--episode','01','--exit-when-idle'])
    monkeypatch.setattr(worker,'claim',lambda *a,**k:None)
    with pytest.raises(SystemExit,match='blocked/failed'):worker.main()


def test_finite_worker_allows_successful_retry_after_historical_failure(root,monkeypatch):
    core.create_project('film')
    source=root/'director.json';source.write_text(json.dumps([{'shot':'001','prompt':'scene','duration':8}]))
    core.import_script('film',source)
    old=core.submit('film','01','001','video')
    with core.db() as conn:conn.execute("UPDATE tasks SET status='failed' WHERE id=?",(old,))
    new=core.retry_task(old)
    with core.db() as conn:conn.execute("UPDATE tasks SET status='review' WHERE id=?",(new,))
    monkeypatch.setattr('sys.argv',['worker','--gpu','-1','--kinds','video','--project','film','--episode','01','--exit-when-idle'])
    monkeypatch.setattr(worker,'claim',lambda *a,**k:None)
    assert worker.main() is None
