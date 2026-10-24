from pipeline.common import decide, selected_pois, channel_for
from pipeline.geo import grid_id,distance
from pipeline.contracts import violation
from pipeline.v2_contracts import Terminals
from pipeline.quality import meta,envelope,evaluate,errors,equal_ids,check_compliance
from math import ceil

ACTIONS={'A':'首访-带样品和桌牌','B':'首访-了解需求','C':'暂缓'}


def candidates(c):
    channels={r['channel_id']:r for r in c['l1']['items'] if r['priority'] in ('P1','P2') or (r['channel_id']=='restaurant_other' and r['priority']=='P3')}
    zones={r['zone_id']:r for r in c['l2']['items'] if r['selected']}
    result=[]
    for p in selected_pois(c):
        ch=channel_for(p,c['profile']);z=grid_id(p,c['brief'])
        if ch and ch['id'] in channels and z in zones:result.append((p,channels[ch['id']],zones[z]))
    return sorted(result,key=lambda r:r[0]['id'])


def calculated(p,ch,z,c):
    b=c['brief'];spec=c['params']['scoring']
    components=dict(channel_weight=c['params']['priority_weights'][ch['priority']],zone_rank=round(1/z['rank'],8),
                    distance_decay=round(1/(1+distance(b['lat'],b['lon'],p['lat'],p['lon'])/b['radius_m']),8),
                    data_completeness=sum(bool(p.get(k)) for k in ('name','category','lat','lon'))/4)
    score=round(sum(components[k]*v for k,v in spec['component_weights'].items()),8)
    return dict(score_components=components,score=score)


def ranked(c):
    rows=candidates(c)
    values={p['id']:calculated(p,ch,z,c) for p,ch,z in rows}
    rows.sort(key=lambda r:(-values[r[0]['id']]['score'],
              round(distance(c['brief']['lat'],c['brief']['lon'],r[0]['lat'],r[0]['lon']),2),r[0]['id']))
    shares=c['params']['scoring']['grade_shares'];n=len(rows)
    if any(v<0 for v in shares.values()) or abs(sum(shares.values())-1)>1e-9:
        raise ValueError('Invalid grade shares')
    for i,(p,ch,z) in enumerate(rows):
        grade='A' if i<ceil(n*shares['A']) else 'B' if i<ceil(n*(shares['A']+shares['B'])) else 'C'
        unknown=ch['channel_id']=='restaurant_other'
        if unknown:grade='C'
        values[p['id']].update(grade=grade,next_action='首访-了解需求' if unknown else ACTIONS[grade])
    return values


def reason(p,ch,z):
    # Merchant names remain verbatim in pois.json; reasons use IDs to avoid republishing
    # promotional claims embedded in source names. Compliance checks remain unchanged.
    return f"POI={p['id']}；业态={ch['channel_id']}；优先级={ch['priority']}；商圈={z['zone_id']}；类型={z['type']}；排名={z['rank']}。"


def run(c):
    rows=candidates(c)
    def rules():
        return {'decision':'在选中商圈内按渠道、排名、距离和资料完整度确定首访动作。',
                'items':[dict(poi_id=p['id'],channel_id=ch['channel_id'],zone_id=z['zone_id'],reason=reason(p,ch,z),
                              **meta(c,['poi:'+p['id'],'L1:'+ch['channel_id'],'L2:'+z['zone_id'],'params.scoring'])) for p,ch,z in rows]}
    decision=decide(c,'L3',{'candidates':[dict(poi=p,channel=ch,zone=z,allowed_reason=reason(p,ch,z)) for p,ch,z in rows]},rules,
                    '返回decision,items，每项poi_id,channel_id,zone_id,reason,evidence,judged_by,confidence,open；reason只能使用候选的allowed_reason原文，不计算分数等级。')
    lookup=ranked(c)
    for r in decision['items']:
        if r['poi_id'] in lookup:r.update(lookup[r['poi_id']])
        if r.get('grade') in ('A','B'):
            r['open'].append('来源：高德POI不提供店内在售品牌；现售佐餐饮料品牌待首访确认。')
    return envelope(c,decision['decision'],decision['items'])


def references(a,c):
    rows={p['id']:(p,ch,z) for p,ch,z in candidates(c)}
    out=equal_ids(a['items'],'poi_id',rows)
    for r in a['items']:
        if r['poi_id'] in rows:
            p,ch,z=rows[r['poi_id']]
            if r['channel_id']!=ch['channel_id'] or r['zone_id']!=z['zone_id']:out.append(violation('reference','items','渠道或商圈不是该POI的上游归属'))
    return out


def shortage(a,c):
    return [z['zone_id'] for z in c['l2']['items'] if z['selected'] and
            sum(r['zone_id']==z['zone_id'] and r['grade'] in ('A','B') for r in a['items'])<c['params']['min_terminals_per_zone']]


def business(a,c):
    out=check_compliance(a,c);rows={p['id']:(p,ch,z) for p,ch,z in candidates(c)};expected=ranked(c)
    for r in a['items']:
        p,ch,z=rows[r['poi_id']]
        if any(r[k]!=v for k,v in expected[r['poi_id']].items()):out.append(violation('score','score_components/score/grade/next_action','与代码重算不一致'))
        if r['reason']!=reason(p,ch,z):out.append(violation('reason','reason','理由只能使用该POI已知字段'))
        if r['grade'] in ('A','B') and not any('现售佐餐饮料品牌待首访确认' in s for s in r['open']):out.append(violation('stock_open','open','A/B终端缺少现售品牌待确认项'))
    for z in shortage(a,c):out.append(violation('zone_shortage',z,'A+B终端数不足 params.min_terminals_per_zone；必须回退L2'))
    return out


def inspect(a,c):return evaluate('L3',a,c,Terminals,references,business)
def check(a,c):return errors(inspect(a,c))
