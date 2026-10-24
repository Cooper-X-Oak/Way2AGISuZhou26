"""Synthetic fixtures only; acceptance runs use actual AMap snapshots."""
from collections import Counter
from copy import deepcopy
import httpx
import pytest
from pipeline import l1,l3,pack
from pipeline.amap import AmapAdapter
from pipeline.common import channel_for
from pipeline.profiles import load,parameters
from pipeline.io import read,write
from pipeline.amap_supplement import supplement,rectangles


def population(n=10):
    c=dict(brief=dict(lat=31.28,lon=120.55,radius_m=3000),params=parameters(),profile=load('jiangnan-gongquan'))
    c['l1']={'items':[dict(channel_id='hotpot',priority='P1'),dict(channel_id='restaurant_other',priority='P3')]}
    c['l2']={'items':[dict(zone_id='0:0',rank=1,selected=True)]}
    c['pois']=[dict(id=f'test:{i:03}',name='测试火锅',category='restaurant',lat=31.28,lon=120.55) for i in range(n)]
    return c


def test_quantiles_ties_and_order_independence():
    c=population();result=l3.ranked(c)
    assert Counter(r['grade'] for r in result.values())=={'A':2,'B':3,'C':5}
    assert [result[f'test:{i:03}']['grade'] for i in range(10)]==list('AABBBCCCCC')
    c['pois'].reverse();assert l3.ranked(c)==result
    # Zero distance coefficient creates a score tie while distances differ.
    c['params']['scoring']['component_weights']['distance_decay']=0
    c['pois'][-1]['lon']+=.001
    result=l3.ranked(c)
    assert result['test:000']['grade']=='C' and result['test:002']['grade']=='A'
    assert all(r['next_action']==l3.ACTIONS[r['grade']] for r in result.values())


@pytest.mark.parametrize('n,expected',[(0,''),(1,'A'),(2,'AC'),(3,'ABC'),(7,'AABBCCC')])
def test_quantile_rounding(n,expected):
    assert ''.join(v['grade'] for v in l3.ranked(population(n)).values())==expected


def test_unknown_cap_and_action():
    c=population();c['pois'][0]['name']='未知细分测试'
    # Force unknown to rank first: cap still wins and does not refill A.
    c['params']['priority_weights']['P3']=1
    result=l3.ranked(c)
    assert result['test:000']['grade']=='C'
    assert result['test:000']['next_action']=='首访-了解需求'
    assert Counter(r['grade'] for r in result.values())=={'A':1,'B':3,'C':6}


@pytest.mark.parametrize('word,expected',[('海鲜','seafood'),('火锅','hotpot'),('烧烤','bbq'),('酒楼','banquet'),('夜宵','late_meal')])
def test_type_field_mapping_before_name(word,expected):
    p=AmapAdapter().normalize({'pois':[dict(id='test',name='名称不确定',typecode='050118',type='餐饮服务;中餐厅;'+word,location='120.55,31.28')]},
                             dict(lat=31.28,lon=120.55,coordinate_system='GCJ-02'),3000)[0]
    assert channel_for(p,load('jiangnan-gongquan'))['id']==expected
    assert p['classification_method']=='rules'


def test_capacity_backlog_and_daily_routes():
    c=population(130)
    c['brief'].update(weeks=1,delivery_people=2,planned_visits_day=40,visits_per_person_day=20)
    c['l3']={'items':[dict(poi_id=p['id'],grade='A' if i<60 else 'B' if i<125 else 'C',score=1-i/130) for i,p in enumerate(c['pois'])]}
    days=pack.schedule(c);pending=pack.backlog(c,days)
    scheduled=[pid for d in days for pid in d['poi_ids']]
    assert len(scheduled)==100 and len(pending)==25
    assert all(len(d['poi_ids'])<=10 for d in days)
    assert len(set(scheduled))==100
    assert set(scheduled).isdisjoint(r['poi_id'] for r in pending)
    assert set(scheduled)|{r['poi_id'] for r in pending}=={r['poi_id'] for r in c['l3']['items'] if r['grade'] in ('A','B')}
    assert scheduled[:60]==[f'test:{i:03}' for i in range(60)]


def seed_areas(root):
    from pipeline.amap import AREAS
    for a in AREAS:
        site=dict(lat=31.28,lon=120.55,coordinate_system='GCJ-02')
        directory=root/'data/areas'/a
        write(directory/'site.json',site);write(directory/'pois.json',[])
        write(directory/'coverage.json',dict(site=site,categories={'restaurant':0},channel_counts={},type_coverage={'050100':{'truncated':True}}))


def test_supplement_budget_restart_dedup_and_failure_ledger(tmp_path,monkeypatch):
    seed_areas(tmp_path);monkeypatch.setenv('AMAP_KEY','supplement-test-secret')
    calls=[]
    def handle(request):
        calls.append(request)
        if len(calls)==1:raise httpx.ConnectError('sensitive details')
        return httpx.Response(200,json={'status':'1','pois':[dict(id='same',name='测试',typecode='050100',location='120.55,31.28',tel='drop')]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result=supplement(tmp_path,client,max_calls=5,sleep=lambda _:None)
    assert len(calls)==result['calls']==5
    assert all(row['truncated'] for row in result['areas'].values())
    assert read(tmp_path/'data/raw/amap/usage.json')['tasks']['grading-fix']==5
    manifest=read(result['manifest']);assert manifest['requests'][0]['status']=='failed'
    assert len(read(tmp_path/'data/areas/guanqian/pois.json'))==1
    for p in tmp_path.rglob('*.json'):
        assert 'supplement-test-secret' not in p.read_text(encoding='utf-8') and 'sensitive details' not in p.read_text(encoding='utf-8')
    usage=read(tmp_path/'data/raw/amap/usage.json');usage['tasks']['grading-fix']=149
    write(tmp_path/'data/raw/amap/usage.json',usage)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert supplement(tmp_path,client,sleep=lambda _:None)['calls']==1
        assert supplement(tmp_path,client,sleep=lambda _:None)['calls']==0


def test_supplement_completes_all_cells(tmp_path,monkeypatch):
    seed_areas(tmp_path);monkeypatch.setenv('AMAP_KEY','test-supplement-complete')
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'status':'1','pois':[]}))) as client:
        result=supplement(tmp_path,client,sleep=lambda _:None)
    assert result['calls']==12 and not any(a['truncated'] for a in result['areas'].values())
    assert len(set(rectangles(dict(lat=31.28,lon=120.55))))==4
