"""L1D 协议测试：脚本化假模型 + 假高德响应（测试替身，非真实商户）。

验证的是循环与关卡逻辑：工具被调用、结果回到模型、补采只在通过后合并、
错误结论会被打回。不证明真实模型或真实高德可用——那需要手动集成运行。
"""
import json
import math
from pathlib import Path

import pytest

from pipeline.engine import execute
from pipeline.judge import Judge
from pipeline.profiles import load, parameters

ROOT = Path(__file__).resolve().parents[1]
REQUEST = '苏州高新区周边3公里，2个地推，做4周'


def site():
    return json.loads((ROOT / 'data/areas/shishan/site.json').read_text(encoding='utf-8'))


def fake_poi(i, name, typecode, type_text, dx_m, dy_m):
    s = site()
    lat = s['lat'] + dy_m / 111_000
    lon = s['lon'] + dx_m / (111_000 * math.cos(math.radians(s['lat'])))
    return {'id': f'TESTFAKE{i:04d}', 'name': name, 'type': type_text, 'typecode': typecode,
            'location': f'{lon:.6f},{lat:.6f}', 'tel': '000-should-be-dropped'}


def replay():
    """Fake recorded pages keyed exactly like pipeline.__main__.load_replay."""
    from pipeline.amap import basic_payload
    bakery = [fake_poi(i, f'测试替身面包坊{i}', '050800', '餐饮服务;糕饼店;糕饼店', 30 * (i % 4) + 20, 30 * (i // 4) + 20) for i in range(12)]
    shops = [fake_poi(100 + i, f'测试替身便利店{i}', '060200', '购物服务;便民商店/便利店;便民商店/便利店', 40 * (i % 6) + 30, 40 * (i // 6) + 30) for i in range(18)]
    page = lambda rows: basic_payload({'status': '1', 'info': 'OK', 'infocode': '10000', 'count': str(len(rows)), 'pois': rows})
    key = lambda **q: json.dumps(q, sort_keys=True, ensure_ascii=False)
    return {key(keywords='面包', page=1): page(bakery), key(keywords='便利店', page=1): page(shops)}


def call(name, args, n):
    return {'id': f'call_{n}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}


class ScriptedModel:
    """Plays a fixed tool plan, then answers from the last area_inventory it observed."""

    def __init__(self, plan, wrong_first=False):
        self.plan, self.turn, self.wrong_first, self.payloads = list(plan), 0, wrong_first, []
        self.model = 'scripted-test-model'

    def __call__(self, payload):
        self.payloads.append(payload)
        self.turn += 1
        if self.plan:
            name, args = self.plan.pop(0)
            return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [call(name, args, self.turn)]}}]}
        inventory = next(json.loads(m['content']) for m in reversed(payload['messages'])
                         if m['role'] == 'tool' and 'by_channel' in m['content'] and 'radius_m' in m['content'])
        counts = inventory['by_channel']
        if self.wrong_first:
            self.wrong_first = False
            counts = {k: v + 1 for k, v in counts.items()}
        final = {'decision': '补采后两个渠道均有候选。', 'confidence': 0.8,
                 'items': [{'channel_id': k, 'candidate_count': v, 'status': 'acquired' if v else 'insufficient_after_search',
                            'evidence': [], 'reason': '高德补采结果'} for k, v in counts.items()]}
        return {'choices': [{'message': {'role': 'assistant', 'content': '```json\n' + json.dumps(final, ensure_ascii=False) + '\n```'}}]}


PLAN = [('area_inventory', {}), ('search_merchants', {'keywords': '面包'}), ('search_merchants', {'keywords': '便利店'}), ('area_inventory', {})]


def inputs(profile, **extra):
    from pipeline.__main__ import prepare_data
    from pipeline.market_capture import load_market
    params = parameters()
    pois, coverage = prepare_data(params, 'shishan')
    return {'request': REQUEST, 'site': coverage['site'], 'area_id': 'shishan', 'pois': pois, 'coverage': coverage,
            'params': params, 'judge': Judge(offline=True), 'profile': load(profile), 'demo_replay': None,
            'auto_approve': True, 'no_defaults': False, 'market': load_market('shishan'), **extra}


@pytest.fixture(autouse=True)
def at_root(monkeypatch):
    monkeypatch.chdir(ROOT)


def read(directory, name):
    from pipeline.io import read as verified
    return verified(directory / name)


def test_agent_acquires_missing_channel_and_pipeline_completes(tmp_path):
    model = ScriptedModel(PLAN)
    directory, state = execute(inputs('bread-trial', agent_l1d=True, llm_post=model, amap_replay=replay()), root=tmp_path)
    assert state['status'] == 'completed', state.get('violations')
    assert state['completed_stages'][:3] == ['L0', 'L1', 'L1D']
    d = read(directory, 'discovery.json')
    assert d['judged_by'] == 'llm' and d['added_poi_ids']
    assert all(p['acquired_by'] == 'agent:L1D' and 'tel' not in p for p in d['added_pois'])
    assert d['agent']['tool_calls'] == 4 and d['agent']['amap_calls'] == 0  # replay consumes no quota
    terminals = read(directory, 'terminals.json')['items']
    assert {t['channel_id'] for t in terminals} & {'bakery', 'convenience'}
    assert {t['poi_id'] for t in terminals} <= {p['id'] for p in read(directory, 'pois.json')}
    tools = model.payloads[0]['tools']
    assert {t['function']['name'] for t in tools} == {'area_inventory', 'list_merchants', 'search_merchants'}
    assert any(m['role'] == 'tool' for m in model.payloads[-1]['messages'])  # results went back to the model
    assert (directory / 'agent' / 'steps.jsonl').is_file()


def test_wrong_count_is_pushed_back_to_model(tmp_path):
    model = ScriptedModel(PLAN, wrong_first=True)
    directory, state = execute(inputs('bread-trial', agent_l1d=True, llm_post=model, amap_replay=replay()), root=tmp_path)
    assert state['status'] == 'completed'
    rejected = [m for m in model.payloads[-1]['messages'] if m['role'] == 'user' and '未通过检查' in (m['content'] or '')]
    assert rejected and '与代码复算' in rejected[0]['content']


def test_unrecorded_query_without_key_reports_and_lists_recordings(tmp_path):
    plan = [('search_merchants', {'keywords': '烘焙'}), ('area_inventory', {})]
    model = ScriptedModel(plan)
    directory, state = execute(inputs('bread-trial', agent_l1d=True, llm_post=model, amap_replay=replay()), root=tmp_path)
    tool_msgs = [m['content'] for m in model.payloads[1]['messages'] if m['role'] == 'tool']
    assert '没有与该查询完全匹配的录制数据' in tool_msgs[0] and '面包' in tool_msgs[0]
    d = read(directory, 'L1D.json')
    assert d['added_poi_ids'] == []
    # Nothing acquired: L2 has no target zones and stops with an explicit reason, never fabricating merchants.
    assert state['status'] == 'blocked' and state['stopped_at'] == 'L2'


@pytest.mark.skipif(not (ROOT / '.work/handbook/dongting-brief.md').is_file(),
                    reason='jiangnan-gongquan 原文在被忽略的 .work/handbook（见 #20）；可用 docs/runtime-review/reproduce.py --prepare-sources 准备')
def test_rules_mode_inserts_l1d_without_changing_existing_profile(tmp_path):
    directory, state = execute(inputs('jiangnan-gongquan'), root=tmp_path)
    assert state['status'] == 'completed'
    d = read(directory, 'discovery.json')
    assert d['judged_by'] == 'rules' and d['added_pois'] == []
    assert all(r['status'] == 'sufficient' for r in d['items'])


def test_bread_offline_has_no_candidates_in_cache(tmp_path):
    directory, state = execute(inputs('bread-trial'), root=tmp_path)
    d = read(directory, 'L1D.json')
    assert all(r['status'] == 'not_searched_offline' and r['candidate_count'] == 0 for r in d['items'])
    assert state['stopped_at'] == 'L2'
