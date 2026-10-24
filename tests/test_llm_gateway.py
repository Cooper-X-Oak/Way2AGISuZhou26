"""All provider responses are fixtures; these tests make no live LLM claims."""
import hashlib
import json

import httpx
import pytest

from pipeline.llm.client import Gateway, LLMError, serialized


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'https://mock.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret-not-for-ledger')
    monkeypatch.setenv('LLM_MODEL', 'mock-model')


def reply(value, usage=True):
    result = {'choices': [{'message': {'content': json.dumps(value)}}]}
    if usage:
        result['usage'] = dict(prompt_tokens=80, completion_tokens=20, total_tokens=100)
    return httpx.Response(200, json=result)


def gateway(handler, **config):
    return Gateway({'llm': config}, transport=httpx.MockTransport(handler), sleep=lambda seconds: None)


def test_probe_schema_metadata_and_digest(config):
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        assert request.headers['Authorization'] == 'Bearer test-secret-not-for-ledger'
        return reply({'ok': True})
    g = gateway(handler)
    result = g.check()
    assert result['provider'] == 'mock.invalid'
    assert result['supports_json_schema'] is True
    assert result['charged_tokens'] == 100
    assert bodies[0]['response_format']['type'] == 'json_schema'
    assert bodies[0]['max_tokens'] == 16
    assert g.records[0]['prompt_sha256'] == hashlib.sha256(serialized(bodies[0]['messages']).encode()).hexdigest()
    assert 'test-secret' not in serialized(g.records)


def test_format_negotiation_and_local_validation(config):
    bodies = []
    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if len(bodies) == 1:
            return httpx.Response(400, json={'error': {'message': 'json_schema is not supported'}})
        return reply({'ok': True})
    g = gateway(handler)
    assert g.check()['supports_json_schema'] is False
    assert [b['response_format']['type'] for b in bodies] == ['json_schema', 'json_object']
    g.check()
    assert bodies[-1]['response_format']['type'] == 'json_object'
    assert g.records[0]['token_accounting'] == 'conservative_reservation'
    assert g.records[0]['status'] == 'schema_unsupported'


@pytest.mark.parametrize('status', [400, 401, 403])
def test_unrelated_errors_do_not_downgrade(config, status):
    g = gateway(lambda request: httpx.Response(status, json={'error': 'bad credentials'}))
    with pytest.raises(LLMError, match='llm_http_error'):
        g.check()
    assert len(g.records) == 1


def test_validation_error_has_feedback_and_is_not_retried_inside_gateway(config):
    g = gateway(lambda request: reply({}))
    with pytest.raises(LLMError) as error:
        g.check()
    assert not error.value.fatal
    assert error.value.previous == {}
    assert error.value.violations[0]['path'] == 'ok'
    assert len(g.records) == 1


@pytest.mark.parametrize('kind', ['timeout', 'rate_limit', 'server'])
def test_transport_exhaustion_is_bounded_and_logged(config, kind):
    sleeps = []
    def handler(request):
        if kind == 'timeout':
            raise httpx.ReadTimeout('test-secret-not-for-ledger', request=request)
        return httpx.Response(429 if kind == 'rate_limit' else 503,
                              headers={'Retry-After': '2'}, json={'error': 'test-secret-not-for-ledger'})
    g = gateway(handler)
    g.sleep = sleeps.append
    with pytest.raises(LLMError, match='llm_transport_exhausted') as error:
        g.check()
    assert error.value.fatal
    assert len(g.records) == 3 and len(sleeps) == 2
    assert g.total_tokens == sum(r['charged_tokens'] for r in g.records)
    assert 'test-secret' not in serialized(g.records)
    assert g.stopped


def test_transient_failure_then_success(config):
    attempts = []
    def handler(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(429, json={})
        return reply({'ok': True})
    g = gateway(handler)
    assert g.check()['supports_json_schema']
    assert len(attempts) == 2


def test_budget_prevents_request(config):
    g = gateway(lambda request: pytest.fail('budget must stop before request'), token_budget=1)
    with pytest.raises(LLMError, match='llm_budget_exceeded'):
        g.check()
    assert g.records[0]['request_sent'] is False


def test_budget_shared_across_calls_and_missing_usage_is_charged(config):
    g = gateway(lambda request: reply({'ok': True}, usage=False), token_budget=2500)
    g.check()
    first = g.total_tokens
    assert first > 1024
    with pytest.raises(LLMError, match='llm_budget_exceeded'):
        g.check()
    assert g.total_tokens == first
    assert g.records[0]['token_usage'] is None


def test_provider_exceeding_reservation_stops(config):
    def handler(request):
        response = reply({'ok': True})
        body = response.json()
        body['usage'] = dict(prompt_tokens=200_000, completion_tokens=1, total_tokens=200_001)
        return httpx.Response(200, json=body)
    g = gateway(handler)
    with pytest.raises(LLMError, match='llm_budget_exceeded'):
        g.check()
    assert g.records[-1]['run_tokens'] == 200_001


@pytest.mark.parametrize('raw', ['not json', 'NaN', '{"ok":true, "extra":1}'])
def test_invalid_json_or_extra_fields_fail(config, raw):
    g = gateway(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'content': raw}}],
        'usage': dict(prompt_tokens=80, completion_tokens=20, total_tokens=100)}))
    with pytest.raises(LLMError) as error:
        g.check()
    assert error.value.fatal is False


def test_cli_probe(config, monkeypatch, capsys):
    from pipeline.__main__ import main
    monkeypatch.setattr('pipeline.llm.Gateway', lambda params: gateway(lambda request: reply({'ok': True})))
    monkeypatch.setattr('sys.argv', ['pipeline', 'llm-check'])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['supports_json_schema'] is True


def test_run_scope_budget_snapshot_and_reset(config):
    from pipeline.judge import Judge
    from pipeline.llm.lifecycle import prepare_run, attempt_records
    inputs = {'judge': Judge(), 'params': {'llm': {'token_budget': 50_000}}}
    prepared = prepare_run(inputs)
    g = prepared['judge']
    assert isinstance(g, Gateway)
    assert prepared['params']['llm']['token_budget'] == 50_000
    assert 'llm' in prepared['params']['sources']
    assert 'sources' not in inputs['params']
    g.total_tokens = 100
    g.records.append({'status': 'mock'})
    g.stopped = True
    assert attempt_records(g, 0) == [{'status': 'mock'}]
    new_run = prepare_run(prepared)
    assert new_run['judge'].total_tokens == 0
    assert not new_run['judge'].records and not new_run['judge'].stopped
