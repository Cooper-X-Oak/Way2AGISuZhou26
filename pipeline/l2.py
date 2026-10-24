from collections import Counter
from pipeline.common import decide, selected_pois, channel_for
from pipeline.geo import grid_id, distance
from pipeline.contracts import violation
from pipeline.v2_contracts import Zones
from pipeline.quality import meta,envelope,evaluate,errors,equal_ids


def counts(c):
    active={r['channel_id'] for r in c['l1']['items'] if r['priority'] in ('P1','P2')}
    result={}
    for p in selected_pois(c):
        z=grid_id(p,c['brief']);result.setdefault(z,Counter({k:0 for k in sorted(active)}))
        ch=channel_for(p,c['profile'])
        if ch and ch['id'] in active:result[z][ch['id']]+=1
    return {z:dict(n) for z,n in result.items()}


def ordered(c):
    weights={r['channel_id']:c['params']['priority_weights'][r['priority']] for r in c['l1']['items']}
    return sorted(counts(c).items(),key=lambda z:(-sum(n*weights[k] for k,n in z[1].items()),z[0]))


def selection(c):
    return [z for z,n in ordered(c) if sum(n.values()) and z not in c.get('excluded_zones',[])][:c['params']['max_selected_zones']]


def run(c):
    data=ordered(c); profiles={r['id']:r for r in c['profile']['channels']}
    def rules():
        rows=[]
        for z,n in data:
            types={profiles[k]['grid_type'] for k,v in n.items() if v}
            rows.append(dict(zone_id=z,tier='社区级',type=next(iter(types)) if len(types)==1 else ('混合' if types else '非目标'),
                             **meta(c,['L1:'+k for k,v in n.items() if v] or ['params.priority_weights'],
                                    opened=[f"来源：500米网格与{c['coverage']['source']}点位；商圈层级暂按社区级假设，需现场验证。"])))
        return {'decision':'按目标业态加权点位数排序，选择未被回退排除的商圈。','items':rows}
    decision=decide(c,'L2',{'counts':dict(data),'profile':c['profile'],'channels':c['l1']},rules,
                    '返回 decision,items；每项 zone_id,tier,type,evidence,judged_by,confidence,open；所有网格各一次，不计算counts/rank/selected。')
    computed={z:dict(counts=n,rank=i+1,selected=z in selection(c)) for i,(z,n) in enumerate(data)}
    for r in decision['items']:
        r.update(computed.get(r['zone_id'],{}))
    return envelope(c,decision['decision'],decision['items'])


def references(a,c):
    return equal_ids(a['items'],'zone_id',counts(c))


def business(a,c):
    out=[]; ranked=ordered(c); expected={z:(n,i+1) for i,(z,n) in enumerate(ranked)}
    for r in a['items']:
        n,rank=expected[r['zone_id']]
        if r['counts']!=n or r['rank']!=rank:out.append(violation('recount','counts/rank','计数或排名与POI代码重算不一致'))
        if r['selected']!=(r['zone_id'] in selection(c)):out.append(violation('selection','selected','必须按排名补选，排除回退商圈'))
    selected=[r for r in a['items'] if r['selected']]
    if not selected or len(selected)>c['params']['max_selected_zones']:out.append(violation('selection_limit','items','无可选商圈或超出上限'))
    coverage=c['coverage']; b=c['brief'];s=coverage['site']
    if distance(s['lat'],s['lon'],b['lat'],b['lon'])+b['radius_m']>coverage['radius_m']+0.01:
        out.append(violation('source_coverage','coverage','请求区域超出现有数据覆盖'))
    return out


def inspect(a,c):return evaluate('L2',a,c,Zones,references,business)
def check(a,c):return errors(inspect(a,c))
