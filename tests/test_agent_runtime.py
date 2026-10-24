"""Scripted tool calls only; synthetic fixtures never enter integration runs."""
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from pipeline.engine import STAGES
from pipeline.io import read
from pipeline.llm.client import LLMError
from runtime.agent import Agent
from runtime.client import Client
from runtime.events import Events
from runtime.line import load_line
from runtime.session import Session
from test_pipeline import raw, contexts
from test_llm_gateway import config


class Script:
    label = 'llm:scripted-fixture'
    budget = 200000
    max_output_tokens = 8192
    total_tokens = 0

    def __init__(self, replies):
        self.replies = iter(replies)
        self.seen = []

    def complete(self, messages, tools, *, stage, emit):
        self.seen.append((stage, deepcopy(messages), {t['function']['name'] for t in tools}))
        self.total_tokens += 100
        name, arguments = next(self.replies)
        return dict(role='assistant', content='读取证据或按检查清单修订后提交。',
                    reasoning_content='THIS MUST NEVER BE SAVED',
                    tool_calls=[dict(id=f'call-{len(self.seen)}', type='function',
                                     function=dict(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))])


def fixtures(contexts, count=3):
    result = {}
    for name, module in STAGES[:count]:
        a = module.run(contexts[name])
        a['judged_by'] = 'llm'
        for row in a.get('items', []):
            row['judged_by'] = 'llm'
        result[name] = a
    return result


def script_for(artifacts, stages, *, retry=True):
    result = []
    for stage in stages:
        result.extend([('load_skill', {'name': stage.skills_allowed[0]}), ('get_brief', {})])
        if retry:
            result.append(('submit_artifact', {'payload': {}}))
        result.append(('submit_artifact', {'payload': artifacts[stage.name]}))
    return result


def rows(session):
    return [json.loads(s) for s in session.events.path.read_text(encoding='utf-8').splitlines()]


def test_one_agent_three_workshops_retry_compression_and_gates(raw, contexts, tmp_path):
    stages, catalog = load_line()
    stages = stages[:3]
    client = Script(script_for(fixtures(contexts), stages))
    session = Session(raw, tmp_path, client=client, line=(stages, catalog))
    identity = session.agent.id
    directory, state = session.run()
    assert state['status'] == 'completed', state
    assert state['agent_id'] == identity
    assert state['completed_stages'] == ['L0', 'L1', 'L2']
    events = rows(session)
    for stage in stages:
        selected = [e for e in events if e['stage'] == stage.name]
        assert any(e['kind'] == 'check.violation' and e['detail']['code'] == 'schema' for e in selected)
        assert sum(e['kind'] == 'agent.output' for e in selected) == 2
        assert any(e['kind'] == 'agent.read' and e['detail'].get('skill') == stage.skills_allowed[0] for e in selected)
    for stage, messages, tools in client.seen:
        assert tools == set(next(s for s in stages if s.name == stage).tools_allowed)
        assert sum(m['role'] == 'system' for m in messages) == 1
    assert client.seen[0][2] != client.seen[4][2] != client.seen[8][2]
    compacted = [e['detail'] for e in events if e['kind'] == 'context.compacted']
    assert len(compacted) == 3
    assert all(e['after_tokens'] < e['before_tokens'] for e in compacted)
    assert 'THIS MUST NEVER BE SAVED' not in json.dumps(read(directory / 'session.json'))
    assert [e['seq'] for e in events] == list(range(1, len(events) + 1))
    assert all({'status', 'parent_id', 'refs', 'metrics'} <= e.keys() for e in events)
    ids = {e['id'] for e in events if e['kind'] == 'tool.call'}
    assert all(e['parent_id'] in ids for e in events if e['kind'] == 'tool.result')


def test_full_line_mock_never_calls_old_stage_runners(raw, tmp_path, monkeypatch):
    # Construct labelled mock responses before forbidding every old runner.
    class FixtureJudge:
        label = 'llm:mock-fixture'
        def decide(self, stage, payload, rules): return rules()
    c = deepcopy(raw)
    c.update(judge=FixtureJudge(), checks={}, approvals={'L2': 'auto (demo)', 'L3': 'auto (demo)'},
             source_documents={raw['profile']['source']: Path(raw['profile']['source']).read_text(encoding='utf-8')})
    artifacts = {}
    for name, module in STAGES:
        artifact = module.run(c)
        report = module.inspect(artifact, c)
        assert report['passed'], report
        artifacts[name] = artifact
        c['brief' if name == 'L0' else name.lower()] = artifact
        c['checks'][name] = report
    def forbidden(*args, **kwargs): raise AssertionError('old runner called during agent execution')
    for _, module in STAGES:
        monkeypatch.setattr(module, 'run', forbidden)
    stages, catalog = load_line()
    client = Script(script_for(artifacts, stages))
    session = Session(raw, tmp_path, client=client)
    _, state = session.run()
    assert state['status'] == 'completed', state
    assert state['completed_stages'] == [s.name for s in stages]
    assert read(session.directory / 'pack.json')['items'] == artifacts['L3']['items']
    assert sum(e['kind'] == 'artifact.ready' for e in rows(session)) == 8


def test_gate_disk_restore_and_same_agent_backtrack(raw, contexts, tmp_path):
    stages, catalog = load_line()
    raw['auto_approve'] = False
    client = Script(script_for(fixtures(contexts), stages[:3], retry=False))
    session = Session(raw, tmp_path, client=client, line=(stages, catalog))
    _, state = session.run()
    assert state['status'] == 'gate_pending'
    agent_id = session.agent.id
    restored = Session.restore(session.directory, client=Script([]), line=(stages, catalog))
    assert restored.agent.id == agent_id
    assert restored.agent.messages == session.agent.messages
    restored.resolve_gate(True, 'test approval')
    assert restored.state['completed_stages'] == ['L0', 'L1', 'L2']
    restored.backtrack('L2', 'test upstream revision')
    assert restored.agent.id == agent_id
    assert restored.state['completed_stages'] == ['L0', 'L1']
    assert 'l2' not in restored.c and 'L2' not in restored.agent.memory
    assert 'L2' not in restored.c['approvals']
    assert not (restored.directory / 'L2.json').exists()
    restored.client = Script(script_for(fixtures(contexts), stages[2:3], retry=False))
    restored.agent.client = restored.client
    _, state = restored.run()
    assert state['status'] == 'gate_pending'
    assert restored.agent.id == agent_id
    restored.resolve_gate(False, 'test rejection')
    assert restored.state['error'] == 'gate_rejected'
    assert any(e['kind'] == 'flow.backtrack' for e in rows(restored))
    assert (restored.directory / 'attempts/L2-r1-1.json').exists()
    assert (restored.directory / 'attempts/L2-r2-1.json').exists()


@pytest.mark.parametrize('failure', ['steps', 'budget', 'tool_error', 'attempts'])
def test_bounded_failures(raw, tmp_path, failure):
    stages, catalog = load_line()
    stage = stages[0].model_copy(deep=True)
    stage.max_steps = 2 if failure != 'attempts' else 16
    if failure == 'budget': stage.token_budget = 50
    replies = [('get_brief', {})] * 3
    if failure == 'tool_error': replies = [('poi_detail', {'poi_id': 'not-allowed'})] * 3
    if failure == 'attempts': replies = [('submit_artifact', {'payload': {}})] * 4
    session = Session(raw, tmp_path, client=Script(replies), line=([stage], catalog))
    _, state = session.run()
    assert state['error'] == ('agent_stage_budget_exceeded' if failure == 'budget' else 'agent_max_attempts' if failure == 'attempts' else 'agent_max_steps')
    if failure == 'tool_error':
        assert any(e['kind'] == 'tool.result' and e['status'] == 'warn' for e in rows(session))


def response(message=None, status=200, usage=True):
    return httpx.Response(status, json={'choices': [{'finish_reason': 'tool_calls', 'message': message or {'role': 'assistant', 'content': 'no tools'}}],
                                       **({'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}} if usage else {})})


def test_tools_probe_and_usage(config, tmp_path):
    def handle(request):
        body = json.loads(request.content)
        assert body['tools'][0]['function']['name'] == 'confirm_tools'
        return response({'role': 'assistant', 'content': 'Confirm.', 'reasoning_content': 'private', 'tool_calls': [
            {'id': 'one', 'type': 'function', 'function': {'name': 'confirm_tools', 'arguments': '{"ok":true}'}}]})
    client = Client(transport=httpx.MockTransport(handle))
    assert client.check() == {'supports_tools': True, 'charged_tokens': 30}
    assert client.total_tokens == 30


@pytest.mark.parametrize('status', [200, 400])
def test_no_tools_stops_without_fallback(config, status):
    client = Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, text='tools unsupported') if status == 400 else response()))
    with pytest.raises(LLMError, match='llm_tools_unsupported'):
        client.check()


def test_budget_before_network_and_missing_usage(config):
    calls = []
    def handle(request):
        calls.append(request)
        return response(usage=False)
    client = Client(budget=1, transport=httpx.MockTransport(handle))
    with pytest.raises(LLMError, match='llm_budget_exceeded'): client.check()
    assert not calls
    client = Client(transport=httpx.MockTransport(handle))
    with pytest.raises(LLMError): client.check()
    assert client.records[0]['accounting'] == 'conservative_reservation'
    assert client.total_tokens > client.max_output_tokens


def test_line_reference_validation_and_skill_boundary(tmp_path, raw):
    import yaml
    stages, catalog = load_line()
    data = yaml.safe_load(Path('production/line.yaml').read_text(encoding='utf-8'))
    data['stages'][0]['skills_allowed'].append('missing-skill')
    path = tmp_path / 'line.yaml'
    path.write_text(yaml.safe_dump(data), encoding='utf-8')
    with pytest.raises(ValueError, match='unknown_skill'): load_line(path)
    session = Session(raw, tmp_path, client=Script([]))
    toolbox = session.make_tools(stages[0])
    with pytest.raises(ValueError, match='skill_not_allowed'):
        toolbox.call('load_skill', {'name': 'zone-analysis'})
    with pytest.raises(ValueError, match='artifact_not_upstream'):
        toolbox.call('get_artifact', {'stage': 'L2'})


def test_large_list_pagination_and_explicit_assembly(raw, tmp_path):
    session = Session(raw, tmp_path, client=Script([]))
    toolbox = session.make_tools(session.line[0])
    # A labelled synthetic tool value, not source data or an LLM output.
    toolbox.values['fixture'] = [{'id': str(i), 'count': i} for i in range(150)]
    page = toolbox.call('read_result', {'ref': 'fixture', 'offset': 12, 'limit': 100})
    assert len(page['data']['rows']) == 100 and page['data']['total'] == 150
    with pytest.raises(ValueError):
        toolbox.call('read_result', {'ref': 'fixture', 'limit': 1000})
    output = toolbox.call('submit_artifact', {'payload': {'items': {
        '$each': {'$ref': 'fixture'}, 'set': {'judged_by': 'llm'},
        'key': 'id', 'overrides': {'149': {'note': 'model authored'}}}}})
    assert len(output['items']) == 150 and output['items'][-1]['note'] == 'model authored'
    assert output['items'][0]['count'] == 0
    assert 'judged_by' not in toolbox.values['fixture'][0]


def test_transport_failure_retries_share_budget(config, monkeypatch):
    monkeypatch.setattr('runtime.client.time.sleep', lambda _: None)
    seen = []
    def transport(request):
        seen.append(request)
        raise httpx.ConnectError('do not leak provider secrets')
    client = Client(transport=httpx.MockTransport(transport))
    with pytest.raises(LLMError, match='llm_transport_exhausted'):
        client.check()
    assert len(seen) == len(client.records) == 3
    assert client.total_tokens == sum(r['charged_tokens'] for r in client.records)
    assert 'secrets' not in json.dumps(client.records)


def test_partner_tool_uses_agent_eligibility_not_business_classifier(contexts, monkeypatch):
    from runtime.tools import Toolbox
    c = contexts['L3P']
    c['market'] = {'partners': [dict(id='fixture-partner', name='synthetic', lat=c['brief']['lat'], lon=c['brief']['lon'])]}
    toolbox = Toolbox(c, 'L3P', {}, ['partner_coverage'])
    def forbidden(*args): raise AssertionError('tool classified business type')
    monkeypatch.setattr('pipeline.l3p.kind', forbidden)
    geometry = toolbox.call('partner_coverage', {})['data']['items'][0]
    assert 'recommendation' not in geometry
    excluded = toolbox.call('partner_coverage', {'eligible_partner_ids': []})['data']['items'][0]
    assert excluded['recommendation'] == '暂缓'
    eligible = toolbox.call('partner_coverage', {'eligible_partner_ids': ['fixture-partner']})['data']['items'][0]
    assert eligible['recommendation'] == '优先'


def test_agent_sse_uses_envelope_and_seq(tmp_path):
    import asyncio
    import threading
    from server.events import stream_events
    events = Events(tmp_path)
    events.emit('run.started')
    events.emit('tool.call', stage='L0')
    events.emit('run.completed')
    done = threading.Event()
    done.set()
    class Manager:
        runs = {'test': {'runtime': 'agent', 'done': done}}
        def directory(self, run_id): return tmp_path
    class Request:
        async def is_disconnected(self): return False
    async def collect():
        return [row async for row in stream_events(Manager(), 'test', 1, Request())]
    result = asyncio.run(collect())
    assert [r['id'] for r in result] == ['2', '3']
    assert json.loads(result[0]['data'])['kind'] == 'tool.call'


def test_service_agent_gate_and_sse(raw, contexts, tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from server.app import create_app
    from server.runs import RunManager
    stages, catalog = load_line()
    artifacts = fixtures(contexts)
    def runner(inputs, root, **kwargs):
        return Session(inputs, root, client=Script(script_for(artifacts, stages[:3], retry=False)),
                       line=(stages[:3], catalog), **{k: v for k, v in kwargs.items() if k != 'approve'}).run(approve=kwargs['approve'])
    monkeypatch.setattr('runtime.session.execute', runner)
    monkeypatch.setattr('server.runs.prepare_data', lambda *a: (raw['pois'], raw['coverage']))
    monkeypatch.setattr('server.runs.load_market', lambda *a: {})
    manager = RunManager(tmp_path)
    with TestClient(create_app(manager)) as api:
        body = dict(area='guanqian', profile=raw['profile']['id'], input=raw['request'], runtime='agent')
        response = api.post('/api/runs', json=body)
        assert response.status_code == 202
        run_id = response.json()['run_id']
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = api.get(f'/api/runs/{run_id}').json()
            if state['status'] == 'gate_pending': break
            time.sleep(.01)
        assert state['status'] == 'gate_pending', state
        assert api.get(f'/api/runs/{run_id}/artifacts/L2.pending.json').status_code == 200
        assert api.post(f'/api/runs/{run_id}/gates/L2', json={'decision': 'approve'}).status_code == 200
        assert manager.runs[run_id]['done'].wait(20)
        assert api.get(f'/api/runs/{run_id}').json()['status'] == 'completed'
        text = api.get(f'/api/runs/{run_id}/events').text
        assert 'event: agent.plan' in text and 'event: gate.resolved' in text
        assert 'event: run.completed' in text


def test_service_agent_initial_failure_emits_event(raw, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from server.runs import RunManager
    def broken(*args, **kwargs): raise RuntimeError('secret must not leak')
    monkeypatch.setattr('runtime.session.execute', broken)
    monkeypatch.setattr('server.runs.prepare_data', lambda *a: (raw['pois'], raw['coverage']))
    monkeypatch.setattr('server.runs.load_market', lambda *a: {})
    manager = RunManager(tmp_path)
    try:
        run_id = manager.start(SimpleNamespace(area='guanqian', profile=raw['profile']['id'], input=raw['request'],
                               runtime='agent', auto_approve=False, demo_replay=None))
        assert manager.runs[run_id]['done'].wait(10)
        text = (manager.directory(run_id) / 'events.jsonl').read_text(encoding='utf-8')
        assert 'run.failed' in text and 'secret must not leak' not in text
    finally:
        manager.close()
