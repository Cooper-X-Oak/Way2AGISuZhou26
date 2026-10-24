"""v2 contracts: synthetic test POIs never enter data/ or acceptance runs."""
from copy import deepcopy
import json
import random
from pathlib import Path
import pytest
from pipeline import l0,l1,l2,l3,l4,pack
from pipeline.engine import execute,STAGES
from pipeline.io import read
from pipeline.judge import Judge
from pipeline.profiles import load,parameters
from pipeline.quality import LAYERS
from pipeline.geo import distance,grid_id
from pipeline.sources import OSMAdapter,category
from pipeline.common import channel_for


@pytest.fixture
def raw():
    site={'lat':31.2834209,'lon':120.5556663,'location_note':'synthetic fixture'}
    def p(i,dx,name='测试餐饮',cat='restaurant'):
        return dict(id=f'test:{i}',name=name,category=cat,lat=site['lat'],lon=site['lon']+dx)
    return dict(request='苏州高新区周边3公里，2个地推，做4周',site=site,profile=load('jiangnan-gongquan'),params=parameters(),
                judge=Judge(True),pois=[p(1,.0001,'测试海鲜'),p(2,.0002,'测试火锅')]+[p(i+3,dx) for i,dx in enumerate((.006,.012,.018,.024,.027))]+[p(8,.003,'测试公司','office'),p(9,.004,'测试健身','gym'),p(10,.0003,'测试火锅'),p(11,.0004,'测试火锅')],
                coverage={'site':site,'radius_m':3000,'source':'synthetic test fixture'},auto_approve=True)


@pytest.fixture
def contexts(raw):
    c=deepcopy({k:v for k,v in raw.items() if k!='judge'});c['judge']=raw['judge']
    c.update(checks={},approvals={'L2':'auto (demo)','L3':'auto (demo)'},source_documents={raw['profile']['source']:Path(raw['profile']['source']).read_text(encoding='utf-8')})
    result={}
    for name,module in STAGES:
        result[name]=deepcopy(c)
        a=module.run(c);r=module.inspect(a,c)
        assert r['passed'],r
        c['brief' if name=='L0' else name.lower()]=a;c['checks'][name]=r
    return result


@pytest.mark.parametrize('name,module',STAGES)
def test_five_layers_pass(name,module,contexts):
    c=contexts[name];a=module.run(c)
    a['confidence']=1.0
    for row in a.get('items',[]):
        if name!='PACK':row['confidence']=1.0
    # PACK preserves upstream terminal confidence; use threshold 0 for this positive quality case.
    c['params']['confidence_threshold']=0.0
    if name=='PACK':a=module.run(c)
    report=module.inspect(a,c)
    assert [r['layer'] for r in report['layers']]==list(LAYERS)
    assert all(r['status']=='passed' for r in report['layers']),report


@pytest.mark.parametrize('name,module',STAGES)
@pytest.mark.parametrize('layer',LAYERS)
def test_each_layer_negative(name,module,layer,contexts):
    c=contexts[name];a=module.run(c)
    if layer=='structure':a['unexpected']='reject me'
    elif layer=='references':a['evidence']=['L9:invented']
    elif layer=='human_gate':c['approvals'][name]='pretend human'
    elif layer=='judgment_quality':a['confidence']=0.1
    elif name=='L0':a['planned_visits_day']=9999
    elif name=='L1':a['items'][0]['commercial_conditions']['price_band']='编造价格'
    elif name=='L2':a['items'][0]['counts'][next(iter(a['items'][0]['counts']))]+=1
    elif name=='L2C':a['items'][0]['intensity']='高'
    elif name=='L3P':
        # Add a synthetic partner to exercise arithmetic, not an empty happy path.
        c['market']={'partners':[dict(id='test:partner',name='测试酒水批发',lat=c['brief']['lat'],lon=c['brief']['lon'],tags={})]}
        a=module.run(c);a['items'][0]['coverage_count']+=1
    elif name=='L3':a['items'][0]['score']=0.01
    elif name=='L4':a['items'][0]['pitch']+='护血管'
    elif name=='PACK':a['days'][0]['route_distance_m']+=1
    report=module.inspect(a,c);row=next(r for r in report['layers'] if r['layer']==layer)
    if layer=='judgment_quality':
        assert row['status']=='needs_review' and report['review_queue']
        assert report['passed'] # confidence is not an automatic truth verdict
    else:assert row['status']=='failed' and not report['passed'],report


@pytest.mark.parametrize('name,module',[('L2',l2),('L3',l3)])
def test_required_human_gate(name,module,contexts):
    c=contexts[name];c['approvals'].pop(name);a=module.run(c)
    assert any(v.code=='approval_pending' for v in module.check(a,c))
    c['auto_approve']=False;c['approvals'][name]='auto (demo)'
    assert any(v.code=='approval' for v in module.check(a,c))
    c['approvals'][name]='human'
    assert not module.check(a,c)


def test_missing_l0_and_radius(raw):
    raw['request']='想做渠道拓展';raw['no_defaults']=True
    a=l0.run(raw);assert a['questions'] and l0.check(a,raw)
    raw['request']='周边3.1公里，2个地推，做4周'
    assert any(v.code=='radius' for v in l0.check(l0.run(raw),raw))


@pytest.mark.parametrize('name,module',STAGES[1:-1])
@pytest.mark.parametrize('mutation',['missing','duplicate','nan','source','enum','contact'])
def test_strict_contracts(name,module,mutation,contexts):
    c=contexts[name]
    if name=='L3P':c['market']={'partners':[dict(id='test:partner',name='测试酒水批发',lat=c['brief']['lat'],lon=c['brief']['lon'],tags={})]}
    a=module.run(c);r=a['items'][0]
    if mutation=='missing':a['items']=[]
    if mutation=='duplicate':a['items'].append(deepcopy(r))
    if mutation=='nan':r['confidence']=float('nan')
    if mutation=='source':r['judged_by']='llm'
    if mutation=='enum':r[{'L1':'priority','L2':'type','L2C':'strategy','L3':'grade','L3P':'type','L4':'target'}[name]]='invented'
    if mutation=='contact':r['phone']='synthetic unwanted field'
    assert module.check(a,c)


def test_a1_scene_coverage_and_material_link(contexts):
    c=contexts['L1'];a=l1.run(c);a['excluded_scenes']=[]
    assert any(v.code=='scene_coverage' for v in l1.check(a,c))
    c=contexts['L4'];a=l4.run(c);a['items'][0]['selling_point_ids']=['fake']
    assert l4.check(a,c)
    # Valid profile ID but not permitted by this channel's A1 mapping.
    c['l1']['items'][0]['selling_point_ids']=['natural-soda']
    a['items'][0]['selling_point_ids']=['low-sodium']
    assert any(v.code=='point' for v in l4.check(a,c))


def test_unknown_channel_priority_cap(contexts):
    c=contexts['L1'];a=l1.run(c)
    row=next(r for r in a['items'] if r['channel_id']=='restaurant_other')
    assert row['priority']=='P3'
    row['priority']='P2'
    assert any(v.code=='unknown_channel_cap' for v in l1.check(a,c))


def test_pack_backlog_tampering(contexts):
    c=contexts['PACK'];a=pack.run(c)
    a['backlog']=[deepcopy(a['items'][0])]
    assert any(v.code=='backlog' for v in pack.check(a,c))


def test_counts_rank_selection_and_coverage(contexts):
    c=contexts['L2'];a=l2.run(c)
    a['items'][0]['rank']=99
    assert any(v.code=='recount' for v in l2.check(a,c))
    a=l2.run(c);a['items'][0]['selected']=False
    assert any(v.code=='selection' for v in l2.check(a,c))
    c['coverage']['radius_m']=2000
    assert any(v.code=='source_coverage' for v in l2.check(l2.run(c),c))


def test_score_hand_calculation_and_actions(contexts):
    c=contexts['L3'];p,ch,z=l3.candidates(c)[0]
    p={**p,'lat':c['brief']['lat'],'lon':c['brief']['lon']};z={**z,'rank':1}
    r=l3.calculated(p,ch,z,c)
    assert r['score']==1.0
    assert set(r)=={'score','score_components'} # grades require the full run population
    a=l3.run(c);a['items'][0]['next_action']='暂缓'
    assert l3.check(a,c)
    a=l3.run(c);a['items'][0]['reason']+='客流每天一千人'
    assert any(v.code=='reason' for v in l3.check(a,c))


@pytest.mark.parametrize('field',['pitch','table_card','open'])
def test_compliance_hits_have_offsets(field,contexts):
    c=contexts['L4'];a=l4.run(c)
    if field=='open':a['items'][0][field].append('来源：测试最解腻护血管')
    else:a['items'][0][field]+='最解腻护血管'
    hits=[v for v in l4.check(a,c) if v.code=='compliance']
    assert {'最解腻','护血管'}<={v.matched_term for v in hits}
    assert all(v.rule_id and v.offset>=0 and field in v.path for v in hits)


def ledger(directory):return [json.loads(r) for r in (directory/'ledger.jsonl').read_text(encoding='utf-8').splitlines()]


def test_compliance_replay_and_feedback(raw,tmp_path):
    raw['demo_replay']='compliance'
    directory,state=execute(raw,tmp_path)
    assert state['status']=='completed'
    rows=[r for r in ledger(directory) if r.get('stage')=='L4']
    assert [r['status'] for r in rows]==['failed','passed']
    assert rows[0]['seeded_for_demo'] and not rows[1]['seeded_for_demo']
    assert {'最解腻','护血管'}<={v['matched_term'] for v in rows[0]['violations']}
    assert all(len(read(directory/r['check_path'])['layers'])==5 for r in rows)


def test_backtrack_replay_preserves_threshold_and_history(raw,tmp_path):
    raw['demo_replay']='backtrack'
    directory,state=execute(raw,tmp_path)
    # A single selected grid cannot meet its prior A+B count + 1; fail honestly.
    assert state['status']=='blocked' and state['stopped_at']=='L2',state
    events=ledger(directory);backs=[r for r in events if r.get('event')=='backtrack']
    assert 1<=len(backs)<=2 and all(r['seeded_for_demo'] for r in backs)
    assert read(directory/'params.json')['min_terminals_per_zone']==3
    assert len(list((directory/'attempts').glob('L2-*.json')))==4
    assert [r['status'] for r in events if r.get('stage')=='L2']==['passed','failed','failed','failed']
    failed=[r for r in events if r.get('stage')=='L3' and r.get('status')=='failed']
    assert failed and all({v['code'] for v in r['violations']}=={'zone_shortage'} for r in failed)
    assert all(r['approved_by']=='auto (demo)' for r in events if r.get('stage') in ('L2','L3') and 'attempt' in r)


def test_backtrack_limit(raw,tmp_path):
    # Twelve real-schema synthetic grid candidates, one per grid, threshold fixed at two.
    b=raw['site'];raw['pois']=[]
    for x in (-.012,-.006,0,.006):
        for y in (-.006,0,.006):
            raw['pois'].append(dict(id=f'test:{x}:{y}',name='测试火锅',category='restaurant',lat=b['lat']+y,lon=b['lon']+x))
    raw['params']['min_terminals_per_zone']=2
    directory,state=execute(raw,tmp_path)
    assert state['status']=='blocked' and state['stopped_at']=='L2' and state['backtracks']==2
    backs=[r for r in ledger(directory) if r.get('event')=='backtrack']
    assert len(backs)==3 and backs[-1]['status']=='limit_exceeded'
    assert not any(r['seeded_for_demo'] for r in backs)
    assert not (directory/'pack.json').exists()


def test_natural_backtrack_not_seeded(raw,tmp_path):
    for p in raw['pois']:
        if p['name']=='测试餐饮':p['name']='测试火锅'
    raw['params']['min_terminals_per_zone']=2
    directory,state=execute(raw,tmp_path)
    assert state['status']=='completed' and state['natural_backtrack']
    assert not any(r.get('seeded_for_demo') for r in ledger(directory))


def test_three_attempts_no_fabrication(raw,tmp_path):
    raw['request']=raw['request'].replace('3公里','4公里')
    directory,state=execute(raw,tmp_path)
    assert state['status']=='blocked' and state['stopped_at']=='L0'
    assert len(ledger(directory))==3 and not (directory/'channels.json').exists()


def test_retry_receives_violations_and_candidate(raw,tmp_path,monkeypatch):
    original=l1.run;seen=[]
    def run(c):
        seen.append((c.get('previous'),c.get('violations')))
        a=original(c)
        if len(seen)==1:a['items'][0]['priority']='invalid'
        return a
    monkeypatch.setattr(l1,'run',run)
    directory,state=execute(raw,tmp_path)
    assert state['status']=='completed' and seen[1][0] and seen[1][1]
    assert read(directory/'attempts/L1-r1-1.json')['artifact']['items'][0]['priority']=='invalid'


def test_exception_retains_candidate(raw,tmp_path,monkeypatch):
    def fail(a,c):raise RuntimeError('do not persist secret provider details')
    monkeypatch.setattr(l1,'inspect',fail)
    directory,state=execute(raw,tmp_path)
    assert state['stopped_at']=='L1' and read(directory/'attempts/L1-r1-3.json')['artifact']
    assert 'secret provider' not in (directory/'ledger.jsonl').read_text(encoding='utf-8')


def test_interactive_accept_and_decline(raw,tmp_path,monkeypatch):
    raw['auto_approve']=False
    monkeypatch.setattr('builtins.input',lambda prompt:'no')
    directory,state=execute(raw,tmp_path)
    assert state['stopped_at']=='L2' and (directory/'pending/L2-r1.json').exists()
    monkeypatch.setattr('builtins.input',lambda prompt:'yes')
    directory,state=execute(raw,tmp_path)
    assert state['status']=='completed'
    assert all(r['approved_by']=='human' for r in ledger(directory) if r.get('stage') in ('L2','L3'))


def test_trace_random_three_and_tampering(raw,tmp_path,contexts):
    directory,state=execute(raw,tmp_path);assert state['status']=='completed'
    chains=read(directory/'trace.json');snapshot=read(directory/'inputs.json')
    for pid in random.Random(42).sample(sorted(chains),3):
        chain=chains[pid]
        for key in ('terminal','score_components','zone','channel'):
            assert chain[key]['judged_by'] and chain[key]['check']['passed']
        for row in chain['scenes']+chain['selling_points']:
            src=row['source'];content=snapshot['source_documents'][src['file']]
            assert content[src['offset']:src['offset']+len(src['quote'])]==src['quote']
            assert row['check']['passed'] and src['check']['passed']
    c=contexts['PACK'];a=pack.run(c);next(iter(a['trace'].values()))['selling_points']=[]
    assert any(v.code=='trace' for v in pack.check(a,c))


def test_pack_capacity_and_upstream_recheck(contexts):
    c=contexts['PACK'];c['brief']['weeks']=1;c['brief']['planned_visits_day']=1
    a=pack.run(c)
    assert all(len(d['poi_ids'])<=1 for d in a['days'])
    assert len({p for d in a['days'] for p in d['poi_ids']})==sum(len(d['poi_ids']) for d in a['days'])
    c['l4']['items'][0]['pitch']+='护血管';a=pack.run(c)
    assert any(v.code=='upstream_check' for v in pack.check(a,c))


def test_profile_swap_and_zero_pois(raw,tmp_path):
    raw['profile']=load('xinxian-huoquan');directory,state=execute(raw,tmp_path)
    assert state['status']=='completed'
    assert {t['poi_id'] for t in read(directory/'terminals.json')['items']}=={'test:8','test:9'}
    raw['pois']=[];directory,state=execute(raw,tmp_path)
    assert state['status']=='blocked' and state['stopped_at']=='L2'


def test_source_normalizes_both_shapes_and_geometry(raw):
    s=raw['site'];adapter=OSMAdapter()
    p={'type':'node','id':123,'lat':s['lat'],'lon':s['lon'],'tags':{'office':'company'}}
    rows=adapter.normalize({'elements':[p,p,{**p,'id':456,'lat':0}]},s,3000)
    converted={'type':'item','id':123,'tags':{'_osm_type':'node','_lat':str(s['lat']),'_lon':str(s['lon']),'office':'company'}}
    assert adapter.normalize({'elements':[converted]},s,3000)==rows
    assert len(rows)==1 and rows[0]['distance_m']==0
    assert 111000<distance(0,0,1,0)<112000
    assert grid_id({**s,'lon':s['lon']-.001},s).startswith('-1:')
    assert category({'amenity':'cafe','name':'茶馆'})=='tea'


def test_no_llm_fallback(monkeypatch):
    for key in ('LLM_BASE_URL','LLM_API_KEY','LLM_MODEL'):monkeypatch.delenv(key,raising=False)
    with pytest.raises(RuntimeError):Judge()

def test_cached_osm_is_first_and_no_amap_without_key(monkeypatch):
    from pipeline.__main__ import prepare_data
    import pipeline.amap
    monkeypatch.delenv('AMAP_KEY',raising=False)
    monkeypatch.setattr(pipeline.amap,'fetch_amap',lambda **kwargs:pytest.fail('No key, no AMap'))
    pois,coverage=prepare_data()
    assert pois and all(p['source']=='OpenStreetMap' for p in pois)
    assert coverage['source_order']==['OpenStreetMap'] and coverage['amap_calls']==0
    assert coverage['total']==len(pois)


def test_llm_metadata_and_code_arithmetic(contexts):
    # Mock judgment only; not evidence of a live LLM result.
    class MockJudge:
        label='llm:synthetic-model'
        def decide(self,stage,payload,rules):
            self.last=payload
            return rules()
    for name,module in STAGES:
        if name=='PACK':continue
        c=contexts[name];c['judge']=MockJudge()
        a=module.run(c)
        assert a['judged_by']=='llm' and not module.check(a,c)
        assert 'output_contract' in c['judge'].last
        if name=='L3':
            a['items'][0]['score']-=.01
            assert any(v.code=='score' for v in module.check(a,c))


@pytest.mark.parametrize('claim',['售价3元','毛利30%','首单买一送一','免费赠桌牌','售价三元'])
def test_material_rejects_invented_commercial_claims(claim,contexts):
    c=contexts['L4'];a=l4.run(c);a['items'][0]['pitch']+=claim
    assert any(v.code=='commercial_claim' for v in l4.check(a,c))
