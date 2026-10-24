"""AMap transport tests are synthetic, not live capture evidence."""
import json
import httpx
import pytest
from pipeline.amap import AmapAdapter, basic_payload, fetch_amap, wgs_to_gcj, gcj_to_wgs
from pipeline.io import write, read


def test_roundtrip_and_normalization():
    site={'lat':31.2834209,'lon':120.5556663}
    lat,lon=wgs_to_gcj(site['lat'],site['lon'])
    assert abs(lon-site['lon'])>0.001
    assert gcj_to_wgs(lat,lon)==pytest.approx((site['lat'],site['lon']),abs=1e-7)
    p={'id':'fixture','typecode':'050119','name':'test fixture','location':f'{lon},{lat}','tel':'synthetic-not-retained'}
    rows=AmapAdapter().normalize({'pois':[p,p]},site,3000)
    assert len(rows)==1 and rows[0]['restaurant_types']==['海鲜']
    assert rows[0]['coordinate_system']=='WGS84' and rows[0]['distance_m']==0
    assert 'tel' not in json.dumps(rows)
    assert not AmapAdapter().normalize({'pois':[{**p,'location':'0,0'}]},site,3000)


@pytest.mark.parametrize('field',['tel','mobile','email','business','key'])
def test_unrequested_private_fields_dropped_before_save(field):
    assert 'sensitive' not in json.dumps(basic_payload({'pois':[{'id':'test',field:'sensitive'}]}))


def source_root(tmp_path):
    site={'lat':31.2834209,'lon':120.5556663}
    write(tmp_path/'data/site.json',site)
    write(tmp_path/'data/pois.json',[{'original':'preserve-on-failure'}])
    return site


def test_pagination_and_no_key_on_disk(tmp_path,monkeypatch):
    site=source_root(tmp_path)
    monkeypatch.setenv('AMAP_KEY','synthetic-test-only-secret')
    lat,lon=wgs_to_gcj(site['lat'],site['lon'])
    calls=[]
    def handler(request):
        q=request.url.params
        calls.append(int(q['page_num']))
        assert 'show_fields' not in q and q['key']=='synthetic-test-only-secret'
        n=25 if q['page_num']=='1' else 1
        return httpx.Response(200,json={'status':'1','count':str(n),'pois':[{'id':f"{q['page_num']}-{i}",'typecode':'050117','location':f'{lon},{lat}','name':None} for i in range(n)]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=fetch_amap(tmp_path,client,type_codes=('050117',),sleep=lambda _:None)
    assert calls==[1,2] and result['poi_count']==26
    assert read(tmp_path/'data/coverage.json')['channel_counts']['jiangnan-gongquan']['hotpot']==26
    assert all(p['name'] is None and p['fetched_at'] for p in read(tmp_path/'data/pois.json'))
    for p in tmp_path.rglob('*.json'):
        assert 'synthetic-test-only-secret' not in p.read_text(encoding='utf-8')


@pytest.mark.parametrize('mode',['error','network'])
def test_failed_fetch_preserves_original(tmp_path,monkeypatch,mode):
    site=source_root(tmp_path)
    monkeypatch.setenv('AMAP_KEY','synthetic-test-only-secret')
    def handler(request):
        if mode=='network':raise httpx.ConnectError('sensitive URL',request=request)
        if mode=='error':return httpx.Response(200,json={'status':'0','infocode':'10001'})
        page=request.url.params['page_num']
        rows=[{'id':f'{page if mode!="repeat" else "same"}-{i}','typecode':'050117','location':'120.5,31.2'} for i in range(25)]
        if mode=='contact':rows[0]['tel']='sensitive'
        return httpx.Response(200,json={'status':'1','pois':rows})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError):fetch_amap(tmp_path,client,type_codes=('050117',),sleep=lambda _:None)
    assert read(tmp_path/'data/pois.json')==[{'original':'preserve-on-failure'}]
    manifests=list(tmp_path.rglob('manifest.json'))
    assert len(manifests)==1 and read(manifests[0])['status']=='failed'
    assert all('sensitive' not in p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.json'))

@pytest.mark.parametrize('limit',[0,1,3,8])
def test_hard_budget_stops_before_next_request(tmp_path,monkeypatch,limit):
    site=source_root(tmp_path);monkeypatch.setenv('AMAP_KEY','synthetic-budget-secret')
    lat,lon=wgs_to_gcj(site['lat'],site['lon']);calls=[]
    def handler(request):
        calls.append(str(request.url.params['page_num']))
        return httpx.Response(200,json={'status':'1','pois':[{'id':f'{len(calls)}-{i}','typecode':'050117','location':f'{lon},{lat}'} for i in range(25)]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetch_amap(tmp_path,client,type_codes=('050117','050119'),max_calls=limit,sleep=lambda _:None)
    coverage=read(tmp_path/'data/coverage.json')
    assert len(calls)==limit and coverage['amap_calls']==limit
    assert coverage['truncated'] and '截断' in coverage['budget_note']
    assert all('synthetic-budget-secret' not in p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.json'))
from pipeline.amap import Meter, usage_lock, load_key, SUBTYPES
from pipeline.common import channel_for
from pipeline.profiles import load


def test_month_and_task_budget_rejected_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv('AMAP_KEY', 'mock-only')
    raw = tmp_path/'data/raw/amap'
    raw.mkdir(parents=True)
    month = Meter(raw, 80, 'amap-fetch').month()
    for data in ({'months': {month: 4921}, 'tasks': {}}, {'months': {}, 'tasks': {'amap-fetch': 321}}):
        write(raw/'usage.json', data)
        with httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail('budget must refuse before network'))) as client:
            with pytest.raises(RuntimeError): fetch_amap(tmp_path, client, area_id='shishan')
        assert read(raw/'usage.json') == data
        assert not (raw/'usage.lock').exists()


def test_meter_rate_and_persistent_failures(tmp_path):
    ticks = [0.0]; stamps = []
    def sleep(t): ticks[0] += t
    meter = Meter(tmp_path, 3, 'test', clock=lambda: ticks[0], sleep=sleep)
    for _ in range(3):
        meter.before_request(True); stamps.append(ticks[0])
    assert stamps == pytest.approx([0, .4, .8])
    assert read(tmp_path/'usage.json')['months'][meter.month()] == 3
    with pytest.raises(RuntimeError): meter.before_request(True)
    assert read(tmp_path/'usage.json')['tasks']['test'] == 3


def test_usage_excludes_geocoding_and_lock(tmp_path):
    with usage_lock(tmp_path):
        with pytest.raises(RuntimeError):
            with usage_lock(tmp_path): pass
        meter = Meter(tmp_path, 0, 'test')
        meter.before_request(False)
        assert meter.calls == 0
        with pytest.raises(RuntimeError): meter.before_request(True)
    assert not (tmp_path/'usage.lock').exists()


def test_dotenv_native_area_and_contact_scrubbing(tmp_path, monkeypatch):
    (tmp_path/'.env').write_text('AMAP_KEY="fixture-dotenv-secret"\n', encoding='utf-8')
    monkeypatch.setenv('AMAP_KEY', 'different-env-value')
    assert load_key(tmp_path) == 'fixture-dotenv-secret'
    def handler(request):
        assert request.url.params['key'] == 'fixture-dotenv-secret'
        if 'geocode' in request.url.path:
            return httpx.Response(200,json={'status':'1','geocodes':[{'adcode':'320505','location':'120.55,31.28','level':'道路','formatted_address':'苏州测试地址'}]})
        return httpx.Response(200,json={'status':'1','pois':[{'id':'fixture','name':'合成火锅测试','typecode':'050117','location':'120.55,31.28','tel':'do-not-save','contact_info':{'mobile':'do-not-save'},'business':{'tel':'do-not-save'}}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_amap(tmp_path,client,area_id='shishan',type_codes=('050117',),sleep=lambda _:None)
    p = read(tmp_path/'data/areas/shishan/pois.json')[0]
    assert p['coordinate_system']=='GCJ-02' and p['lat']==31.28 and p['lon']==120.55
    assert result['calls']==1
    for path in tmp_path.rglob('*.json'):
        text = path.read_text(encoding='utf-8')
        assert 'do-not-save' not in text and 'fixture-dotenv-secret' not in text


@pytest.mark.parametrize('code,name,expected',[
    ('050119','合成样本','seafood'), ('050117','合成样本','hotpot'), ('050101','合成样本','banquet'),
    ('050300','合成样本','canteen'), ('050118','合成样本','restaurant_other'),
    ('050118','合成烧烤样本','bbq'), ('050100','合成夜宵样本','late_meal'),
])
def test_mapping_rules(code,name,expected):
    p=AmapAdapter().normalize({'pois':[{'id':'test','name':name,'typecode':code,'location':'120.55,31.28'}]}, {'lat':31.28,'lon':120.55,'coordinate_system':'GCJ-02'},3000)[0]
    assert channel_for(p,load('jiangnan-gongquan'))['id']==expected


@pytest.mark.parametrize('mode',['cap','repeat','reflection'])
def test_truncation_and_reflected_credentials(tmp_path,monkeypatch,mode):
    source_root(tmp_path);monkeypatch.setenv('AMAP_KEY','fixture-reflection-secret')
    def handler(request):
        if mode=='reflection':return httpx.Response(200,json={'status':'0','info':'fixture-reflection-secret'})
        page=request.url.params['page_num']
        rows=[{'id':f'{page if mode=="cap" else "same"}-{i}','typecode':'050117','location':'120.55,31.28'} for i in range(25)]
        return httpx.Response(200,json={'status':'1','pois':rows})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        if mode=='reflection':
            with pytest.raises(RuntimeError):fetch_amap(tmp_path,client,type_codes=('050117',),sleep=lambda _:None)
            assert read(tmp_path/'data/pois.json')==[{'original':'preserve-on-failure'}]
        else:
            fetch_amap(tmp_path,client,type_codes=('050117',),sleep=lambda _:None)
            info=read(tmp_path/'data/coverage.json')['type_coverage']['050117']
            assert info['truncated'] and info['reason']==('page_ceiling' if mode=='cap' else 'repeated_page')
    assert all('fixture-reflection-secret' not in p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.json'))


def test_area_run_uses_cache_without_network(tmp_path,monkeypatch):
    from pipeline.__main__ import prepare_data
    monkeypatch.chdir(tmp_path)
    write('data/areas/shishan/pois.json',[{'coordinate_system':'GCJ-02'}])
    write('data/areas/shishan/coverage.json',{'coordinate_system':'GCJ-02'})
    monkeypatch.setattr(httpx.Client,'get',lambda *a,**k:pytest.fail('cache must not call HTTP'))
    assert len(prepare_data(area='shishan')[0])==1

def test_geocode_selection_is_explicit():
    from pipeline.amap import select_geocode
    road={'adcode':'320505','formatted_address':'苏州测试道路','level':'道路','location':'120.55,31.28'}
    assert select_geocode({'geocodes':[road,{**road,'location':'120.56,31.28'}]})==(road,2)
    poi={**road,'level':'兴趣点','formatted_address':'苏州测试兴趣点'}
    assert select_geocode({'geocodes':[road,poi]})==(poi,2)
    with pytest.raises(RuntimeError):select_geocode({'geocodes':[road,{**road,'formatted_address':'不同道路'}]})
    with pytest.raises(RuntimeError):select_geocode({'geocodes':[{**road,'location':'0,0'}]})


def test_client_disables_proxy_environment(tmp_path,monkeypatch):
    import pipeline.amap as module
    source_root(tmp_path);monkeypatch.setenv('AMAP_KEY','mock-client-secret')
    real_client=httpx.Client;options=[]
    def factory(**kwargs):
        options.append(kwargs)
        return real_client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'status':'1','pois':[]})),**kwargs)
    monkeypatch.setattr(module.httpx,'Client',factory)
    fetch_amap(tmp_path,type_codes=('050117',),sleep=lambda _:None)
    assert options[0]['trust_env'] is False


def test_network_failure_is_counted(tmp_path,monkeypatch):
    source_root(tmp_path);monkeypatch.setenv('AMAP_KEY','mock-fail-secret')
    def handler(request):raise httpx.ConnectError('secret URL must not reach disk',request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError):fetch_amap(tmp_path,client,type_codes=('050117',),sleep=lambda _:None)
    assert read(tmp_path/'data/raw/amap/usage.json')['tasks']['amap-fetch']==1
    ledger=next((tmp_path/'data/raw/amap').rglob('ledger.jsonl'))
    assert json.loads(ledger.read_text(encoding='utf-8'))['status']=='failed'


def test_reason_does_not_republish_merchant_claims():
    from pipeline.l3 import reason
    p={'id':'amap:test','name':'第一合成样本'}
    value=reason(p,{'channel_id':'hotpot','priority':'P1'},{'zone_id':'grid:test','type':'混合','rank':1})
    assert 'amap:test' in value and '第一' not in value
    assert p['name']=='第一合成样本'
