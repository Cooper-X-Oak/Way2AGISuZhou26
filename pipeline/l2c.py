"""Observed competitor POIs, never market share or inferred restaurant stock."""
import json
from pipeline.common import decide
from pipeline.geo import grid_id, distance
from pipeline.contracts import violation
from pipeline.v2_contracts import Competition
from pipeline.quality import meta, envelope, evaluate, errors, equal_ids


def counts(c, zones=None):
    brands=[r['brand'] for r in c['profile'].get('competitors', [])]
    result={z['zone_id']:{b:0 for b in brands} for z in (zones or c['l2'])['items']}
    seen=set()
    for p in c.get('market', {}).get('competitors', []):
        if p['id'] in seen:continue
        seen.add(p['id'])
        if distance(c['brief']['lat'],c['brief']['lon'],p['lat'],p['lon'])>c['brief']['radius_m']:continue
        z=grid_id(p,c['brief'])
        if z in result:
            for b in set(p['brands']) & set(brands):result[z][b]+=1
    return result


def intensity(n,c):
    thresholds=c['params']['competition']
    return '高' if n>=thresholds['high_min'] else '中' if n>=thresholds['medium_min'] else '低'


def reason(n):return '缓存竞品点位计数='+json.dumps(n,ensure_ascii=False,sort_keys=True)+'；仅为观测数量。'


def preview(c,zones):
    return [dict(zone_id=z,counts=n,intensity=intensity(sum(n.values()),c),assumption=c['params']['competition']['source']) for z,n in counts(c,zones).items()]


def run(c):
    values=counts(c)
    def rules():
        return dict(decision='依据缓存计数提出切入假设，零点位不代表无竞争。',items=[dict(zone_id=z,
            strategy={'低':'补位','中':'正面','高':'避让'}[intensity(sum(n.values()),c)],reason=reason(n),
            **meta(c,['L2:'+z,'params.competition'],opened=[
                '来源：params.competition；强度阈值与切入策略均为项目假设。',
                '来源：高德关键词缓存；点位不证明终端现售品牌或真实市场份额；采样截断见market.coverage。',
                *c.get('market',{}).get('open', ['来源：未采集竞品缓存；未找到公开来源的其他竞品待补充。'])])) for z,n in values.items()])
    a=decide(c,'L2C',{'counts':values},rules,
             '返回decision,items；每项zone_id,strategy(补位/正面/避让),reason,evidence,judged_by,confidence,open；reason必须等于缓存竞品点位计数=加按键排序的JSON(ensure_ascii=False，默认空格)+；仅为观测数量。不得推断销量。')
    for r in a['items']:
        if r['zone_id'] in values:
            n=values[r['zone_id']];r.update(counts=n,intensity=intensity(sum(n.values()),c))
    return envelope(c,a['decision'],a['items'])


def references(a,c):return equal_ids(a['items'],'zone_id',counts(c))


def business(a,c):
    out=[];values=counts(c)
    for r in a['items']:
        n=values[r['zone_id']]
        if r['counts']!=n or r['intensity']!=intensity(sum(n.values()),c):out.append(violation('competition_recount','items','竞争计数/强度与代码重算不符'))
        if r['reason']!=reason(n):out.append(violation('competition_reason','reason','理由只能引用计数'))
    return out


def inspect(a,c):return evaluate('L2C',a,c,Competition,references,business)
def check(a,c):return errors(inspect(a,c))
