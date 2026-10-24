"""Four rectangle searches per truncated type; fair pagination, durable 150-call cap."""
from collections import deque
from copy import deepcopy
import json
import math
from pathlib import Path
import time
import uuid
import httpx
from pipeline.amap import AREAS, AmapAdapter, Meter, basic_payload, load_key, usage_lock
from pipeline.common import coverage_counts
from pipeline.io import read, write, now
from pipeline.profiles import load, PROFILE_DIR

TASK_ID = 'grading-fix'
TASK_CAP = 150


def rectangles(site, radius=3000):
    angular=radius/6371000
    dy=math.degrees(angular)
    dx=math.degrees(math.asin(math.sin(angular)/math.cos(math.radians(site['lat']))))
    x,y=site['lon'],site['lat']
    return [f'{left:.8f},{top:.8f}|{right:.8f},{bottom:.8f}'
            for left,right in ((x-dx,x),(x,x+dx))
            for bottom,top in ((y-dy,y),(y,y+dy))]


def supplement(root=Path('.'), client=None, max_calls=150, sleep=time.sleep):
    root=Path(root);raw=root/'data/raw/amap'
    if type(max_calls) is not int or not 0<=max_calls<=TASK_CAP:raise ValueError('budget must be 0..150')
    key=load_key(root)
    with usage_lock(raw):
        usage=read(raw/'usage.json') if (raw/'usage.json').exists() else {'tasks':{}}
        budget=min(max_calls,max(0,TASK_CAP-usage['tasks'].get(TASK_ID,0)))
        meter=Meter(raw,budget,TASK_ID,sleep=sleep,task_limit=TASK_CAP)
        directory=raw/('grading-'+uuid.uuid4().hex);directory.mkdir()
        snapshots={a:{k:read(root/'data/areas'/a/(k+'.json')) for k in ('site','pois','coverage')} for a in AREAS}
        write(directory/'before.json',snapshots)
        queue=deque();jobs=[];extra={a:[] for a in AREAS}
        # Iterate cells before areas/types so every target receives early coverage.
        for cell in range(4):
            for area,snapshot in snapshots.items():
                if snapshot['site'].get('coordinate_system')!='GCJ-02':raise ValueError('Expected native GCJ-02')
                for code,info in snapshot['coverage']['type_coverage'].items():
                    if not info['truncated']:continue
                    job=dict(area=area,type_code=code,cell=cell,polygon=rectangles(snapshot['site'])[cell],
                             next_page=1,calls=0,returned=0,truncated=True,reason='task_budget',seen=[])
                    jobs.append(job);queue.append(job)
        manifest=dict(started_at=now(),task_id=TASK_ID,budget=budget,task_cap=TASK_CAP,calls=0,jobs=jobs,requests=[],
                      method='原圆外接矩形四等分；API矩形搜索后过滤原圆3000米；POI ID去重；逐页轮询全部网格',
                      source='https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch',status='running')
        write(directory/'manifest.json',manifest)
        owned=client is None;client=client or httpx.Client(timeout=45,trust_env=False)
        try:
            while queue and meter.calls<budget:
                job=queue.popleft();page=job['next_page']
                query=dict(polygon=job['polygon'],types=job['type_code'],page_size=25,page_num=page)
                filename=f"{job['area']}-{job['type_code']}-{job['cell']}-{page}.json"
                meter.before_request(True)
                entry=dict(endpoint='/v5/place/polygon',params={**query,'key':'[REDACTED]'},path=filename,
                           fetched_at=now(),status='attempted')
                manifest['requests'].append(entry);manifest['calls']=meter.calls;job['calls']+=1
                write(directory/'manifest.json',manifest)
                try:
                    response=client.get('https://restapi.amap.com/v5/place/polygon',params={**query,'key':key})
                    response.raise_for_status();original=response.json()
                    if key in json.dumps(original,ensure_ascii=False):raise ValueError('Reflected credential')
                    payload=basic_payload(original);write(directory/filename,payload)
                    if payload.get('status')!='1':raise ValueError('Rejected request')
                    batch=payload.get('pois',[]);ids={p['id'] for p in batch}
                    extra[job['area']].extend(batch);job['returned']+=len(batch)
                    entry['status']='success';entry['count']=len(batch)
                    if batch and not ids-set(job['seen']):job['reason']='repeated_page'
                    elif len(batch)<25:job.update(truncated=False,reason=None)
                    elif page==8:job['reason']='page_ceiling'
                    else:job['next_page']+=1;queue.append(job)
                    job['seen']=sorted(set(job['seen'])|ids)
                except Exception as exc:
                    entry.update(status='failed',error_type=type(exc).__name__)
                    job['reason']='request_failed'
                finally:
                    with (directory/'ledger.jsonl').open('a',encoding='utf-8') as log:
                        log.write(json.dumps(entry,ensure_ascii=False)+'\n')
                    write(directory/'manifest.json',manifest)
            summary={}
            for area,snapshot in snapshots.items():
                site=snapshot['site'];coverage=deepcopy(snapshot['coverage'])
                merged={p['id']:p for p in snapshot['pois']}
                added=AmapAdapter().normalize({'pois':extra[area]},site,3000)
                for p in added:
                    p.update(fetched_at=now(),capture_manifest=str(directory/'manifest.json'))
                    merged[p['id']]=p
                pois=sorted(merged.values(),key=lambda p:p['id'])
                # Upgrade classification metadata for cached records without altering source facts.
                adapter=AmapAdapter()
                for p in pois:
                    tags=p.get('tags',{})
                    p['restaurant_types']=adapter.subtypes(str(tags.get('typecode','')),str(tags.get('type','')))
                    p['classification_source']=adapter.mapping['source']
                for code,info in coverage['type_coverage'].items():
                    cells=[j for j in jobs if j['area']==area and j['type_code']==code]
                    if cells:
                        info['previous']=deepcopy(snapshot['coverage']['type_coverage'][code])
                        info['supplement_cells']=cells
                        info['truncated']=any(j['truncated'] for j in cells)
                        info['reason']=';'.join(sorted({j['reason'] for j in cells if j['reason']})) or None
                coverage.update(total=len(pois),unnamed=sum(not p.get('name') for p in pois),
                    truncated=any(v['truncated'] for v in coverage['type_coverage'].values()),
                    supplement_manifest=str(directory/'manifest.json'),supplemented_at=now(),
                    supplement_calls=sum(j['calls'] for j in jobs if j['area']==area),
                    channel_counts={p.stem:coverage_counts(pois,load(p.stem)) for p in PROFILE_DIR.glob('*.yaml')},
                    classification_note=adapter.mapping['source'])
                coverage['categories']={k:sum(p['category']==k for p in pois) for k in coverage['categories']}
                coverage['budget_note']='补抓仍截断：详见type_coverage' if coverage['truncated'] else '四格补抓未触及分页上限；API不保证全量'
                dest=root/'data/areas'/area
                write(dest/'pois.json',pois);write(dest/'coverage.json',coverage)
                summary[area]=dict(before=len(snapshot['pois']),after=len(pois),calls=coverage['supplement_calls'],
                                   truncated=coverage['truncated'],channel_counts_before=snapshot['coverage']['channel_counts'],
                                   channel_counts_before_remapped={p.stem:coverage_counts(snapshot['pois'],load(p.stem)) for p in PROFILE_DIR.glob('*.yaml')},
                                   channel_counts_after=coverage['channel_counts'])
            manifest.update(status='completed',finished_at=now(),summary=summary)
            write(directory/'manifest.json',manifest)
            return dict(calls=meter.calls,task_total=meter.data['tasks'].get(TASK_ID,0),usage=meter.data,
                        manifest=str(directory/'manifest.json'),areas=summary)
        finally:
            if owned:client.close()
