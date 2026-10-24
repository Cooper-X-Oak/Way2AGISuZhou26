"""Synthetic POIs stay inside pytest; live acceptance is in immutable runs."""
from copy import deepcopy
import json
import httpx
import pytest
from pipeline import l2c,l3p,l4,pack
from pipeline.engine import execute,STAGES
from pipeline.profiles import load,parameters
from pipeline.judge import Judge
from pipeline.io import read,write
from pipeline.market_capture import capture,queries,normalize
from pathlib import Path


@pytest.fixture
def contexts():
    site=dict(lat=31.28,lon=120.55,coordinate_system='GCJ-02',location_note='synthetic fixture')
    c=dict(request='周边3公里，2个地推，做4周',site=site,pois=[dict(id=f'test:{i}',name='测试火锅',category='restaurant',**site) for i in range(10)],
           profile=load('jiangnan-gongquan'),params=parameters(),judge=Judge(True),coverage=dict(site=site,radius_m=3000,source='synthetic fixture'),auto_approve=True)
    c['market']=dict(competitors=[dict(id='test:competitor',brands=['农夫山泉'],**site)],partners=[dict(id='test:partner',name='测试酒水批发',tags={},**site)])
    c.update(checks={},approvals={'L2':'auto (demo)','L3':'auto (demo)'},source_documents={c['profile']['source']:Path(c['profile']['source']).read_text(encoding='utf-8')})
    result={}
    for name,module in STAGES:
        result[name]=deepcopy(c);a=module.run(c);report=module.inspect(a,c)
        assert report['passed'],report
        c['brief' if name=='L0' else name.lower()]=a;c['checks'][name]=report
    return result


def test_competition_dedup_thresholds_and_enums(contexts):
    c=contexts['L2C'];c['market']['competitors']*=2
    a=l2c.run(c);assert a['items'][0]['counts']['农夫山泉']==1
    assert [l2c.intensity(n,c) for n in (0,1,2,4,5)]==['低','低','中','中','高']
    a['items'][0]['counts']['农夫山泉']=7
    assert any(v.code=='competition_recount' for v in l2c.check(a,c))
    a=l2c.run(c);a['items'][0]['strategy']='挤出'
    assert any(v.code=='schema' for v in l2c.check(a,c))
    a=l2c.run(c);a['items'][0]['reason']='市场份额很低'
    assert any(v.code=='competition_reason' for v in l2c.check(a,c))


def test_coverage_boundary_and_tamper(contexts):
    c=contexts['L3P'];p=c['market']['partners'][0]
    assert len(l3p.coverage(p,c))==5
    far={**p,'lat':p['lat']+.04};assert l3p.coverage(far,c)==[]
    a=l3p.run(c);a['items'][0]['coverage_count']+=1
    assert any(v.code=='partner_recount' for v in l3p.check(a,c))
    a=l3p.run(c);a['items'][0]['partner_id']='invented'
    assert any(v.code=='item_set' for v in l3p.check(a,c))


def test_assignment_priority_distance_ties_and_gap(contexts):
    c=contexts['PACK'];p=c['market']['partners'][0]
    c['market']['partners'] += [{**p,'id':'test:a','lon':p['lon']+.001},{**p,'id':'test:b'}]
    c['l3p']=l3p.run(c)
    assert {r['partner_id'] for r in l3p.assignments(c).values()}=={'test:b'}
    # Direct allocator unit test: recommendation dominates distance.
    for r in c['l3p']['items']:r['recommendation']='优先' if r['partner_id']=='test:a' else '备选'
    assert {r['partner_id'] for r in l3p.assignments(c).values()}=={'test:a'}
    c['l3p']['items']=[]
    assert l3p.gaps(c)['terminal_count']==5 and l3p.gaps(c)['zone_ids']==['0:0']
    assert {r['status'] for r in l3p.assignments(c).values()}=={'需新招经销商'}


def test_strategy_pitch_all_three_and_naming(contexts):
    c=contexts['L4']
    for strategy in l4.ANGLES:
        for r in c['l2c']['items']:r['strategy']=strategy
        a=l4.run(c)
        assert all(l4.ANGLES[strategy] in r['pitch'] for r in a['items'] if r['target'].startswith('test:'))
        assert not l4.check(a,c)
    a['items'][0]['pitch']+='农夫山泉'
    assert any(v.rule_id=='competitor-naming' for v in l4.check(a,c))
    a=l4.run(c);a['items'][0]['pitch']+='别家水质差'
    assert any(v.rule_id=='competitor-denigration' for v in l4.check(a,c))


def test_trace_supply_and_recheck(contexts):
    c=contexts['PACK'];a=pack.run(c)
    assert len(a['supply_assignments'])==5 and a['recruitment_gaps']['terminal_count']==0
    chain=a['trace']['test:0']
    assert chain['competition']['data']['intensity']=='低'
    assert chain['supply']['partner']['data']['partner_id']=='test:partner'
    a['trace']['test:0']['supply']['data']['partner_id']='invented'
    assert any(v.code=='trace' for v in pack.check(a,c))
    a=pack.run(c);a['recruitment_gaps']['terminal_count']=1
    assert any(v.code=='supply_assignment' for v in pack.check(a,c))


def test_pending_and_seeded_retry(contexts,tmp_path,monkeypatch):
    c=contexts['L0'];c['auto_approve']=False
    monkeypatch.setattr('builtins.input',lambda _:'no')
    directory,state=execute(c,tmp_path)
    assert state['stopped_at']=='L2'
    assert read(directory/'pending/L2-r1.json')['competition_summary'][0]['counts']['农夫山泉']==1
    c['auto_approve']=True;c['demo_replay']='compliance'
    directory,state=execute(c,tmp_path);assert state['status']=='completed'
    rows=[json.loads(line) for line in (directory/'ledger.jsonl').read_text(encoding='utf-8').splitlines()]
    rows=[r for r in rows if r.get('stage')=='L4']
    assert [r['status'] for r in rows]==['failed','passed']
    assert rows[0]['seeded_for_demo'] and any(v['rule_id']=='competitor-naming' for v in rows[0]['violations'])


def test_capture_config_meter_restart_privacy_and_radius(tmp_path,monkeypatch):
    from pipeline.amap import AREAS
    from pipeline.market_capture import CAP
    for area in AREAS:
        write(tmp_path/'data/areas'/area/'site.json',dict(lat=31.28,lon=120.55,coordinate_system='GCJ-02'))
        write(tmp_path/'data/areas'/area/'coverage.json',dict(radius_m=3000))
    q=queries(load('jiangnan-gongquan'),parameters())
    assert {'酒水批发','烟酒副食批发','餐饮供应链','食材配送','洞庭山泉'}<={r['keyword'] for r in q}
    assert any(r['types']=='060400' for r in q)
    monkeypatch.setenv('AMAP_KEY','test-market-credential')
    called=[]
    def handle(request):
        called.append(request)
        assert int(request.url.params['radius'])<=5000
        if len(called)==1:raise httpx.ConnectError('private exception')
        return httpx.Response(200,json={'status':'1','pois':[dict(id='fixture',name='农夫山泉测试送水点',location='120.55,31.28',tel='private-contact')]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result=capture(tmp_path,5,client,sleep=lambda _:None)
        assert result['calls']==5
        assert all(a['coverage']['truncated'] for a in result['areas'].values())
        usage=read(tmp_path/'data/raw/amap/usage.json');usage['tasks']['competition-partners']=CAP-1
        write(tmp_path/'data/raw/amap/usage.json',usage)
        assert capture(tmp_path,150,client,sleep=lambda _:None)['calls']==1
        assert capture(tmp_path,150,client,sleep=lambda _:None)['calls']==0
    for path in tmp_path.rglob('*.json'):
        text=path.read_text(encoding='utf-8')
        assert not any(s in text for s in ('test-market-credential','private-contact','private exception'))
    assert normalize(dict(id='far',location='0,0'),dict(lat=31.28,lon=120.55),5000) is None


def test_new_stages_retry_limit_and_ledger(contexts,tmp_path,monkeypatch):
    for stage,module,field in [('L2C',l2c,'intensity'),('L3P',l3p,'recommendation')]:
        original=module.run
        def bad(c,original=original,field=field):
            a=original(c);a['items'][0][field]='invalid';return a
        with monkeypatch.context() as m:
            m.setattr(module,'run',bad)
            directory,state=execute(contexts['L0'],tmp_path)
        assert state['stopped_at']==stage
        rows=[json.loads(line) for line in (directory/'ledger.jsonl').read_text(encoding='utf-8').splitlines()]
        attempts=[r for r in rows if r.get('stage')==stage]
        assert len(attempts)==3 and all(len(read(directory/r['check_path'])['layers'])==5 for r in attempts)
