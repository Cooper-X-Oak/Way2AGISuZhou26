"""Decision ergonomics regressions, using synthetic fixtures only."""
from copy import deepcopy
import json
import pytest
from pipeline import l3,l4
from pipeline.io import read
from runtime.assembly import assemble, decision_data, groups, map_violations, review_rows
from runtime.tools import Toolbox
from runtime.limits import size, pages
from runtime.line import load_line
from runtime.agent import Agent
from runtime.decisions import SCHEMAS
from test_pipeline import raw, contexts
from test_agent_runtime import Script, decisions


def test_all_production_budgets_and_small_contracts():
    stages,_=load_line()
    assert sum(s.token_budget for s in stages)<=300000
    assert all(s.assembler=='runtime.assembly.assemble' for s in stages)
    assert SCHEMAS['L1'].model_json_schema()['properties']['items']['maxItems']==10
    assert SCHEMAS['L2'].model_json_schema()['properties']['items']['maxItems']==12
    assert SCHEMAS['L4'].model_json_schema()['properties']['items']['maxItems']==8


def test_unseen_real_id_not_implicitly_registered(contexts):
    c=contexts['L1'];c['judge']=Script([])
    t=Toolbox(c,'L1',{},['decision_data','read_result','submit_artifact'])
    channel=c['profile']['channels'][0]['id']
    d={'items':[dict(channel_id=channel,priority='P1',reason='fixture',refs=[])]}
    t.call('submit_artifact',{'payload':d})
    assert t.submission_errors[0]['code']=='unseen_reference'
    t.call('decision_data',{})
    t.call('submit_artifact',{'payload':d})
    assert not t.submission_errors
    d['items'][0]['refs']=['poi:'+c['pois'][-1]['id']]
    t.call('submit_artifact',{'payload':d})
    assert t.submission_errors[0]['code']=='unseen_reference'


def test_truncation_and_cursor_lossless_and_seen_only_displayed(contexts):
    c=contexts['L1'];t=Toolbox(c,'L1',{},['read_result'])
    t.values['fixture']=[{'poi_id':p['id'],'text':'长文本'*1000} for p in c['pois']]
    r=t.call('read_result',{'ref':'fixture'})
    assert size(r)<=1500
    assert 'poi:'+c['pois'][-1]['id'] not in t.seen
    cursor=r['next_cursor'];records=list(r['records'])
    while cursor:
        r=t.call('read_result',{'cursor':cursor});assert size(r)<=1500
        data=r['data'];records.extend(data['records']);cursor=data['next_cursor']
    assert 'poi:'+c['pois'][-1]['id'] in t.seen
    assert records==[row for page in pages(t.read_result('fixture')) for row in page]
    with pytest.raises(ValueError):t.call('read_result',{'cursor':'fixture:-1'})


def test_assembler_violation_maps_to_template(contexts):
    c=contexts['L4'];c['judge']=Script([])
    # Explicit model-authored synthetic template, independent of final row count.
    templates=[]
    for k,g in groups(c).items():
        templates.append(dict(group_id=k,pitch='fixture',table_card='fixture',selling_point_ids=g['selling_point_ids']))
    artifact,mapping,provenance=assemble('L4',{'items':templates},c)
    c['assembly_provenance']={'L4':provenance}
    report=l4.inspect(artifact,c)
    mapped=map_violations(report['violations'],mapping)
    first=next(v for v in mapped if v['code']=='strategy_pitch')
    assert first['decision_path']=='items[0]'
    assert not report['passed']
    assert artifact['items'][0]['open'] and '来源' in artifact['items'][0]['open'][0]


def test_review_changes_recompute_without_mutating_source_or_upstream(contexts):
    c=contexts['L3'];c['judge']=Script([])
    before=deepcopy(c['pois']);zones=deepcopy(c['l2']);reviews=review_rows(c)
    assert reviews
    d={'items':[dict(poi_id=r['poi_id'],channel_id=r['channel_id'],exclude=False,reason='fixture review') for r in reviews]}
    d['items'][0]['exclude']=True
    a,mapping,provenance=assemble('L3',d,c);c['assembly_provenance']={'L3':provenance}
    chosen=next(r for r in a['items'] if r['poi_id']==reviews[0]['poi_id'])
    assert chosen['grade']=='C' and chosen['next_action']=='暂缓'
    assert c['pois']==before and c['l2']==zones
    assert all(r['judged_by']=='rules' for r in a['items'] if r['poi_id'] not in c['terminal_reviews'])
    # Integrity checks still detect numerical tampering after a valid review.
    chosen['score']=-1
    assert not l3.inspect(a,c)['passed']


def test_context_compaction_retains_actual_schema_not_reminder(raw):
    stages,catalog=load_line();stage=stages[0]
    a=Agent(Script([]),lambda *a,**k:None)
    a.enter(stage,SCHEMAS[stage.name],catalog,{})
    t=Toolbox(raw,stage.name,{},stage.tools_allowed)
    a.messages.extend([{'role':'user','content':'x'*15000}, {'role':'user','content':'按decision_schema提交'}])
    a.bound_context(t,'L0')
    assert any('"workshop"' in m['content'] for m in a.messages)
    assert any('"fields"' in m['content'] for m in a.messages)
    assert size(dict(messages=a.messages,tools=t.definitions()))<=7600


def test_duplicate_decisions_cannot_replace_required_worklist(contexts):
    c=contexts['L2'];a=__import__('pipeline.l2',fromlist=['run']).run(c)
    d=decisions('L2',c,a);d['items'].append(deepcopy(d['items'][0]))
    with pytest.raises(ValueError):assemble('L2',d,c)
