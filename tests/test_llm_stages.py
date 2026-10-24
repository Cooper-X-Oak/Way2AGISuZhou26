"""Exercise real stage checks against mocked HTTP responses, not live models."""
from copy import deepcopy
import json

import httpx
import pytest

from pipeline.engine import STAGES
from pipeline.llm import Gateway, LLMError
from pipeline.llm.schemas import decision_only
from test_pipeline import raw, contexts  # shared synthetic contexts, never saved as source POIs
from test_llm_gateway import config, reply

MODEL_STAGES = STAGES[:-1]


def fixture_decision(module, c):
    """Rules are used ONLY to build labelled mock responses before HTTP testing."""
    class Capture:
        label = 'llm:mock-fixture'
        def decide(self, stage, payload, rules):
            self.value = deepcopy(rules())
            return deepcopy(self.value)
    capture = Capture()
    context = {**c, 'judge': capture}
    module.run(context)
    return capture.value


@pytest.mark.parametrize('stage,module', MODEL_STAGES)
def test_each_stage_http_response_passes_unchanged_checks(config, contexts, stage, module, monkeypatch):
    c = contexts[stage]
    expected = fixture_decision(module, c)
    requests = []
    monkeypatch.setenv('LLM_MODEL_' + stage, 'override-' + stage)
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body['model'] == 'override-' + stage
        assert '# ' + stage in body['messages'][0]['content']
        data = json.loads(body['messages'][1]['content'])
        if stage in ('L3', 'L3P'):
            assert all('lat' not in row and 'name' not in row and 'poi' not in row
                       for row in data['candidates'])
        if stage == 'L4':
            assert 'terminals' not in data and 'competition' not in data
        return reply(expected)
    c['judge'] = Gateway(transport=httpx.MockTransport(handler))
    actual = module.run(c)
    assert module.inspect(actual, c)['passed']
    assert actual['judged_by'] == 'llm'
    assert c['judge'].records[0]['token_usage']['total_tokens'] == 100
    assert len(requests) == 1


@pytest.mark.parametrize('stage,module', MODEL_STAGES)
def test_missing_fields_roundtrip_with_violations(config, contexts, stage, module):
    c = contexts[stage]
    good = fixture_decision(module, c)
    seen = []
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(data)
        if len(seen) == 1:
            return reply({})
        assert data['previous'] == {}
        assert any(v['code'] == 'schema' for v in data['violations'])
        return reply(good)
    c['judge'] = Gateway(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError) as error:
        module.run(c)
    c.update(previous=error.value.previous, violations=error.value.violations)
    assert module.inspect(module.run(c), c)['passed']
    assert len(seen) == 2


@pytest.mark.parametrize('stage,module', MODEL_STAGES)
def test_reference_failures_use_existing_five_layers(config, contexts, stage, module):
    c = contexts[stage]
    good = fixture_decision(module, c)
    bad = deepcopy(good)
    if stage == 'L0':
        bad['profile_id'] = 'nonexistent-profile'
    elif bad['items']:
        bad['items'][0]['evidence'] = ['L9:nonexistent']
    else:
        # The shared synthetic fixture has no partner cache; use one explicit fixture.
        c['market'] = {'partners': [dict(id='test:partner', name='测试酒水批发',
                                       lat=c['brief']['lat'], lon=c['brief']['lon'], tags={})]}
        good = fixture_decision(module, c)
        bad = deepcopy(good)
        bad['items'][0]['evidence'] = ['L9:nonexistent']
    seen = []
    def handler(request):
        data = json.loads(json.loads(request.content)['messages'][1]['content'])
        seen.append(data)
        if len(seen) == 1:
            return reply(bad)
        assert data['violations']
        assert data['previous'] == decision_only(stage, previous)
        return reply(good)
    c['judge'] = Gateway(transport=httpx.MockTransport(handler))
    previous = module.run(c)
    report = module.inspect(previous, c)
    assert not report['passed']
    assert next(row for row in report['layers'] if row['layer'] == 'references')['status'] == 'failed'
    c.update(previous=previous, violations=report['violations'])
    assert module.inspect(module.run(c), c)['passed']
    assert len(seen) == 2
