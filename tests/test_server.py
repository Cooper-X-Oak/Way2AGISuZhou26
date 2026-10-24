import json
import socket
import time

import pytest
from fastapi.testclient import TestClient

from pipeline.engine import STAGES
from pipeline.io import read
from server.app import create_app
from server.runs import RunManager

BODY = dict(area='shishan', profile='jiangnan-gongquan',
            input='苏州高新区周边3公里，2个地推，做4周', auto_approve=True)


@pytest.fixture
def api(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('API tests must not access external networks')
    monkeypatch.setattr(socket, 'create_connection', no_network)
    monkeypatch.setattr(socket, 'getaddrinfo', no_network)
    manager = RunManager(tmp_path)
    with TestClient(create_app(manager)) as client:
        yield client, manager


def start(client, **changes):
    response = client.post('/api/runs', json={**BODY, **changes})
    assert response.status_code == 202, response.text
    return response.json()['run_id']


def wait(client, run_id, status=None):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = client.get(f'/api/runs/{run_id}').json()
        if state['status'] == status or (status is None and state['status'] in ('completed', 'blocked', 'failed')):
            return state
        time.sleep(0.02)
    pytest.fail(f'Run timed out: {state}')


def events(response):
    assert response.status_code == 200
    result = []
    for block in response.text.replace('\r\n', '\n').split('\n\n'):
        fields = dict(line.split(': ', 1) for line in block.splitlines() if ': ' in line)
        if 'data' in fields:
            result.append((int(fields['id']), json.loads(fields['data'])))
    return result


def test_catalog_and_cors(api):
    client, _ = api
    assert client.get('/api/health').json()['mode'] == 'rules'
    areas = client.get('/api/areas').json()
    assert {row['id'] for row in areas} == {'shishan', 'guanqian', 'ligongdi'}
    assert all(row['site'] and row['total'] > 0 and isinstance(row['truncated'], bool) for row in areas)
    assert {row['id'] for row in client.get('/api/profiles').json()} == {'jiangnan-gongquan', 'xinxian-huoquan'}
    for origin in ('http://localhost:12345', 'http://127.0.0.1:9000', 'http://[::1]:8787'):
        r = client.options('/api/runs', headers={'Origin': origin, 'Access-Control-Request-Method': 'POST',
                                               'Access-Control-Request-Headers': 'content-type,last-event-id'})
        assert r.headers['access-control-allow-origin'] == origin
    assert 'access-control-allow-origin' not in client.get('/api/health', headers={'Origin': 'https://example.com'}).headers


def test_run_sse_resume_and_artifacts(api):
    client, manager = api
    run_id = start(client)
    # Subscribe while the worker is running; response finishes at its terminal event.
    response = client.get(f'/api/runs/{run_id}/events')
    rows = events(response)
    assert rows[0][1]['type'] == 'stage_start'
    assert rows[-1][1]['type'] == 'run_done'
    assert [r['stage'] for _, r in rows if r['type'] == 'stage_done'] == [name for name, _ in STAGES]
    assert {'attempt', 'check'} <= {r['type'] for _, r in rows}
    state = wait(client, run_id)
    assert state['status'] == 'completed'
    middle = rows[len(rows) // 2][0]
    resumed = events(client.get(f'/api/runs/{run_id}/events', headers={'Last-Event-ID': str(middle)}))
    assert resumed == [row for row in rows if row[0] > middle]
    assert not events(client.get(f'/api/runs/{run_id}/events', headers={'Last-Event-ID': str(rows[-1][0])}))
    assert client.get(f'/api/runs/{run_id}/events', headers={'Last-Event-ID': '-1'}).status_code == 400
    assert client.get(f'/api/runs/{run_id}/artifacts/pack.json').json()['judged_by']
    for name in ('%2E%2E%2Finputs.json', '..%5Cinputs.json', 'ledger.jsonl', 'pending/L2-r1.json'):
        assert client.get(f'/api/runs/{run_id}/artifacts/{name}').status_code == 404
    assert read(manager.directory(run_id) / 'status.json')['judged_by'] == 'rules'


def test_compliance_events(api):
    client, _ = api
    run_id = start(client, demo_replay='compliance')
    rows = events(client.get(f'/api/runs/{run_id}/events'))
    assert any(row['type'] == 'violation' and row['stage'] == 'L4' for _, row in rows)
    assert rows[-1][1]['type'] == 'run_done'


def test_human_gates_and_capacity(api):
    client, manager = api
    run_id = start(client, auto_approve=False)
    other = start(client, auto_approve=False)
    assert client.post('/api/runs', json=BODY).status_code == 429
    assert wait(client, run_id, 'gate_pending')['current_stage'] == 'L2'
    assert client.post(f'/api/runs/{run_id}/gates/L3', json={'decision': 'approve'}).status_code == 409
    for stage in ('L2', 'L3'):
        state = wait(client, run_id, 'gate_pending')
        assert state['current_stage'] == stage
        preview = client.get(f'/api/runs/{run_id}/artifacts/{stage}.pending.json')
        assert preview.status_code == 200 and preview.json()['artifact']
        assert client.post(f'/api/runs/{run_id}/gates/{stage}', json={'decision': 'approve', 'note': '测试人工确认'}).status_code == 200
        assert client.post(f'/api/runs/{run_id}/gates/{stage}', json={'decision': 'approve'}).status_code == 409
        if stage == 'L2':
            deadline = time.monotonic() + 60
            while client.get(f'/api/runs/{run_id}').json()['current_stage'] == 'L2' and time.monotonic() < deadline:
                time.sleep(0.02)
    assert wait(client, run_id)['status'] == 'completed'
    for stage in ('L2', 'L3'):
        assert read(manager.directory(run_id) / f'pending/{stage}-r1.decision.json')['approved_by'] == 'human'
    rows = events(client.get(f'/api/runs/{run_id}/events'))
    assert [row['stage'] for _, row in rows if row['type'] == 'gate_approved'] == ['L2', 'L3']
    wait(client, other, 'gate_pending')
    client.post(f'/api/runs/{other}/gates/L2', json={'decision': 'reject', 'note': '暂不批准'})
    assert wait(client, other)['status'] == 'blocked'
    assert events(client.get(f'/api/runs/{other}/events'))[-1][1]['type'] == 'run_failed'


def test_invalid_requests_and_worker_failure(api, monkeypatch):
    client, manager = api
    assert client.post('/api/runs', json={**BODY, 'area': '../shishan'}).status_code == 422
    assert client.post('/api/runs', json={**BODY, 'profile': '../secret'}).status_code == 422
    assert client.post('/api/runs', json={**BODY, 'demo_replay': 'invalid'}).status_code == 422
    assert client.get('/api/runs/unknown').status_code == 404
    def broken(*args, **kwargs):
        raise OSError('sensitive detail')
    monkeypatch.setattr('server.runs.execute', broken)
    run_id = start(client)
    assert wait(client, run_id)['status'] == 'failed'
    response = client.get(f'/api/runs/{run_id}/events')
    assert events(response)[-1][1]['type'] == 'run_failed'
    assert 'sensitive detail' not in response.text
    assert manager.runs[run_id]['done'].wait(1)
