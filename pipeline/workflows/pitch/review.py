import re
from pipeline.common import scan
from pipeline.contracts import violation
from pipeline.v2_contracts import Materials
from pipeline.quality import evaluate,errors
from .context import active,targets,angles,channel_for_target

def references(a,c):
    out=[];seen=[];covered=set()
    points={s['id']:s for s in c['profile']['selling_points']}
    for r in a['items']:
        ch=channel_for_target(r['target'],c);seen.append(r['target'])
        if not ch:out.append(violation('target','target','物料目标不是上游业态或终端'));continue
        covered.add(ch['channel_id'])
        if not set(r['selling_point_ids'])<=set(ch['selling_point_ids']):out.append(violation('point','selling_point_ids','卖点不在该业态A1中'))
        elif any(not set(points[s]['scene_ids']) & set(ch['scene_ids']) for s in r['selling_point_ids']):out.append(violation('point_scene','selling_point_ids','卖点不属于该业态场景'))
    if len(seen)!=len(set(seen)) or covered!={ch['channel_id'] for ch,_ in targets(c).values()}:out.append(violation('material_set','items','业态物料缺失或目标重复'))
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


def inspect(a,c):return evaluate('L4',a,c,Materials,references,business)
def check(a,c):return errors(inspect(a,c))
