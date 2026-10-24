"""Candidate partners and geometric coverage; supply capability remains unverified."""
from pipeline.common import decide
from pipeline.geo import distance
from pipeline.contracts import violation
from pipeline.v2_contracts import Partners
from pipeline.quality import meta,envelope,evaluate,errors,equal_ids


def candidates(c):return {p['id']:p for p in c.get('market',{}).get('partners',[])}


def kind(p,c):
    text=(p.get('name') or '')+' '+p.get('tags',{}).get('type','')
    code=p.get('tags',{}).get('typecode','')
    for rule in c['params']['partners']['classification']:
        if any(s in text for s in rule['keywords']) or code in rule['typecodes']:return rule['kind']
    return '其他'


def coverage(p,c):
    pois={p['id']:p for p in c['pois']}
    return sorted(t['poi_id'] for t in c['l3']['items'] if t['grade'] in ('A','B') and
        distance(p['lat'],p['lon'],pois[t['poi_id']]['lat'],pois[t['poi_id']]['lon'])<=c['params']['partners']['coverage_radius_m'])


def recommendation(n,c):return '优先' if n>=c['params']['partners']['priority_min'] else '备选' if n else '暂缓'
def reason(p,k,ids):return f"POI={p['id']}；名称/类型规则分类={k}；3000米内A/B终端数={len(ids)}；供货资质待核实。"


def run(c):
    rows=candidates(c)
    def rules():
        return dict(decision='按名称/类型判断候选伙伴，覆盖和推荐等级由代码重算。',items=[dict(partner_id=pid,type=kind(p,c),
            reason=reason(p,kind(p,c),coverage(p,c)),**meta(c,['poi:'+pid,'params.partners'],opened=[
                '来源：高德POI名称/类型；候选供货资质、品牌授权、配送能力和合作条件待首访核实。',
                '来源：params.partners；3000米球面距离为覆盖假设，不是道路服务承诺。',
                *c.get('market',{}).get('open',[])])) for pid,p in rows.items()])
    a=decide(c,'L3P',{'candidates':[{**p,'allowed_type':kind(p,c),'allowed_reason':reason(p,kind(p,c),coverage(p,c))} for p in rows.values()]},rules,
             '返回decision,items；每项partner_id,type,reason,evidence,judged_by,confidence,open；type只能酒水批发/餐饮供应链/品牌自有网点/其他，按allowed_type，reason用allowed_reason，不生成覆盖数。')
    for r in a['items']:
        if r['partner_id'] in rows:
            ids=coverage(rows[r['partner_id']],c)
            r.update(covered_terminal_ids=ids,coverage_count=len(ids),recommendation=recommendation(len(ids),c))
    return envelope(c,a['decision'],a['items'])


def references(a,c):return equal_ids(a['items'],'partner_id',candidates(c))


def business(a,c):
    out=[];rows=candidates(c)
    for r in a['items']:
        p=rows[r['partner_id']];ids=coverage(p,c);k=kind(p,c)
        expected=dict(type=k,covered_terminal_ids=ids,coverage_count=len(ids),recommendation=recommendation(len(ids),c),reason=reason(p,k,ids))
        if any(r[key]!=value for key,value in expected.items()):out.append(violation('partner_recount','items','伙伴分类、覆盖、推荐或理由与证据重算不符'))
    return out


def assignments(c):
    pois={p['id']:p for p in c['pois']};partners=candidates(c);result={}
    for t in c['l3']['items']:
        if t['grade'] not in ('A','B'):continue
        p=pois[t['poi_id']]
        options=[r for r in c['l3p']['items'] if t['poi_id'] in r['covered_terminal_ids']]
        def order(r):
            q=partners[r['partner_id']]
            return ({'优先':0,'备选':1,'暂缓':2}[r['recommendation']],distance(p['lat'],p['lon'],q['lat'],q['lon']),r['partner_id'])
        best=min(options,key=order) if options else None
        result[t['poi_id']]=dict(partner_id=best['partner_id'] if best else None,
            status='候选供货方待核实' if best else '需新招经销商',zone_id=t['zone_id'],
            distance_m=round(order(best)[1],2) if best else None)
    return result


def gaps(c):
    rows=[(pid,r) for pid,r in assignments(c).items() if r['partner_id'] is None]
    return dict(terminal_count=len(rows),zone_ids=sorted({r['zone_id'] for _,r in rows}),terminal_ids=sorted(pid for pid,_ in rows))


def inspect(a,c):return evaluate('L3P',a,c,Partners,references,business)
def check(a,c):return errors(inspect(a,c))
