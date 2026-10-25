"""Failure-boundary regressions; all model replies below are test doubles."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pipeline.io import read, write
from server.public import create_public_app
from test_chat_library import BODY, Manager


@pytest.mark.parametrize('execute', [False, True])
def test_tool_state_is_recoverable_before_final_model_reply(tmp_path, monkeypatch, execute):
    import server.chat
    manager = Manager(tmp_path)
    calls = 0
    saved_id = None

    def completion(*args, **kwargs):
        nonlocal calls, saved_id
        calls += 1
        if calls == 1:
            name = 'execute_market_task' if execute else 'update_document'
            arguments = {'brief': '周边3公里推广面包', 'plan': '先核对候选'}
            if execute:
                arguments['request'] = arguments['brief']
            return {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'test-operation', 'type': 'function', 'function': {
                    'name': name, 'arguments': json.dumps(arguments)}}]}
        # Simulate losing the final model response after a tool has succeeded.
        files = list((tmp_path / 'conversations').glob('*.json'))
        assert len(files) == 1, 'Tool outcome must exist before the final model call'
        state = read(files[0]); saved_id = state['id']
        assert state['document']['plan'] == '先核对候选'
        assert state['messages'][-2] == {'role': 'user', 'content': BODY['input']}
        assert state['messages'][-1]['role'] == 'assistant'
        assert state['latest_run'] == ('a' * 32 if execute else None)
        owners = read(tmp_path / 'session-owners.json')
        assert saved_id in owners
        if execute:
            assert owners[saved_id] == owners['a' * 32]
            assert state['messages'][-1]['run_id'] == 'a' * 32
        raise RuntimeError('Injected interruption during final wording')

    monkeypatch.setattr(server.chat, 'completion', completion)
    with TestClient(create_public_app(tmp_path, manager), base_url='https://demo.test') as client:
        # Establish the anonymous cookie before the interrupted request.
        assert client.get('/api/tasks/config').status_code == 200
        with pytest.raises(RuntimeError, match='Injected interruption'):
            client.post('/api/chat', json=BODY)
        # A new app instance can recover the saved state under the same owner.
        cookie = dict(client.cookies)
    with TestClient(create_public_app(tmp_path, manager), base_url='https://demo.test') as restarted:
        restarted.cookies.update(cookie)
        state = restarted.get('/api/chat/' + saved_id)
        assert state.status_code == 200
        assert state.json()['document']['brief'] == '周边3公里推广面包'
    assert len(manager.started) == int(execute)


def test_json_replace_failure_preserves_old_file_and_removes_own_temp(tmp_path, monkeypatch):
    path = tmp_path / 'status.json'
    write(path, {'status': 'running'})
    original = path.read_bytes()
    def reject_replace(source, target):
        assert path.read_bytes() == original
        assert json.loads(source.read_text(encoding='utf8')) == {'status': 'completed'}
        raise OSError('Injected replace failure')
    monkeypatch.setattr(Path, 'replace', reject_replace)
    with pytest.raises(OSError, match='Injected replace failure'):
        write(path, {'status': 'completed'})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_json_serialization_failure_does_not_touch_old_file(tmp_path):
    path = tmp_path / 'status.json'
    write(path, {'status': 'running'})
    with pytest.raises(ValueError):
        write(path, {'score': float('nan')})
    assert read(path) == {'status': 'running'}
    assert list(tmp_path.iterdir()) == [path]


def test_demo_channel_pitches_preserve_facts_and_pass_existing_gates():
    from pipeline import l4
    from pipeline.judge import Judge
    from pipeline.profiles import load, parameters
    profile = load('xinxian-huoquan')
    context = {'profile': profile, 'params': parameters(), 'judge': Judge(True), 'pois': [],
               'l1': {'items': [{**ch, 'channel_id': ch['id'], 'priority': 'P1',
                                 'selling_point_ids': [p['id'] for p in profile['selling_points']
                                                       if set(p['scene_ids']) & set(ch['scene_ids'])]}
                                for ch in profile['channels']]},
               'l2c': {'items': []}, 'l3': {'items': []}}
    artifact = l4.run(context)
    assert not l4.check(artifact, context)
    items = {row['target']: row for row in artifact['items']}
    for channel, audience in [('tea_channel', '茶席'), ('office_channel', '行政'), ('gym_channel', '会员')]:
        row = items[channel]
        assert audience in row['pitch']
        assert not any(term in row['pitch'] for term in ('点单', '试陈列', '家庭常备'))
        assert any('沟通假设' in item for item in row['open'])
        assert row['judged_by'] == 'rules'
        points = {p['id']: p['fact'] for p in profile['selling_points']}
        assert all(points[key] in row['pitch'] for key in row['selling_point_ids'])


def test_custom_product_does_not_inherit_water_channel_pitches():
    from pipeline.profiles import load
    from server.library import apply_selection
    inputs = {'profile': load('xinxian-huoquan'), 'site': {}, 'coverage': {'site': {}}}
    apply_selection(inputs, {'product': {'name': '测试面包', 'facts': '测试用产品资料', 'source': 'fixture'}})
    assert not inputs['profile']['material'].get('channel_pitch_templates')
    assert not inputs['profile']['material'].get('channel_pitch_source')
