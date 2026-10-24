"""Full shishan cache replay against fixture responses; no live model request."""
from copy import deepcopy
import json
from pathlib import Path

import httpx

from pipeline.__main__ import prepare_data
from pipeline.engine import execute, STAGES
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
        assert json.loads((directory / (stage + '.json')).read_text(encoding='utf-8'))['judged_by'] == 'llm'
    assert all(r['token_usage']['total_tokens'] == 100 for r in g.records)
    (directory / 'mock-response-provenance.json').write_text(json.dumps({
        'mock': True, 'live_provider_calls': 0,
        'response_source': 'test-only fixtures derived from rules before HTTP replay',
        'calls': g.records,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
