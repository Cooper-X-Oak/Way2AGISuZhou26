"""Auditable L0-L4/PACK runner: bounded retries, explicit approvals and backtracking."""
import csv
import hashlib
import json
import time
import uuid
from copy import deepcopy
from pathlib import Path
from pipeline import l0,l1,l2,l2c,l3,l3p,l4,pack
from pipeline.io import now,write
from pipeline.profiles import source_path

STAGES=(("L0",l0),("L1",l1),("L2",l2),("L2C",l2c),("L3",l3),("L3P",l3p),("L4",l4),("PACK",pack))
NAMES={'L0':'brief','L1':'channels','L2':'zones','L2C':'competition','L3':'terminals','L3P':'partners','L4':'materials','PACK':'pack'}


def execute(inputs,root=Path('.'),stages=STAGES):
    run_id=now().replace(':','').replace('-','').replace('.','')+'-'+uuid.uuid4().hex[:8]
    directory=Path(root)/'runs'/run_id;directory.mkdir(parents=True,exist_ok=False)
    public=deepcopy({k:v for k,v in inputs.items() if k!='judge'})
    sources={}
    for row in inputs['profile']['scenes']+inputs['profile']['selling_points']:
        file=row['source'].split('#')[0]
        sources[file]=source_path(file).read_text(encoding='utf-8')
    public['source_documents']=sources
    write(directory/'inputs.json',public);write(directory/'params.json',inputs['params'])
    write(directory/'pois.json',inputs['pois']);write(directory/'coverage.json',inputs['coverage']);write(directory/'profile.json',inputs['profile'])
    write(directory/'market.json',public.get('market',{'competitors':[],'partners':[],'open':['来源：未提供市场缓存。']}))
    c={**deepcopy(public),'judge':inputs['judge'],'approvals':{},'checks':{},'excluded_zones':[]}
    state=dict(run_id=run_id,status='running',started_at=now(),judged_by=inputs['judge'].label.split(':',1)[0],
               input_sha256=hashlib.sha256(json.dumps(public,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
               completed_stages=[],stopped_at=None,backtracks=0)
    write(directory/'status.json',state)
    def ledger(entry):
        with (directory/'ledger.jsonl').open('a',encoding='utf-8',newline='\n') as f:f.write(json.dumps({'timestamp':now(),**entry},ensure_ascii=False)+'\n')
    def stop(name,violations):
        state.update(status='blocked',stopped_at=name,violations=violations,finished_at=now())
        write(directory/'status.json',state)
        return directory,state
    index=0;revisions={};demo_configured=False
    while index<len(stages):
        name,module=stages[index];revision=revisions.get(name,0)+1;revisions[name]=revision
        c.pop('previous',None);c.pop('violations',None);c['approvals'].pop(name,None)
        if name=='L3' and c.get('demo_replay')=='backtrack' and not demo_configured:
            demo_configured=True
            rows=l3.candidates(c)
            totals={z['zone_id']:0 for z in c['l2']['items'] if z['selected']}
            ranked=l3.ranked(c)
            for p,ch,z in rows:
                totals[z['zone_id']]+=ranked[p['id']]['grade'] in ('A','B')
            natural=any(n<c['params']['min_terminals_per_zone'] for n in totals.values())
            state['natural_backtrack']=natural
            if not natural and totals:
                before=c['params']['min_terminals_per_zone']
                c['params']['min_terminals_per_zone']=min(totals.values())+1
                c['params']['sources']['min_terminals_per_zone']='来源：--demo-replay backtrack 演示配置，固定为首轮最小A+B数+1；后续不降低'
                c['seeded_backtrack']=True
                write(directory/'params.json',c['params'])
                ledger(dict(event='demo_configuration',stage='L3',seeded_for_demo=True,parameter='min_terminals_per_zone',before=before,after=c['params']['min_terminals_per_zone']))
        restart=False;passed=False
        for attempt in range(1,4):
            c['attempt']=attempt;started=time.perf_counter();artifact=None
            try:
                artifact=module.run(c);json.dumps(artifact,allow_nan=False)
                if c.get('auto_approve') and name in ('L2','L3'):c['approvals'][name]='auto (demo)'
                report=module.inspect(artifact,c)
                if name in ('L2','L3') and not c.get('auto_approve') and {v['code'] for v in report['violations']}=={'approval_pending'}:
                    preview=directory/f'pending/{name}-r{revision}.json'
                    write(preview,{'artifact':artifact,'check':report,**({'competition_summary':l2c.preview(c,artifact)} if name=='L2' else {})})
                    try:approved=input(f'{name} 待确认：{preview}；输入 yes 确认，其他输入停止：').strip().lower()=='yes'
                    except EOFError:approved=False
                    if approved:c['approvals'][name]='human';report=module.inspect(artifact,c)
                violations=report['violations']
            except Exception as exc:
                violations=[dict(code='execution_error',path=name,message=f'{type(exc).__name__}: 执行失败，未生成替代结果')]
                report={'stage':name,'passed':False,'layers':[dict(layer=k,status='failed' if i==0 else 'blocked',violations=violations if i==0 else []) for i,k in enumerate(('structure','references','business','judgment_quality','human_gate'))],'review_queue':[],'violations':violations}
                try:json.dumps(artifact,allow_nan=False)
                except (ValueError,TypeError):artifact=None
            filename=f'attempts/{name}-r{revision}-{attempt}.json'
            entry=dict(stage=name,revision=revision,attempt=attempt,status='failed' if violations else 'passed',violations=violations,
                       duration_seconds=round(time.perf_counter()-started,6),artifact_path=filename,check_path=f'checks/{name}-r{revision}-{attempt}.json',
                       judged_by=artifact.get('judged_by') if artifact else None,approved_by=c['approvals'].get(name),
                       seeded_for_demo=bool(c.get('seeded_backtrack') or artifact and artifact.get('seeded_for_demo')))
            write(directory/filename,{'artifact':artifact,'check':report,'violations':violations})
            write(directory/entry['check_path'],report);write(directory/f'{name}.check.json',report);ledger(entry)
            if artifact is not None:
                write(directory/f'{name}.json',artifact)
                if name=='L0':write(directory/'brief.json',artifact)
            if not violations:
                key='brief' if name=='L0' else name.lower();c[key]=artifact;c['checks'][name]=report
                write(directory/f'{NAMES[name]}.json',artifact)
                if name not in state['completed_stages']:state['completed_stages'].append(name)
                passed=True;break
            codes={v['code'] for v in violations}
            if name=='L3' and codes <= {'zone_shortage','approval_pending'} and 'zone_shortage' in codes:
                zones=l3.shortage(artifact,c)
                backtrack=dict(event='backtrack',backtrack={'from':'L3','to':'L2','reason':'选中商圈内A+B级终端数不足，保持阈值并取消该商圈，补选下一名','zone_ids':zones},
                               seeded_for_demo=bool(c.get('seeded_backtrack')),count=state['backtracks']+1)
                limit=min(2,c['params']['max_backtracks'])
                if state['backtracks']>=limit:
                    ledger({**backtrack,'status':'limit_exceeded'})
                    return stop('L2',[dict(code='backtrack_limit',path='L2',message='回退上限已到；需补充数据或重新立项，未放宽阈值')])
                state['backtracks']+=1
                if not c.get('seeded_backtrack'):state['natural_backtrack']=True
                ledger({**backtrack,'status':'returning'})
                c['excluded_zones']=sorted(set(c['excluded_zones'])|set(zones))
                c['checks'].pop('L2',None);c['checks'].pop('L3',None)
                c['approvals'].pop('L2',None);c['approvals'].pop('L3',None)
                for downstream in ('L2C','L3P','L4','PACK'):
                    c['checks'].pop(downstream,None);c.pop(downstream.lower(),None)
                state['completed_stages']=[s for s in state['completed_stages'] if s not in ('L2','L2C','L3','L3P','L4','PACK')]
                index=next(i for i,(n,_) in enumerate(stages) if n=='L2');restart=True;break
            if 'approval_pending' in codes:return stop(name,violations)
            c.update(previous=artifact,violations=violations)
        write(directory/'status.json',state)
        if restart:continue
        if not passed:return stop(name,violations)
        index+=1
    if 'pack' in c:
        write(directory/'trace.json',c['pack']['trace'])
        fields=['poi_id','channel_id','zone_id','score','grade','next_action','reason','suggested_partner_id','supply_status','supply_distance_m']
        with (directory/'terminals.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
            for terminal in c['pack']['items']:
                supply=c['pack']['supply_assignments'].get(terminal['poi_id'],{})
                writer.writerow({**terminal,'suggested_partner_id':supply.get('partner_id'),'supply_status':supply.get('status'),'supply_distance_m':supply.get('distance_m')})
    state.update(status='completed',finished_at=now());write(directory/'status.json',state)
    return directory,state
