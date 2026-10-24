"""Pure code assembly, spherical-distance routes and source-anchored trace."""
import hashlib
from pathlib import Path
from pipeline.geo import distance
from pipeline.contracts import violation
from pipeline.v2_contracts import Pack
from pipeline.quality import envelope, evaluate, errors
from pipeline import l3p

NOTE = '只排A+B，A级优先、同级就近分组；首访上限为地推人数×周数×5天×params.first_visits_per_day（项目假设，默认10）；球面距离非道路导航；超容量A+B保留backlog，C保留终端清单。'


def schedule(c):
    b=c['brief'];pois={p['id']:p for p in c['pois']}
    remaining={t['poi_id']:t for t in c['l3']['items'] if t['grade'] in ('A','B')}
    days=[]
    for week in range(1,b['weeks']+1):
        for day in range(1,6):
            daily=b['planned_visits_day']
            for person in range(1,b['delivery_people']+1):
                current=b;route=[];length=0.0
                for _ in range(min(daily,b['visits_per_person_day'],c['params']['first_visits_per_day'])):
                    if not remaining:break
                    grade=min(t['grade'] for t in remaining.values())
                    pid=min((pid for pid,t in remaining.items() if t['grade']==grade),key=lambda pid:(distance(current['lat'],current['lon'],pois[pid]['lat'],pois[pid]['lon']),pid))
                    point=pois[pid];length+=distance(current['lat'],current['lon'],point['lat'],point['lon'])
                    current=point;route.append(pid);del remaining[pid]
                daily-=len(route)
                if route:days.append(dict(week=week,day=day,person=person,poi_ids=route,route_distance_m=round(length,2)))
    return days


def backlog(c, days):
    scheduled={pid for day in days for pid in day['poi_ids']}
    return sorted((t for t in c['l3']['items'] if t['grade'] in ('A','B') and t['poi_id'] not in scheduled),
                  key=lambda t:(t['grade'],-t['score'],t['poi_id']))


def anchor(row, c):
    # Source file is captured in inputs.json, so a run remains replayable if handbook changes.
    source=row['source'];file=source.split('#')[0]
    content=c['source_documents'][file]
    quote=row.get('fact',row.get('visual_keywords',row.get('name')))
    offset=content.find(quote)
    if offset<0:raise ValueError('Profile quote missing from source')
    return {'source':source,'file':file,'quote':quote,'offset':offset,
            'line':content[:offset].count('\n')+1,'sha256':hashlib.sha256(content.encode()).hexdigest(),
            'judged_by':'human','provenance_note':'用户提供的公开brief原文；不表示本轮人工确认',
            'check':{'passed':True,'method':'exact substring in captured source'}}


def trace(c):
    channels={r['channel_id']:r for r in c['l1']['items']};zones={r['zone_id']:r for r in c['l2']['items']}
    scenes={r['id']:r for r in c['profile']['scenes']};points={r['id']:r for r in c['profile']['selling_points']}
    checks=c['checks'];result={}
    competition={r['zone_id']:r for r in c['l2c']['items']}
    partners={r['partner_id']:r for r in c['l3p']['items']};supply=l3p.assignments(c)
    # Full five-layer reports live once per stage; each node links the exact run-local
    # report and retains its verdict/layer status, avoiding repeated review queues.
    def check_ref(stage):
        report=checks[stage]
        return dict(stage=stage,passed=report['passed'],path=f'{stage}.check.json',
                    layers=[dict(layer=r['layer'],status=r['status']) for r in report['layers']])
    def node(data,stage):return {'data':data,'judged_by':data['judged_by'],'check':check_ref(stage)}
    for t in c['l3']['items']:
        ch=channels[t['channel_id']];z=zones[t['zone_id']]
        result[t['poi_id']]={'terminal':node(t,'L3'),
            'score_components':{'data':t['score_components'],'judged_by':'rules','check':check_ref('L3')},
            'zone':node(z,'L2'),'channel':node(ch,'L1'),
            'scenes':[{'id':s,'judged_by':ch['judged_by'],'check':check_ref('L1'),'source':anchor(scenes[s],c)} for s in ch['scene_ids']],
            'selling_points':[{'id':s,'judged_by':ch['judged_by'],'check':check_ref('L1'),'source':anchor(points[s],c)} for s in ch['selling_point_ids']]}
        result[t['poi_id']]['competition']=node(competition[t['zone_id']],'L2C')
        assignment=supply.get(t['poi_id'])
        result[t['poi_id']]['supply']={'data':assignment,'judged_by':'rules','check':check_ref('L3P'),
            'partner':node(partners[assignment['partner_id']],'L3P') if assignment and assignment['partner_id'] else None}
    return result


def run(c):
    days=schedule(c)
    result=envelope(c,'铺市清单、合规物料、拜访路线与来源证据已拼装；待验证项随包交付。',c['l3']['items'],
                    channels=c['l1'],zones=c['l2'],materials=c['l4'],days=days,backlog=backlog(c,days),trace=trace(c),
                    coverage=c['coverage'],review_queue=[r for report in c['checks'].values() for r in report['review_queue']],planning_note=NOTE)
    result.update(competition=c['l2c'],partners=c['l3p'],supply_assignments=l3p.assignments(c),recruitment_gaps=l3p.gaps(c))
    result['judged_by']='rules'
    return result


def references(a,c):
    out=[]
    for key,expected in [('items',c['l3']['items']),('channels',c['l1']),('zones',c['l2']),('materials',c['l4']),('competition',c['l2c']),('partners',c['l3p'])]:
        if a[key]!=expected:out.append(violation('upstream',key,'拼装内容必须等于上游已验收交付物'))
    if a['trace']!=trace(c):out.append(violation('trace','trace','证据链断裂或不等于上游及原文锚点'))
    return out


def business(a,c):
    from pipeline import l0,l1,l2,l2c,l3,l4
    out=[]
    for name,module,key in [('L0',l0,'brief'),('L1',l1,'l1'),('L2',l2,'l2'),('L2C',l2c,'l2c'),('L3',l3,'l3'),('L3P',l3p,'l3p'),('L4',l4,'l4')]:
        verified=module.inspect(c[key],{**c,'demo_replay':None,'attempt':2})
        if not verified['passed'] or not c['checks'].get(name,{}).get('passed'):
            out.append(violation('upstream_check',name,'上游验收未通过'))
    if a['days']!=schedule(c):out.append(violation('route','days','路线、人员或每日容量与代码重算不一致'))
    if a['backlog']!=backlog(c,schedule(c)):out.append(violation('backlog','backlog','超出容量的A+B必须完整保留且不得重复排入'))
    if a['coverage']!=c['coverage'] or a['planning_note']!=NOTE:out.append(violation('provenance','coverage','覆盖或路线假设被更改'))
    if a['review_queue']!=[r for report in c['checks'].values() for r in report['review_queue']]:out.append(violation('review_queue','review_queue','待复核项遗漏'))
    if a['supply_assignments']!=l3p.assignments(c) or a['recruitment_gaps']!=l3p.gaps(c):out.append(violation('supply_assignment','supply_assignments','建议供货方或招商缺口与代码重算不一致'))
    return out


def inspect(a,c):return evaluate('PACK',a,c,Pack,references,business)
def check(a,c):return errors(inspect(a,c))
