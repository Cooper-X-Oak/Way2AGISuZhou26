"""Explicitly fictional responses; never read real keys or call a real model."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest
import yaml

from pipeline import profile_builder as builder
from pipeline.llm import LLMError
from pipeline.profiles import load

FIXTURES = Path(__file__).parent / 'fixtures'


def example(kind='tea'):
    name, fact, redline = {
        'tea': ('测试青叶凉茶', '配料为水和茶叶', '神奇饮品'),
        'beer': ('测试麦田精酿', '配料为水、麦芽和啤酒花', '无限畅饮'),
    }[kind]
    scene = '海鲜、火锅、烧烤、酒楼宴席、夜宵、简餐等餐饮佐餐'
    text = (FIXTURES / f'fictional-{kind}.txt').read_text(encoding='utf-8')
    raw = dict(product=dict(name=name, quote=name, facts=[dict(value=fact, quote=fact)]),
               selling_points=[dict(id='ingredients', name='配料', fact=fact, quote=fact, scene_ids=['meal'])],
               scenes=[dict(id='meal', name=scene, quote=scene)],
               compliance_rules=[dict(id='redline', terms=[redline], quote=f'不得使用“{redline}”宣传')],
               competitors=[], channels=[dict(name=n, quote=scene, scene_ids=['meal']) for n in builder.CHANNEL_IDS])
    return dict(id='fictional-' + kind, text=text), raw


class MockGateway:
    mock = True

    def __init__(self, *responses):
        self.responses = list(responses)
        self.contexts = []
        self.records = []

    def decide(self, stage, context):
        assert stage == 'PROFILE'
        self.contexts.append(deepcopy(context))
        self.records.append(dict(event='mock_call', judged_by='llm', mock=True, charged_tokens=0))
        result = self.responses[min(len(self.contexts) - 1, len(self.responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)


@pytest.fixture(autouse=True)
def no_credentials(monkeypatch):
    for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'):
        monkeypatch.delenv(key, raising=False)
    import httpx
    monkeypatch.setattr(httpx.Client, 'post', lambda *a, **k: pytest.fail('Network forbidden'))


@pytest.mark.parametrize('kind', ['tea', 'beer'])
def test_valid_draft(kind, tmp_path):
    payload, raw = example(kind)
    report = builder.save_draft(payload, report_path=tmp_path/'check.json',
                                directory=tmp_path/'drafts', gateway=MockGateway(raw))
    assert report['status'] == 'passed'
    profile = yaml.safe_load(Path(report['profile_path']).read_text(encoding='utf-8'))
    assert profile['draft_metadata']['mock'] is True
    for row in profile['selling_points'] + profile['scenes'] + profile['product']['specification']['facts']:
        assert row['judged_by'] == 'llm'
        assert payload['text'][int(row['source'].split('#')[1]):].startswith(row['quote'])
    loaded = load('drafts/' + payload['id'], tmp_path)
    assert loaded['scenes'][0]['input_source'].startswith('user_text#')
    assert loaded['competitors'] == []
    assert {'国家级', '治疗', '神奇饮品' if kind == 'tea' else '无限畅饮'} <= {
        term for rule in loaded['compliance_rules'] for term in rule['terms']}


def test_quote_retry():
    payload, raw = example()
    invalid = deepcopy(raw)
    invalid['selling_points'][0]['quote'] = '原文没有这句话'
    gateway = MockGateway(invalid, raw)
    result = builder.build(payload, gateway=gateway)
    assert result['status'] == 'passed'
    assert len(result['attempts']) == 2
    assert gateway.contexts[1]['previous'] == invalid
    assert gateway.contexts[1]['violations'][0]['code'] == 'quote'


def test_three_failures_and_immutable_ledgers(tmp_path):
    payload, raw = example()
    raw['channels'][0]['name'] = '药店'
    gateway = MockGateway(raw)
    result = builder.save_draft(payload, report_path=tmp_path/'check.json', directory=tmp_path/'drafts', gateway=gateway)
    assert result['status'] == 'blocked'
    assert len(gateway.contexts) == 3
    assert all(any(v['code'] == 'channel_enum' for v in a['violations']) for a in result['attempts'])
    assert not (tmp_path/'drafts'/f"{payload['id']}.yaml").exists()
    ledger = Path(result['ledger_path'])
    assert len(ledger.read_text(encoding='utf-8').splitlines()) == 3
    again = builder.save_draft(payload, report_path=tmp_path/'check.json', directory=tmp_path/'drafts', gateway=MockGateway(raw))
    assert Path(again['ledger_path']) != ledger and ledger.is_file()


@pytest.mark.parametrize('mutation', ['fact', 'scene', 'competitor', 'rule', 'duplicate', 'reference', 'schema', 'name'])
def test_invalid_evidence(mutation):
    payload, raw = example()
    if mutation == 'fact': raw['product']['facts'][0]['value'] = '治愈疾病'
    if mutation == 'scene': raw['scenes'][0]['name'] = '商务会议'
    if mutation == 'competitor': raw['competitors'] = [dict(brand='虚构对手', category='凉茶', quote='配料为水和茶叶')]
    if mutation == 'rule': raw['compliance_rules'][0]['terms'] = ['原文没有']
    if mutation == 'duplicate': raw['scenes'] *= 2
    if mutation == 'reference': raw['channels'][0]['scene_ids'] = ['missing']
    if mutation == 'schema': raw['product']['untrusted_extra_fact'] = '额外事实'
    if mutation == 'name': payload['product_name'] = '未出现的名称'
    assert builder.build(payload, gateway=MockGateway(raw))['status'] == 'blocked'


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_whitespace_original_offsets(newline, tmp_path):
    payload, raw = example()
    payload['text'] = payload['text'].replace('配料为水和茶叶', '配料为水' + newline + '  和茶叶')
    raw['product']['facts'][0].update(value='配料为水 和茶叶', quote='配料为水 和茶叶')
    raw['selling_points'][0].update(fact='配料为水 和茶叶', quote='配料为水 和茶叶')
    result = builder.save_draft(payload, gateway=MockGateway(raw), directory=tmp_path/'drafts', report_path=tmp_path/'check.json')
    assert result['status'] == 'passed'
    assert result['profile']['selling_points'][0]['fact'] == '配料为水' + newline + '  和茶叶'
    loaded = load('drafts/' + payload['id'], tmp_path)
    assert loaded['selling_points'][0]['fact'] == '配料为水\n  和茶叶'
    assert loaded['selling_points'][0]['quote'] == '配料为水' + newline + '  和茶叶'


def test_gateway_errors():
    payload, raw = example()
    gateway = MockGateway(LLMError('llm_invalid_json', fatal=False), raw)
    assert builder.build(payload, gateway=gateway)['status'] == 'passed'
    fatal = MockGateway(LLMError('llm_budget_exceeded'))
    result = builder.build(payload, gateway=fatal)
    assert len(result['attempts']) == 1 and result['status'] == 'blocked'


def test_explicit_non_water_competitor(tmp_path):
    payload, raw = example()
    payload['text'] += '\n测试虚构竞品：测试对手，品类凉茶。'
    raw['competitors'] = [dict(brand='测试对手', category='凉茶', quote='测试对手，品类凉茶')]
    result = builder.save_draft(payload, directory=tmp_path/'drafts', report_path=tmp_path/'check.json', gateway=MockGateway(raw))
    assert result['status'] == 'passed'
    profile = load('drafts/' + payload['id'], tmp_path)
    assert profile['competitors'][0]['category'] == '凉茶'
    assert next(r for r in profile['compliance_rules'] if r['id'] == 'competitor-naming')['terms'] == ['测试对手']


def test_public_gateway_limitation_before_keys(monkeypatch):
    monkeypatch.setattr(builder, 'Gateway', lambda *a, **k: pytest.fail('Must not read credentials'))
    payload, _ = example()
    result = builder.build(payload)
    assert result['attempts'][0]['violations'][0]['code'] == 'gateway_contract_unavailable'


@pytest.mark.parametrize('profile_id', ['../escape', 'drafts/../escape', '/absolute', 'drafts/nested/name', 'drafts\\name'])
def test_path_traversal(profile_id, tmp_path):
    with pytest.raises(ValueError): load(profile_id, tmp_path)
    with pytest.raises(ValueError): builder.build(dict(id=profile_id, text='test'), gateway=MockGateway({}))


def test_existing_draft_and_tampered_source(tmp_path):
    payload, raw = example()
    args = dict(report_path=tmp_path/'check.json', directory=tmp_path/'drafts')
    first = builder.save_draft(payload, gateway=MockGateway(raw), **args)
    before = Path(first['profile_path']).read_bytes()
    assert builder.save_draft(payload, gateway=MockGateway(raw), **args)['status'] == 'blocked'
    assert Path(first['profile_path']).read_bytes() == before
    Path(first['profile_path']).with_suffix('.txt').write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='摘要'): load('drafts/' + payload['id'], tmp_path)


def test_cli_draft(monkeypatch, tmp_path):
    import pipeline.__main__ as cli
    payload, raw = example()
    original = builder.save_draft
    monkeypatch.setattr(builder, 'save_draft', lambda p, **kw: original(p, directory=tmp_path/'drafts', gateway=MockGateway(raw), **kw))
    monkeypatch.setattr(sys, 'argv', ['pipeline', 'profile-draft', '--text-file', str(FIXTURES/'fictional-tea.txt'),
                                    '--id', payload['id'], '--name', raw['product']['name'], '-o', str(tmp_path/'check.json')])
    assert cli.main() == 0
    assert json.loads((tmp_path/'check.json').read_text(encoding='utf-8'))['status'] == 'passed'


@pytest.mark.parametrize('kind', ['tea', 'beer'])
def test_offline_shishan_cli(kind, monkeypatch, tmp_path):
    """Only relocate file roots; all production stage checks run unmodified."""
    import pipeline.__main__ as cli
    from pipeline import l0
    from pipeline.engine import execute
    payload, raw = example(kind)
    builder.save_draft(payload, report_path=tmp_path/'check.json', directory=tmp_path/'drafts', gateway=MockGateway(raw))
    monkeypatch.setattr(l0, 'PROFILE_DIR', tmp_path/'drafts')
    monkeypatch.setattr(cli, 'load', lambda pid: load(pid, tmp_path))
    monkeypatch.setattr(cli, 'execute', lambda inputs: execute(inputs, root=tmp_path))
    output = tmp_path/'offline.json'
    monkeypatch.setattr(sys, 'argv', ['pipeline', 'run', '--offline', '--area', 'shishan', '--profile', 'drafts/' + payload['id'],
        '--input', '半径3公里，2个地推，做4周', '--auto-approve', '-o', str(output)])
    assert cli.main() == 0, output.read_text(encoding='utf-8')
    state = json.loads(output.read_text(encoding='utf-8'))
    assert state['status'] == 'completed' and state['judged_by'] == 'rules'
    run = Path(state['run_directory'])
    assert state['completed_stages'] == ['L0', 'L1', 'L2', 'L2C', 'L3', 'L3P', 'L4', 'PACK']
    for stage in state['completed_stages']:
        assert json.loads((run/f'{stage}.check.json').read_text(encoding='utf-8'))['passed']
    l1 = json.loads((run/'L1.json').read_text(encoding='utf-8'))
    assert {r['channel_id'] for r in l1['items']} == set(builder.CHANNEL_IDS.values())
