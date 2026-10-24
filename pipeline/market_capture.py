"""Contact-free, metered market snapshot. All searches share a durable task cap."""
from collections import deque
import json
from pathlib import Path
import time
import uuid
import httpx
from pipeline.amap import AREAS, Meter, basic_payload, load_key, usage_lock
from pipeline.geo import distance
from pipeline.io import read, write, now
from pipeline.profiles import load, parameters

TASK_ID='competition-partners'
CAP=150


def normalize(row,site,radius):
    try:
        lon,lat=map(float,row['location'].split(','))
        if not (-90<=lat<=90 and -180<=lon<=180):return None
        d=distance(site['lat'],site['lon'],lat,lon)
        if d>radius:return None
        return dict(id='amap:'+row['id'],name=row.get('name') or '',lat=lat,lon=lon,
                    coordinate_system='GCJ-02',source='AMap',tags={k:row.get(k,'') for k in ('type','typecode')},distance_m=round(d,2))
    except (ValueError,KeyError,TypeError):return None


def queries(profile,params):
    result=[dict(keyword=k,types='',role='competitor',brand=r['brand']) for r in profile['competitors'] for k in r['keywords']]
    return result+params['partners']['queries']


def capture(root=Path('.'),max_calls=150,client=None,sleep=time.sleep):
    root=Path(root)
    if type(max_calls) is not int or not 0<=max_calls<=CAP:raise ValueError('budget must be 0..150')
    params=parameters();profile=load('jiangnan-gongquan');spec=queries(profile,params)
    radius=params['partners']['search_radius_m']
    if not 0<radius<=5000:raise ValueError('partner radius must be <=5000')
    raw=root/'data/raw/amap';key=load_key(root)
    with usage_lock(raw):
        usage=read(raw/'usage.json') if (raw/'usage.json').exists() else {'tasks':{}}
        budget=min(max_calls,max(0,CAP-usage['tasks'].get(TASK_ID,0)))
        meter=Meter(raw,budget,TASK_ID,sleep=sleep,task_limit=CAP)
        directory=raw/('market-'+uuid.uuid4().hex);directory.mkdir()
        sites={a:read(root/'data/areas'/a/'site.json') for a in AREAS}
        coverages={a:read(root/'data/areas'/a/'coverage.json') for a in AREAS}
        if any(s.get('coordinate_system')!='GCJ-02' for s in sites.values()):raise ValueError('Expected GCJ-02')
        jobs=[dict(area=a,**q,next_page=1,calls=0,truncated=True,reason='task_budget',seen=[]) for q in spec for a in AREAS]
        for i,j in enumerate(jobs):j['job_id']=i
        queue=deque(jobs);found={a:{'competitors':{},'partners':{}} for a in AREAS}
        manifest=dict(task_id=TASK_ID,task_cap=CAP,budget=budget,started_at=now(),calls=0,jobs=jobs,requests=[],status='running')
        owned=client is None;client=client or httpx.Client(timeout=30,trust_env=False)
        write(directory/'manifest.json',manifest)
        try:
            while queue and meter.calls<budget:
                j=queue.popleft();site=sites[j['area']];page=j['next_page']
                r=coverages[j['area']]['radius_m'] if j['role']=='competitor' else radius
                q=dict(location=f"{site['lon']},{site['lat']}",radius=r,keywords=j['keyword'],types=j['types'],page_size=25,page_num=page,sortrule='distance')
                filename=f"job-{j['job_id']}-{page}.json"
                meter.before_request(True);j['calls']+=1;manifest['calls']=meter.calls
                entry=dict(endpoint='/v5/place/around',params={**q,'key':'[REDACTED]'},path=filename,status='attempted',fetched_at=now())
                manifest['requests'].append(entry);write(directory/'manifest.json',manifest)
                try:
                    response=client.get('https://restapi.amap.com/v5/place/around',params={**q,'key':key})
                    response.raise_for_status();original=response.json()
                    if key in json.dumps(original,ensure_ascii=False):raise ValueError('Reflected credential')
                    payload=basic_payload(original);write(directory/filename,payload)
                    if payload.get('status')!='1':raise ValueError('Rejected request')
                    batch=payload.get('pois',[]);ids={p['id'] for p in batch}
                    entry.update(status='success',count=len(batch))
                    for row in batch:
                        p=normalize(row,site,r)
                        if p is None:continue
                        p.update(capture_manifest=str(directory/'manifest.json'),fetched_at=entry['fetched_at'])
                        bucket=found[j['area']]['competitors' if j['role']=='competitor' else 'partners']
                        if j['role']=='competitor':
                            # A fuzzy search hit does not establish a brand: require literal query in source name.
                            if j['keyword'] not in p['name']:continue
                            p['brands']=sorted(set(bucket.get(p['id'],{}).get('brands',[]))|{j['brand']})
                        bucket[p['id']]=p
                    if batch and not ids-set(j['seen']):j['reason']='repeated_page'
                    elif len(batch)<25:j.update(truncated=False,reason=None)
                    elif page>=8:j['reason']='page_ceiling'
                    else:j['next_page']+=1;queue.append(j)
                    j['seen']=sorted(set(j['seen'])|ids)
                except Exception as exc:
                    entry.update(status='failed',error_type=type(exc).__name__);j['reason']='request_failed'
                finally:
                    with (directory/'ledger.jsonl').open('a',encoding='utf-8') as log:log.write(json.dumps(entry,ensure_ascii=False)+'\n')
                    write(directory/'manifest.json',manifest)
            summary={}
            for area,values in found.items():
                area_jobs=[j for j in jobs if j['area']==area]
                coverage=dict(source='AMap',coordinate_system='GCJ-02',site=sites[area],competitor_radius_m=coverages[area]['radius_m'],partner_radius_m=radius,
                    truncated=any(j['truncated'] for j in area_jobs),jobs=area_jobs,calls=sum(j['calls'] for j in area_jobs),manifest=str(directory/'manifest.json'))
                snapshot={k:sorted(v.values(),key=lambda p:p['id']) for k,v in values.items()}
                snapshot.update(coverage=coverage,open=[
                    '来源：高德关键词/周边搜索；截断原因见coverage.jobs；零结果不代表不存在，点位不证明营业或供货资质。',
                    *profile['competitors_open']])
                # Keep immutable capture and prior cache for recovery.
                target=root/'data/areas'/area/'market.json'
                if target.exists():write(directory/f'{area}-before.json',read(target))
                write(directory/f'{area}.json',snapshot)
                if budget:write(target,snapshot)
                summary[area]=dict(competitors=len(values['competitors']),partners=len(values['partners']),coverage=coverage)
            manifest.update(status='completed',finished_at=now());write(directory/'manifest.json',manifest)
            return dict(calls=meter.calls,task_total=meter.data['tasks'].get(TASK_ID,0),usage=meter.data,manifest=str(directory/'manifest.json'),areas=summary)
        finally:
            if owned:client.close()


def load_market(area,root=Path('.')):
    path=Path(root)/'data/areas'/str(area)/'market.json'
    if area and path.exists():
        result=read(path)
        if result['coverage']['coordinate_system']!='GCJ-02' or any(p['coordinate_system']!='GCJ-02' for k in ('competitors','partners') for p in result[k]):raise ValueError('Coordinate mismatch')
        return result
    return dict(competitors=[],partners=[],coverage={'truncated':True,'reason':'not_collected'},open=['来源：尚未采集本区域竞品和伙伴，空结果不代表不存在。'])
