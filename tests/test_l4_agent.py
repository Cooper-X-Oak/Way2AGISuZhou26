"""L4 怎么说 agent loop with a scripted model (not evidence of live model quality).

Runs the real engine on the recorded 新鲜活泉 × 狮山 case: L1D replays the recorded agent, L4 uses a
scripted model that lists targets, self-checks one bad draft, submits an incomplete batch (rejected by
the completion bar), then submits a complete batch.
"""
import json

from pipeline.demo import RECORDING, Replayer, build_inputs
from pipeline.engine import execute
from pipeline.__main__ import load_replay_pages


def message(content='', calls=()):
    return {'choices': [{'message': {'content': content, 'tool_calls': [
        {'id': f'c{i}', 'type': 'function', 'function': {'name': n, 'arguments': json.dumps(a, ensure_ascii=False)}}
        for i, (n, a) in enumerate(calls)]}}]}


def draft(t, product):
    facts = '；'.join(p['fact'] for p in t['selling_points'][:1])
    ids = [p['id'] for p in t['selling_points'][:1]]
    pitch = (f"老板您好，{'、'.join(t['scenes'])}这类场景，客人常需要一瓶水，可以把{product}放进选择里。资料原文：{facts}。"
             f"进货成本和售价请您核对确认后再评估。{t['required_angle']}")
    return dict(target=t['target'], pitch=pitch, table_card=f'本店提供{product}', selling_point_ids=ids)


class ScriptedModel:
    model = 'scripted-model'

    def __init__(self):
        self.calls = 0

    def __call__(self, payload):
        self.calls += 1
        msgs = payload['messages']
        if self.calls == 1:
            return message(calls=[('list_targets', {})])
        assert '技能 pitch-writing' in msgs[0]['content'] and '渠道兜底' in msgs[0]['content'], 'business method comes from the skill file'
        listing = next(json.loads(m['content']) for m in msgs if m['role'] == 'tool')
        assert not listing.get('truncated'), 'target list must reach the model whole'
        stores = [t for t in listing['targets'] if t['kind'] == '门店']
        assert stores and all(t['name'] and t['unknowns'] and t['reason'] for t in stores)
        items = [draft(t, listing['product']) for t in listing['targets']]
        if self.calls == 2:
            bad = dict(items[0], pitch=items[0]['pitch'].replace(listing['targets'][0]['required_angle'], ''))
            return message(calls=[('check_pitch', bad), ('check_pitch', items[0])])
        if self.calls == 3:
            return message(json.dumps({'decision': '先写一部分', 'confidence': 0.7, 'items': items[:-1]}, ensure_ascii=False))
        return message(json.dumps({'decision': '按渠道场景和网格策略逐店撰写', 'confidence': 0.72, 'items': items}, ensure_ascii=False))


def test_l4_agent_meets_completion_bar(tmp_path):
    data = json.loads(RECORDING.read_text(encoding='utf-8'))
    writer = ScriptedModel()
    inputs = build_inputs(True, llm_post=Replayer(data['model_replies'], data['model']),
                          amap_replay=load_replay_pages(data['amap_pages']), agent_l4=True, llm_post_l4=writer, agent_echo=False)
    directory, state = execute(inputs, root=tmp_path)
    assert state['status'] == 'completed', state.get('violations')
    from pipeline.io import read
    materials = read(directory / 'materials.json')
    assert materials['judged_by'] == 'llm' and all(r['judged_by'] == 'llm' for r in materials['items'])
    assert materials['agent'] == {'model': 'scripted-model', 'model_calls': 4, 'tool_calls': 3}
    assert all(r['compliance']['passed'] for r in materials['items'])
    terminals = {t['poi_id'] for t in read(directory / 'terminals.json')['items']}
    assert all(f"poi:{r['target']}" in r['evidence'] for r in materials['items'] if r['target'] in terminals)
    folder = next(p for p in (directory / 'pitch').iterdir() if (p / 'status.json').is_file() and read(p / 'status.json')['status'] == 'passed')
    assert read(folder / 'status.json')['judged_by'] == 'llm' and 'pitch-writing' in read(folder / 'skills.json')
    steps = [json.loads(l) for l in (directory / 'agent' / 'steps.jsonl').read_text(encoding='utf-8').splitlines()]
    l4 = [s for s in steps if s.get('stage') == 'L4']
    checks = [s for s in l4 if s['kind'] == 'tool' and s['tool'] == 'check_pitch']
    assert [c['result']['passed'] for c in checks] == [False, True]
    assert any('切入策略' in p for p in checks[0]['result']['problems'])
    assert any(s['kind'] == 'rejected' and any('整体' in p for p in s['problems']) for s in l4)

    from pipeline.view import build
    v = build(directory)
    assert v['pitches'] and v['pitch_agent']['model'] == 'scripted-model'
    assert [s['label'] for s in v['pitch_agent']['steps'] if s['kind'] == 'tool'][:1] == ['读取话术目标']
    assert all(s.get('stage', 'L1D') == 'L1D' for s in v['steps'])
    assert '「怎么说」' in v['boundary'][0]
