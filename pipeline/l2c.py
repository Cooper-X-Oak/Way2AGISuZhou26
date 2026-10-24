"""Observed competitor POIs, never market share or inferred restaurant stock."""
import json
import math
from pipeline.common import decide
from pipeline.geo import grid_id, distance
from pipeline.contracts import violation
from pipeline.v2_contracts import Competition
from pipeline.quality import meta, envelope, evaluate, errors, equal_ids


def counts(c, zones=None):
    brands=[r['brand'] for r in c['profile'].get('competitors', [])]
    result={z['zone_id']:{b:0 for b in brands+list(c['params']['competition']['retail_typecodes'])} for z in (zones or c['l2'])['items']}
    seen=set()
    brand_pois={}
    for p in c.get('market', {}).get('competitors', []):
        entry=brand_pois.setdefault(p['id'],dict(point=p,brands=set()))
        entry['brands'].update(set(p['brands']) & set(brands))
    for pid,entry in brand_pois.items():
        p=entry['point']
        if not entry['brands']: continue
        if distance(c['brief']['lat'],c['brief']['lon'],p['lat'],p['lon'])>c['brief']['radius_m']:continue
        seen.add(pid)
        z=grid_id(p,c['brief'])
        # Multi-brand outlets contribute once; attribution is stable, not sales share.
        if z in result:result[z][sorted(entry['brands'])[0]]+=1
    # Count a physical outlet once across brand and retail proxy buckets.
    for p in c.get('market', {}).get('availability', []):
        if p['id'] in seen: continue
        seen.add(p['id'])
        if distance(c['brief']['lat'],c['brief']['lon'],p['lat'],p['lon'])>c['brief']['radius_m']: continue
        z=grid_id(p,c['brief'])
        code=str(p.get('tags',{}).get('typecode',''))
        for label,codes in c['params']['competition']['retail_typecodes'].items():
            if z in result and any(code.startswith(v) for v in codes):
                result[z][label]=result[z].get(label,0)+1
                break
    return result


def intensity(n,c,values=None):
    values=counts(c) if values is None else values
    selected=[sum(values[z['zone_id']].values()) for z in c['l2']['items'] if z['selected']]
    # Competition rank: ties use the first rank, so identical evidence stays identical.
    fraction=sum(v<n for v in selected)/len(selected) if selected else 0
    shares=c['params']['competition']['rank_shares']
    if any(v<=0 for v in shares.values()) or not math.isclose(sum(shares.values()),1):
        raise ValueError('Competition rank shares must be positive and sum to one')
    return '低' if fraction<shares['low'] else '中' if fraction<shares['low']+shares['medium'] else '高'


def strategies(c,values):
    p1={r['channel_id'] for r in c['l1']['items'] if r['priority']=='P1'}
    totals={z['zone_id']:sum(v for k,v in z['counts'].items() if k in p1) for z in c['l2']['items']}
    selected=[totals[z['zone_id']] for z in c['l2']['items'] if z['selected']]
    config=c['params']['competition']
    result={}
    for z,n in values.items():
        level=intensity(sum(n.values()),c,values)
        top=bool(selected) and totals[z]>0 and sum(v>totals[z] for v in selected)/len(selected)<config['p1_top_share']
        result[z]=config['high_p1_strategy'] if level=='高' and top else config['strategies'][level]
    return result


def reason(n):return '缓存中记录的饮品可得性点位：'+'、'.join(f'{label}{count}处' for label,count in sorted(n.items()))+'。这些计数是代理指标，非竞品实测，不代表店内在售品牌或市场份额。'


def preview(c,zones):
    context={**c,'l2':zones};values=counts(context)
    return [dict(zone_id=z,counts=n,intensity=intensity(sum(n.values()),context,values),assumption=c['params']['competition']['source']) for z,n in values.items()]


def run(c):
    values=counts(c);mapped=strategies(c,values)
    def rules():
        return dict(decision='依据缓存计数提出切入假设，零点位不代表无竞争。',items=[dict(zone_id=z,
            strategy=mapped[z],reason=reason(n),
            **meta(c,['L2:'+z,'params.competition'],opened=[
                '来源：params.competition；'+c['params']['competition']['source'],
                '来源：高德关键词缓存；点位不证明终端现售品牌或真实市场份额；采样截断见market.coverage。',
                *c.get('market',{}).get('open', ['来源：未采集竞品缓存；未找到公开来源的其他竞品待补充。'])])) for z,n in values.items()])
    a=decide(c,'L2C',{'counts':values,'allowed_strategies':mapped,'allowed_reasons':{z:reason(n) for z,n in values.items()}},rules,
             '返回decision,items；每项zone_id,strategy(补位/正面/避让),reason,evidence,judged_by,confidence,open；strategy必须等于allowed_strategies，reason必须等于allowed_reasons。不得推断销量。')
    for r in a['items']:
        if r['zone_id'] in values:
            n=values[r['zone_id']];r.update(counts=n,intensity=intensity(sum(n.values()),c,values))
    return envelope(c,a['decision'],a['items'])


def references(a,c):return equal_ids(a['items'],'zone_id',counts(c))


def business(a,c):
    out=[];values=counts(c);mapped=strategies(c,values)
    for r in a['items']:
        n=values[r['zone_id']]
        if r['counts']!=n or r['intensity']!=intensity(sum(n.values()),c,values):out.append(violation('competition_recount','items','竞争计数/强度与代码重算不符'))
        if r['strategy']!=mapped[r['zone_id']]:out.append(violation('competition_strategy','strategy','策略与配置及P1排名重算不符'))
        if r['reason']!=reason(n):out.append(violation('competition_reason','reason','理由只能引用计数'))
    return out


def inspect(a,c):return evaluate('L2C',a,c,Competition,references,business)
def check(a,c):return errors(inspect(a,c))
