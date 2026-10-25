"""Live activity stream for the workbench: built from the run ledger and agent steps (scripted models, recorded case)."""
import json
import shutil

from pipeline.demo import RECORDING, Replayer, build_inputs
from pipeline.engine import execute
from pipeline.__main__ import load_replay_pages
from pipeline.io import read, write
from pipeline.view import build

from test_l4_agent import ScriptedModel


def run_case(tmp_path):
    data = json.loads(RECORDING.read_text(encoding='utf-8'))
    inputs = build_inputs(True, llm_post=Replayer(data['model_replies'], data['model']), amap_replay=load_replay_pages(data['amap_pages']),
                          agent_l4=True, llm_post_l4=ScriptedModel(), agent_echo=False)
    # The service passes an observer, which is what writes stage_start/stage_done events to the ledger.
    directory, state = execute(inputs, root=tmp_path, observer=lambda entry: None)
    assert state['status'] == 'completed'
    return directory


def test_activity_follows_the_run_in_time_order(tmp_path):
    v = build(run_case(tmp_path))
    act = v['activity']
    starts = [r['stage'] for r in act if r['kind'] == 'stage']
    assert starts == ['L0', 'L1', 'L1D', 'L2', 'L2C', 'L3', 'L3P', 'L4', 'PACK']
    assert [r['t'] for r in act] == sorted(r['t'] for r in act)
    assert any(r['kind'] == 'tool' and r['text'].startswith('高德补采') for r in act)
    inventory = next(r for r in act if r['kind'] == 'tool' and r['text'].startswith('盘点现有商户'))
    assert '_channel' not in inventory['text'], 'channel names, not ids'
    assert any(r['kind'] == 'merge' for r in act)
    pitch_checks = [r for r in act if r['kind'] == 'tool' and r['text'].startswith('自查话术')]
    assert pitch_checks and '未通过' in pitch_checks[0]['text'] and '通过' in pitch_checks[-1]['text']
    assert not pitch_checks[0]['text'].split('：')[1].startswith('amap:'), 'store names, not ids'
    assert any(r['kind'] == 'rejected' and r['stage'] == 'L4' for r in act)
    assert all(r['ok'] for r in act if r['kind'] == 'check')
    assert v['live'] is None


def test_live_step_while_running(tmp_path):
    directory = run_case(tmp_path)
    running = tmp_path / 'running'
    shutil.copytree(directory, running)
    ledger = (running / 'ledger.jsonl').read_text(encoding='utf-8').splitlines()
    cut = next(i for i, line in enumerate(ledger) if json.loads(line).get('event') == 'stage_start' and json.loads(line)['stage'] == 'L2C')
    (running / 'ledger.jsonl').write_text('\n'.join(ledger[:cut + 1]) + '\n', encoding='utf-8')
    status = read(running / 'status.json')
    write(running / 'status.json', {**status, 'status': 'running', 'judged_by': 'llm'})
    steps = [l for l in (running / 'agent' / 'steps.jsonl').read_text(encoding='utf-8').splitlines() if json.loads(l)['stage'] == 'L1D']
    (running / 'agent' / 'steps.jsonl').write_text('\n'.join(steps) + '\n', encoding='utf-8')
    v = build(running)
    assert v['live']['stage'] == 'L2C' and v['live']['text'] == '「对手」等待模型判断'
    assert v['live']['since'] == v['activity'][-1]['t']
