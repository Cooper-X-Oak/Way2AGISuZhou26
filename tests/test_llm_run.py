"""Full shishan cache replay against fixture responses; no live model request."""
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from pipeline.__main__ import prepare_data
from pipeline.engine import execute, STAGES
from pipeline.io import read
from pipeline.llm import Gateway
from pipeline.market_capture import load_market
from pipeline.profiles import load, parameters
from test_llm_gateway import config, reply
from test_llm_stages import fixture_decision


def shishan_inputs():
    params = parameters()
    pois, coverage = prepare_data(params, 'shishan')
    profile = load('jiangnan-gongquan')
    return dict(request='想把江南贡泉铺进苏州高新区周边3公里的餐馆，2个地推，做4周',
                site=coverage['site'], area_id='shishan', pois=pois, coverage=coverage,
                params=params, profile=profile, market=load_market('shishan'),
                auto_approve=True, no_defaults=False, demo_replay=None)


def mock_responses(inputs):
    """Construct fixtures explicitly before the tested HTTP run, never as fallback."""
    class FixtureJudge:
        label = 'llm:mock-fixture'
        def decide(self, stage, payload, rules):
            return rules()
    c = deepcopy(inputs)
    c.update(judge=FixtureJudge(), checks={}, approvals={'L2': 'auto (demo)', 'L3': 'auto (demo)'},
             excluded_zones=[], source_documents={inputs['profile']['source']:
                 Path(inputs['profile']['source']).read_text(encoding='utf-8')})
    values = {}
    for stage, module in STAGES:
        if stage != 'PACK':
            values[stage] = fixture_decision(module, c)
        artifact = module.run(c)
        report = module.inspect(artifact, c)
        assert report['passed'], report
        c['brief' if stage == 'L0' else stage.lower()] = artifact
        c['checks'][stage] = report
    return values


def test_shishan_full_mock(config, tmp_path):
    inputs = shishan_inputs()
    responses = mock_responses(inputs)
    requests = []
    def handler(request):
        body = json.loads(request.content)
        stage = json.loads(body['messages'][1]['content'])['stage']
        requests.append(stage)
        return reply(responses[stage])
    g = Gateway(transport=httpx.MockTransport(handler))
    inputs['judge'] = g
    directory, state = execute(inputs, root=tmp_path)
    assert state['status'] == 'completed', state
    assert state['judged_by'] == 'llm'
    assert requests == [stage for stage, _ in STAGES[:-1]]
    for stage in requests:
        assert read(directory / (stage + '.json'))['judged_by'] == 'llm'
    assert all(r['token_usage']['total_tokens'] == 100 for r in g.records)
    ledger = [json.loads(line) for line in (directory / 'ledger.jsonl').read_text(encoding='utf-8').splitlines()]
    calls = [call for entry in ledger for call in entry.get('llm_calls', [])]
    assert calls == g.records
    assert [call['stage'] for call in calls] == requests
    assert all(call['model'] == 'mock-model' and len(call['prompt_sha256']) == 64 for call in calls)
    assert calls[-1]['run_tokens'] == 700
    assert read(directory / 'params.json')['llm'] == g.config
    assert read(directory / 'inputs.json')['params']['llm'] == g.config
    (directory / 'mock-response-provenance.json').write_text(json.dumps({
        'mock': True, 'live_provider_calls': 0,
        'response_source': 'test-only fixtures derived from rules before HTTP replay',
        'calls': g.records,
    }, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('bad', [{}, [], None])
def test_engine_schema_feedback(config, tmp_path, bad):
    inputs = shishan_inputs()
    good = fixture_decision(STAGES[0][1], inputs)
    seen = []
    def handler(request):
        payload = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(payload)
        if len(seen) == 1:
            return reply(bad)
        assert payload['previous'] == bad
        assert payload['violations'] and all(v['code'] == 'schema' for v in payload['violations'])
        return reply(good)
    inputs['judge'] = Gateway(transport=httpx.MockTransport(handler))
    observed = []
    directory, state = execute(inputs, root=tmp_path, stages=STAGES[:1], observer=observed.append)
    assert state['status'] == 'completed'
    entries = [e for e in observed if 'llm_calls' in e]
    assert [e['status'] for e in entries] == ['failed', 'passed']
    assert [e['llm_calls'][0]['status'] for e in entries] == ['schema_invalid', 'completed']
    assert read(directory / entries[0]['artifact_path'])['artifact'] == bad
    assert read(directory / entries[0]['check_path'])['violations'] == seen[1]['violations']


@pytest.mark.parametrize('failure', ['budget', 'network', 'http'])
def test_engine_fatal_stops_and_records(config, tmp_path, failure):
    inputs = shishan_inputs()
    seen = []
    def handler(request):
        seen.append(request)
        if failure == 'network':
            raise httpx.ConnectError('test-secret-not-for-ledger', request=request)
        return httpx.Response(401, json={'error': 'test-secret-not-for-ledger'})
    params = {'llm': {'token_budget': 1}} if failure == 'budget' else {}
    inputs['judge'] = Gateway(params, transport=httpx.MockTransport(handler), sleep=lambda _: None)
    observed = []
    directory, state = execute(inputs, root=tmp_path, observer=observed.append)
    assert state['status'] == 'blocked' and state['stopped_at'] == 'L0'
    entries = [e for e in observed if 'llm_calls' in e]
    assert len(entries) == 1 and entries[0]['attempt'] == 1
    assert entries[0]['judged_by'] == 'llm'
    expected = {'budget': 'llm_budget_exceeded', 'network': 'llm_transport_exhausted', 'http': 'llm_http_error'}[failure]
    assert state['violations'][0]['code'] == expected
    assert len(seen) == {'budget': 0, 'network': 3, 'http': 1}[failure]
    assert observed[-1]['event'] == 'run_failed'
    assert 'test-secret-not-for-ledger' not in (directory / 'ledger.jsonl').read_text(encoding='utf-8')
    assert not (directory / 'L1.json').exists()
