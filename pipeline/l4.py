from pipeline.common import decide,scan
from pipeline.contracts import violation
import re
from pipeline.v2_contracts import Materials
from pipeline.quality import meta,envelope,evaluate,errors


def active(c):return {r['channel_id']:r for r in c['l1']['items'] if r['priority'] in ('P1','P2')}


ANGLES={'补位':'先了解尚未满足的用水需求，讨论补充陈列。','正面':'围绕本品资料介绍特点，邀请核对样品与进货条件。','避让':'先做需求访谈并记录后续跟进条件，暂不推动替换现有陈列。'}


def angles(c):
    """Profile may override wording per strategy; default keeps the legacy water copy."""
    return {**ANGLES,**(c['profile'].get('material',{}).get('angles') or {})}


def targets(c):
    channels=active(c);zones={r['zone_id']:r for r in c['l2c']['items']}
    result={t['poi_id']:(channels[t['channel_id']],zones[t['zone_id']]['strategy']) for t in c['l3']['items'] if t['grade'] in ('A','B') and t['channel_id'] in channels}
    covered={ch['channel_id'] for ch,_ in result.values()}
    result.update({key:(ch,'补位') for key,ch in channels.items() if key not in covered})
    return result


def run(c):
    if c.get('agent_l4'):
        # Live tasks: the model writes and self-checks pitches; the same checks below decide completion.
        import sys
        from pipeline import l4_agent
        return l4_agent.run(c,sys.modules[__name__])
    points={r['id']:r for r in c['profile']['selling_points']}
    scenes={r['id']:r for r in c['profile']['scenes']}
    def rules():
        items=[]
        for key,(ch,strategy) in targets(c).items():
            ids=ch['selling_point_ids'];fact='；'.join(points[s]['fact'] for s in ids)
            scene='、'.join(scenes[s]['name'] for s in ch['scene_ids'])
            template=c['profile'].get('material',{}).get('pitch_template')
            if template:pitch=template.format(scene=scene,product=c['profile']['product']['name'],fact=fact)
            else:pitch=f"老板您好，针对{scene}场景，可把{c['profile']['product']['name']}作为顾客用水的一个选择。资料原文：{fact}。建议先询问客人的用水需求，再介绍产品；是否增加点单需要试陈列验证。您可以核对进货成本与售价后再评估毛利，价格带、首单政策由品牌方和您确认，不承诺利润或销量。"
            pitch+=angles(c)[strategy]
            items.append(dict(target=key,pitch=pitch,table_card=c['profile']['material']['table_copy'].format(product=c['profile']['product']['name']),selling_point_ids=ids,
                              **meta(c,['L1:'+ch['channel_id']]+['selling_point:'+s for s in ids],opened=['来源：档案未提供商业条件；毛利、价格带和首单政策待确认；增量点单是待验证假设。'])))
        return dict(decision='按业态准备店主沟通话术与桌牌，不承诺销量或利润。',items=items)
    decision=decide(c,'L4',{'profile':c['profile'],'channels':c['l1'],'terminals':c['l3'],'competition':c['l2c'],
                    'required_targets':{key:dict(channel=ch['channel_id'],strategy=s,required_angle=angles(c)[s]) for key,(ch,s) in targets(c).items()}},rules,
                    '返回decision,items；每项target,pitch,table_card,selling_point_ids,evidence,judged_by,confidence,open；required_targets各一次；pitch必须包含required_angle；老板视角、讲点单机会和成本核算，不编造毛利价格政策；卖点引用原文；禁止功效、点名或贬低竞品。')
    seeded=c.get('demo_replay')=='compliance' and c.get('attempt',1)==1
    if seeded and decision['items']:
        decision['items'][0]['pitch']+=' 最解腻，护血管。'
        if c['profile'].get('competitors'):decision['items'][0]['pitch']+=' 对比'+c['profile']['competitors'][0]['brand']+'。'
    for r in decision['items']:
        hits=scan(r,c['profile'])
        r['compliance']={'passed':not hits,'hits':[v.model_dump() for v in hits]}
    return envelope(c,decision['decision'],decision['items'],seeded_for_demo=seeded)


def channel_for_target(target,c):
    if target in active(c):return active(c)[target]
    terminal=next((r for r in c['l3']['items'] if r['poi_id']==target),None)
    return active(c).get(terminal['channel_id']) if terminal else None


def references(a,c):
    out=[];seen=[];covered=set()
    points={s['id']:s for s in c['profile']['selling_points']}
    for r in a['items']:
        ch=channel_for_target(r['target'],c);seen.append(r['target'])
        if not ch:out.append(violation('target','target','物料目标不是上游业态或终端'));continue
        covered.add(ch['channel_id'])
        if not set(r['selling_point_ids'])<=set(ch['selling_point_ids']):out.append(violation('point','selling_point_ids','卖点不在该业态A1中'))
        elif any(not set(points[s]['scene_ids']) & set(ch['scene_ids']) for s in r['selling_point_ids']):out.append(violation('point_scene','selling_point_ids','卖点不属于该业态场景'))
    if len(seen)!=len(set(seen)) or covered!=set(active(c)):out.append(violation('material_set','items','业态物料缺失或目标重复'))
    if set(seen)!=set(targets(c)):out.append(violation('strategy_targets','items','必须为A/B终端及未覆盖业态逐项提供物料'))
    return out


def business(a,c):
    out=scan({k:v for k,v in a.items() if k!='items'},c['profile']);points={s['id']:s for s in c['profile']['selling_points']}
    for i,r in enumerate(a['items']):
        strategy=targets(c)[r['target']][1]
        if angles(c)[strategy] not in r['pitch']:out.append(violation('strategy_pitch',f'items[{i}].pitch','话术未体现所在网格切入策略'))
        content={k:v for k,v in r.items() if k!='compliance'}
        hits=scan(content,c['profile'])
        out.extend(scan(content,c['profile'],f'items[{i}]'))
        if r['compliance']!={'passed':not hits,'hits':[v.model_dump() for v in hits]}:out.append(violation('compliance_record',f'items[{i}].compliance','合规结果必须由扫描生成'))
        if any(points[s]['fact'] not in r['pitch'] for s in r['selling_point_ids']):out.append(violation('fact','pitch','话术未保留引用卖点原文'))
        if not all(t in r['pitch'] for t in ('成本','售价','确认')):out.append(violation('owner_perspective','pitch','必须解释成本售价需确认，不能编造利润'))
        commercial_text=r['pitch']+' '+r['table_card']
        for point in points.values():commercial_text=commercial_text.replace(point['fact'],'')
        if re.search(r'[¥￥]|[零一二三四五六七八九十百千万\d.]+\s*(?:元|块|%|％|折)|买[一二三四五六七八九十\d]+送|免费赠|保底利润|保证销量',commercial_text):
            out.append(violation('commercial_claim',f'items[{i}]','物料包含档案未提供的价格、毛利、折扣或首单承诺'))
    expected=c.get('demo_replay')=='compliance' and c.get('attempt',1)==1
    if a['seeded_for_demo']!=expected:out.append(violation('seed','seeded_for_demo','演示标记不符'))
    return out


def inspect(a,c):
    if isinstance(a,dict) and a.get('agent'):
        from pipeline.l4_agent import llm_context
        c=llm_context(c,a['agent'].get('model','agent'))
    return evaluate('L4',a,c,Materials,references,business)
def check(a,c):return errors(inspect(a,c))
