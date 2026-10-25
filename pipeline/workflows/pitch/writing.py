from pipeline.common import decide,scan
from pipeline.quality import meta,envelope
from .context import targets,angles
from .research import instructions

def compose(c, research):
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
    decision=decide(c,'L4',{'research':research,'skill_instructions':instructions(),'profile':c['profile'],'channels':c['l1'],'terminals':c['l3'],'competition':c['l2c'],
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
