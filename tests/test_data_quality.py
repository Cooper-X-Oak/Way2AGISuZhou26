"""Issue 12: synthetic boundary fixtures; cached merchant regression stays offline."""
import pytest
from pipeline import l0, l3, l3p
from pipeline.common import channel_for
from pipeline.io import read
from pipeline.judge import Judge
from pipeline.profiles import load, parameters


@pytest.mark.parametrize('name,code,kind,expected', [
    ('测试店', '050107', '餐饮服务;中餐厅;江浙菜', 'banquet'),
    ('测试店', '050100', '中餐厅;苏帮菜', 'banquet'),
    ('测试本帮菜', '050100', '中餐厅', 'banquet'),
    ('测试淮扬菜', '050100', '中餐厅', 'banquet'),
    ('得月楼(观前店)', '050100', '中餐厅', 'banquet'),
    ('得月楼（测试分店）', '050100', '中餐厅', 'banquet'),
    ('得月楼旁测试店', '050100', '中餐厅', 'restaurant_other'),
    ('测试楼', '050100', '中餐厅', 'restaurant_other'),
    ('测试店', '050118', '特色/地方风味餐厅', 'restaurant_other'),
    ('测试河鲜', '050100', '中餐厅', 'seafood'),
    ('测试苏帮菜', '050119', '海鲜酒楼', 'seafood'),
    ('测试店', '050119', '中餐厅', 'seafood'),
])
def test_dining_mapping(name, code, kind, expected):
    poi=dict(name=name, source='AMap', category='restaurant', tags=dict(typecode=code,type=kind))
    assert channel_for(poi,load('jiangnan-gongquan'))['id']==expected


@pytest.mark.parametrize('name,code,kind', [
    ('百发超市(观凤数码广场店)', '061210', '烟酒专卖店'),
    ('测试酒水便利店', '061210', '烟酒专卖店'),
    ('测试酒水', '060100', '购物商场'),
    ('测试酒水', '060200', ''),
    ('测试酒水', '060400', ''),
    ('测试酒水', '061210', '超级市场;超市'),
    ('洞庭山泉超市', '061210', ''),
])
def test_retail_cannot_be_supplier(name,code,kind):
    c={'params':parameters()}
    p=dict(name=name,tags=dict(typecode=code,type=kind))
    assert l3p.kind(p,c)=='其他'
    assert '暂不推荐' in l3p.reason(p,'其他',[],c)


@pytest.mark.parametrize('area',['shishan','guanqian','ligongdi'])
def test_site_note_from_coverage(area):
    site=read(f'data/areas/{area}/coverage.json')['site']
    c=dict(site=site,profile=load('jiangnan-gongquan'),params=parameters(),judge=Judge(True),
           request='周边3公里，2个地推，做4周',pois=[],auto_approve=True)
    artifact=l0.run(c)
    assert artifact['site_note']==site['site_note']
    assert not l0.check(artifact,c)
    artifact['site_note']='错误站点备注'
    assert l0.check(artifact,c)


def test_readable_terminal_reason_and_evidence():
    c=dict(profile=load('jiangnan-gongquan'),brief=dict(lat=31.28,lon=120.55))
    p=dict(id='test:opaque',lat=31.28,lon=120.55)
    ch=dict(channel_id='hotpot',priority='P1')
    z=dict(zone_id='-1:0',rank=1,tier='社区级',type='混合')
    text=l3.reason(p,ch,z,c)
    assert text=='火锅，属P1业态；位于商圈1（社区级混合），距参考中心0米。'
    assert 'test:opaque' not in text and 'hotpot' not in text
