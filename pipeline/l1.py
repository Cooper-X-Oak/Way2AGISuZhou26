from pipeline.common import decide
from pipeline.contracts import violation
from pipeline.v2_contracts import Channels
from pipeline.quality import meta, envelope, evaluate, errors, equal_ids, check_compliance


def run(c):
    p=c['profile']
    def rules():
        items=[]
        for ch in p['channels']:
            points=[s['id'] for s in p['selling_points'] if set(s['scene_ids']) & set(ch['scene_ids'])]
            items.append(dict(channel_id=ch['id'],scene_ids=ch['scene_ids'],selling_point_ids=points,
                priority=('P3' if ch['id']=='restaurant_other' else next((key for key in ('P1','P2','P3') if ch['rules_fit']>=c['params']['priority_cutoffs'][key]),'excluded')),reason=ch['rules_reason'],
                commercial_conditions=dict(channel_margin=None,price_band=None,first_order_policy=None),
                **meta(c,['profile_channel:'+ch['id']]+['scene:'+s for s in ch['scene_ids']]+['selling_point:'+s for s in points],
                       opened=['来源：'+ch['source'], '来源：档案 selling_points.mapping_source；卖点适用场景为项目映射假设。', '来源：产品档案未提供渠道毛利、价格带、首单政策；由品牌方/经销商确认。'])))
        covered={s for r in items for s in r['scene_ids']}
        return envelope(c,'按档案场景选择渠道；优先级为待验证的渠道假设。',items,
                        excluded_scenes=[dict(scene_id=s['id'],reason='当前档案候选业态没有直接对应渠道，暂不进入本轮铺市。') for s in p['scenes'] if s['id'] not in covered])
    return decide(c,'L1',{'profile':p,'constraints':{'restaurant_other':'最高P3；业态不明，不能当作场景匹配的证据；场景和卖点仅待核实假设'}},rules,Channels.model_json_schema())


def references(a,c):
    p=c['profile']; scenes={s['id'] for s in p['scenes']}; points={s['id']:s for s in p['selling_points']}
    out=equal_ids(a['items'],'channel_id',[s['id'] for s in p['channels']])
    for r in a['items']:
        if not set(r['scene_ids'])<=scenes or not set(r['selling_point_ids'])<=points.keys():
            out.append(violation('reference','items','场景或卖点不存在'))
        elif any(not set(points[s]['scene_ids']) & set(r['scene_ids']) for s in r['selling_point_ids']):
            out.append(violation('point_scene','selling_point_ids','卖点不适用于引用场景'))
    if not {s['scene_id'] for s in a['excluded_scenes']}<=scenes:out.append(violation('scene','excluded_scenes','排除场景不存在'))
    return out


def business(a,c):
    out=check_compliance(a,c)
    covered={s for r in a['items'] for s in r['scene_ids']}
    excluded=[s['scene_id'] for s in a['excluded_scenes']]
    if covered|set(excluded)!={s['id'] for s in c['profile']['scenes']} or covered & set(excluded) or len(excluded)!=len(set(excluded)):
        out.append(violation('scene_coverage','excluded_scenes','场景必须被引用或明确排除，不能重复'))
    for r in a['items']:
        if r['channel_id']=='restaurant_other' and r['priority'] not in ('P3','excluded'):
            out.append(violation('unknown_channel_cap','priority','业态不明，不能当作场景匹配的证据；最高P3'))
        if any(v is not None for v in r['commercial_conditions'].values()):
            out.append(violation('commercial','commercial_conditions','档案没有商业条件，不能编造'))
        if not all(any(term in s for s in r['open']) for term in ('毛利','价格带','首单')):
            out.append(violation('commercial_open','open','缺失的商业条件必须记录'))
    return out


def inspect(a,c):return evaluate('L1',a,c,Channels,references,business)
def check(a,c):return errors(inspect(a,c))
