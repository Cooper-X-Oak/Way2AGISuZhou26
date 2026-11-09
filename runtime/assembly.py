"""Deterministic assembly, provenance and source maps for agent decisions.

No stage runners or hidden model calls. Unreviewed output remains labelled rules.
Existing business policies (rank selection, competition strategy, partner cover)
remain constraints on decisions rather than being silently overwritten.
"""
from copy import deepcopy
from collections import Counter
import re
from pipeline import l0, l2, l2c, l3, l3p, l4, pack
from pipeline.common import channel_for, scan
from pipeline.quality import meta, envelope
from runtime.decisions import SCHEMAS

COMMERCIAL = '来源：产品档案未提供渠道毛利、价格带、首单政策；由品牌方/经销商确认。'


def metadata(c, refs, actor='rules', opened=None):
    return {**meta(c, refs, opened=opened), 'judged_by': actor}


def groups(c):
    result = {}
    for target, (ch, strategy) in l4.targets(c).items():
        key = ch['channel_id'] + ':' + strategy
        row = result.setdefault(key, dict(group_id=key, channel=ch['channel_id'], strategy=strategy,
            required_angle=l4.ANGLES[strategy], selling_point_ids=ch['selling_point_ids'], targets=[]))
        row['targets'].append(target)
    return result


def review_rows(c):
    base = {**c, 'terminal_reviews': {}}
    scores = l3.ranked(base)
    rows = l3.candidates(base)
    top = {pid for pid in sorted(scores, key=lambda p: (-scores[p]['score'], p)) if scores[pid]['grade']=='A'}
    top = set(sorted(top, key=lambda p: (-scores[p]['score'], p))[:15])
    result = []
    for p, ch, z in rows:
        difficult = ch['channel_id']=='restaurant_other' and any(w in p['name'] for w in ('酒楼','宴','饭店','大酒店','餐厅'))
        if p['id'] in top or difficult:
            result.append(dict(poi_id=p['id'], name=p['name'], channel_id=ch['channel_id'],
                type=p.get('tags',{}).get('type'), typecode=p.get('tags',{}).get('typecode'),
                grade=scores[p['id']]['grade'], review='疑难业态' if difficult else 'A级前15'))
    return result


def decision_data(stage, c):
    if stage=='L0':
        supplied, assumptions = l0.facts(c)
        return dict(fields={k:supplied.get(k) for k in l0.REQUIRED}, assumptions=assumptions, request=c['request'])
    if stage=='L1':
        return [dict(channel_id=ch['id'], name=ch['name'], scene_ids=ch['scene_ids'],
                     source=ch['source'], reference='profile_channel:'+ch['id']) for ch in c['profile']['channels']]
    if stage=='L2':
        return [dict(zone_id=z, counts=n, rank=i+1, required_selected=z in l2.selection(c))
                for i,(z,n) in enumerate(l2.ordered(c)) if z not in c.get('excluded_zones',[])][:12]
    if stage=='L2C':
        values=l2c.counts(c); strategies=l2c.strategies(c, values)
        return [dict(zone_id=z['zone_id'], counts=values[z['zone_id']],
                     intensity=l2c.intensity(sum(values[z['zone_id']].values()),c,values),
                     required_strategy=strategies[z['zone_id']]) for z in c['l2']['items'] if z['selected']]
    if stage=='L3':
        return dict(grades=dict(Counter(r['grade'] for r in l3.ranked({**c,'terminal_reviews':{}}).values())),
                    channels=[dict(channel_id=x['id'],name=x['name']) for x in c['profile']['channels']],
                    items=review_rows(c))
    if stage=='L3P':
        selection=l3p.recommended(c); candidates=l3p.candidates(c)
        return [dict(zone_id=z, required_partner_ids=ids, candidates=[dict(partner_id=pid,
                    name=candidates[pid]['name'], type=l3p.kind(candidates[pid],c),
                    coverage_count=len(l3p.coverage(candidates[pid],c))) for pid in ids]) for z,ids in selection.items()]
    if stage=='L4':
        points={x['id']:x for x in c['profile']['selling_points']}
        return [dict(**{k:v for k,v in g.items() if k!='targets'}, total=len(g['targets']),
                     facts=[dict(id=p, fact=points[p]['fact']) for p in g['selling_point_ids']],
                     table_card=c['profile']['material']['table_copy'].format(product=c['profile']['product']['name']))
                for g in groups(c).values()]
    if stage=='PACK':
        return dict(grades=dict(Counter(r['grade'] for r in c['l3']['items'])),
            selected_zones=[z['zone_id'] for z in c['l2']['items'] if z['selected']],
            capacity=c['brief']['planned_visits_day'], gaps=l3p.gaps(c)['terminal_count'],
            refs=['profile:'+c['profile']['id']], planning_note=pack.NOTE)


def assemble(stage, decision, c):
    d=SCHEMAS[stage].model_validate(decision).model_dump()
    source_map={}; provenance={}; rows=[]
    def add(row, key, index=None):
        rows.append(row)
        provenance[str(key)]=row['judged_by']
        source_map[str(len(rows)-1)]=f'items[{index}]' if index is not None else 'assembler'
    if stage=='L0':
        supplied, assumptions=l0.facts(c)
        a={k:d['fields'].get(k) for k in l0.REQUIRED}
        if a['planned_visits_day'] is None and all(type(a[k]) is int for k in ('delivery_people','visits_per_person_day')):
            a['planned_visits_day']=a['delivery_people']*a['visits_per_person_day']
        a.update(questions=d['questions'], request=c['request'], site_note=c['site'].get('site_note',c['site']['location_note']),
                 location_note=c['site']['location_note'], assumptions=assumptions,
                 **metadata(c,['profile:'+c['profile']['id']],'llm',['来源：'+s for s in assumptions]))
        return a, {}, {}
    if stage=='L1':
        profiles={ch['id']:ch for ch in c['profile']['channels']}
        for i,r in enumerate(d['items']):
            ch=profiles[r['channel_id']]
            points=[s['id'] for s in c['profile']['selling_points'] if set(s['scene_ids'])&set(ch['scene_ids'])]
            add(dict(channel_id=ch['id'],priority=r['priority'],reason=r['reason'],scene_ids=ch['scene_ids'],selling_point_ids=points,
                commercial_conditions=dict(channel_margin=None,price_band=None,first_order_policy=None),
                **metadata(c,['profile_channel:'+ch['id']]+['scene:'+s for s in ch['scene_ids']]+['selling_point:'+s for s in points],
                    'llm',['来源：'+ch['source'],COMMERCIAL])),ch['id'],i)
        covered={s for r in rows for s in r['scene_ids']}
        extra=dict(excluded_scenes=[dict(scene_id=s['id'],reason='档案渠道未映射该场景，本轮不覆盖。') for s in c['profile']['scenes'] if s['id'] not in covered])
    elif stage=='L2':
        decisions={r['zone_id']:(i,r) for i,r in enumerate(d['items'])}
        if len(decisions)!=len(d['items']): raise ValueError('duplicate_decision')
        valid={x['zone_id'] for x in decision_data(stage,c)}
        if not set(decisions)<=valid or not set(l2.selection(c))<=set(decisions): raise ValueError('decision_set')
        profiles={r['id']:r for r in c['profile']['channels']}
        for rank,(z,n) in enumerate(l2.ordered(c),1):
            types={profiles[k]['grid_type'] for k,v in n.items() if v}
            i,r=decisions.get(z,(None,{}))
            add(dict(zone_id=z,counts=n,rank=rank,selected=r.get('selected',False),
                tier=r.get('tier','社区级'),type=r.get('type',next(iter(types)) if len(types)==1 else '混合' if types else '非目标'),
                **metadata(c,['L1:'+k for k,v in n.items() if v] or ['params.priority_weights'],
                    'llm' if r else 'rules',['来源：500米网格统计；层级为待现场核实假设。']+(['来源：模型网格复核；'+r['reason']] if r else []))),z,i)
        extra={}
    elif stage=='L2C':
        decisions={r['zone_id']:(i,r) for i,r in enumerate(d['items'])}
        if len(decisions)!=len(d['items']) or set(decisions)!={z['zone_id'] for z in c['l2']['items'] if z['selected']}: raise ValueError('decision_set')
        values=l2c.counts(c); strategies=l2c.strategies(c,values)
        for z,n in values.items():
            i,r=decisions.get(z,(None,{}))
            add(dict(zone_id=z,counts=n,intensity=r.get('intensity',l2c.intensity(sum(n.values()),c,values)),
                strategy=r.get('strategy',strategies[z]),reason=l2c.reason(n),
                **metadata(c,['L2:'+z,'params.competition'],'llm' if r else 'rules',[
                    '来源：params.competition；'+c['params']['competition']['source'],
                    '来源：高德缓存；仅为饮品可得性代理指标，非实测销量或份额。',*c.get('market',{}).get('open',[])]+(['来源：模型切入判断；'+r['reason']] if r else []))),z,i)
        extra={}
    elif stage=='L3':
        required={r['poi_id'] for r in review_rows(c)}
        if {r['poi_id'] for r in d['items']}!=required or len(d['items'])!=len(required): raise ValueError('decision_set')
        channels={x['id'] for x in c['profile']['channels']}
        if any(r['channel_id'] is not None and r['channel_id'] not in channels for r in d['items']): raise ValueError('unknown_channel')
        c['terminal_reviews']={r['poi_id']:r for r in d['items']}
        lookup=l3.ranked(c); indexes={r['poi_id']:i for i,r in enumerate(d['items'])}
        for p,ch,z in l3.candidates(c):
            pid=p['id']; opened=['来源：高德POI不提供店内在售品牌；现售佐餐饮料品牌待首访确认。']
            if pid in c['terminal_reviews']: opened.append('来源：模型依据POI名称/类型复核；'+c['terminal_reviews'][pid]['reason'])
            add(dict(poi_id=pid,channel_id=ch['channel_id'],zone_id=z['zone_id'],reason=l3.reason(p,ch,z,c),**lookup[pid],
                **metadata(c,['poi:'+pid,'L1:'+ch['channel_id'],'L2:'+z['zone_id'],'params.scoring'],
                    'llm' if pid in indexes else 'rules',opened)),pid,indexes.get(pid))
        extra={}
    elif stage=='L3P':
        choices={r['zone_id']:(i,r) for i,r in enumerate(d['items'])}; expected=l3p.recommended(c)
        if len(choices)!=len(d['items']) or set(choices)!=set(expected): raise ValueError('decision_set')
        selected={pid for r in d['items'] for pid in r['partner_ids']}
        if not selected<=l3p.candidates(c).keys(): raise ValueError('unknown_partner')
        for pid,p in l3p.candidates(c).items():
            zones=sorted(z for z,(_,r) in choices.items() if pid in r['partner_ids']); ids=l3p.coverage(p,c); k=l3p.kind(p,c)
            i=next((i for i,r in enumerate(d['items']) if pid in r['partner_ids']),None)
            add(dict(partner_id=pid,type=k,reason=l3p.reason(p,k,ids,c),covered_terminal_ids=ids,coverage_count=len(ids),
                recommended_zone_ids=zones,recommendation='优先' if zones else '暂缓',
                **metadata(c,['poi:'+pid,'params.partners'],'llm' if zones else 'rules',[
                    '来源：高德POI名称/类型；供货资质、品牌授权、配送能力和合作条件待首访核实。']+['来源：模型伙伴选择；'+choices[z][1]['reason'] for z in zones])),pid,i)
        extra={}
    elif stage=='L4':
        gs=groups(c); templates={r['group_id']:(i,r) for i,r in enumerate(d['items'])}
        if set(templates)!=set(gs) or len(templates)!=len(d['items']): raise ValueError('decision_set')
        for key,g in gs.items():
            i,t=templates[key]
            for target in g['targets']:
                row=dict(target=target,pitch=t['pitch'],table_card=t['table_card'],selling_point_ids=t['selling_point_ids'],
                    **metadata(c,['L1:'+g['channel']]+['selling_point:'+s for s in t['selling_point_ids']],'llm',[COMMERCIAL]))
                hits=scan(row,c['profile']);row['compliance']=dict(passed=not hits,hits=[v.model_dump() for v in hits])
                add(row,target,i)
        extra={'seeded_for_demo':False}
    else:
        days=pack.schedule(c)
        a=envelope(c,'；'.join(r['reason']+' ['+','.join(r['refs'])+']' for r in d['items']),c['l3']['items'],
            channels=c['l1'],zones=c['l2'],materials=c['l4'],days=days,backlog=pack.backlog(c,days),trace=pack.trace(c),
            coverage=c['coverage'],review_queue=[r for report in c['checks'].values() for r in report['review_queue']],planning_note=pack.NOTE,
            competition=c['l2c'],partners=c['l3p'],supply_assignments=l3p.assignments(c),recruitment_gaps=l3p.gaps(c))
        a['judged_by']='rules'
        return a,{},c.get('assembly_provenance',{}).get('L3',{})
    a=envelope(c,'模型决定已按数据组装；未复核行保持rules来源。',rows,**extra)
    return a,source_map,provenance


def map_violations(violations, mapping):
    result=[]
    for v in violations:
        match=re.search(r'(items|rows)(?:\[|\.)(\d+)',v['path'])
        index=int(match[2])-(1 if match and match[1]=='rows' else 0) if match else None
        path=mapping.get(str(index),'decision') if index is not None else 'decision'
        result.append({**v,'decision_path':path})
    return result
