"""Full-run agent loop (experimental) with a scripted run agent — not evidence of live model quality.

The scripted agent drives the real 新鲜活泉 × 狮山 case through pipeline.runtime: it hits each gate on
purpose (out-of-order stage, early finish, re-running a passed stage), loads stage skills, backtracks once
after 进哪家, and finishes. 找终端 replays the recorded agent; 怎么说 uses the scripted writer.
"""
import json

from pipeline.demo import RECORDING, Replayer, build_inputs
from pipeline.__main__ import load_replay_pages
from pipeline.io import read
from pipeline.runtime import execute_agent
from pipeline.skills import for_stage, listing

from test_l4_agent import ScriptedModel, message


class ScriptedRunAgent:
    model = 'scripted-run-agent'

    def __init__(self):
        self.calls, self.errors, self.backed, self.probed = 0, [], False, False
        self.plan = []

    def tool_results(self, msgs):
        return [json.loads(m['content']) for m in msgs if m['role'] == 'tool']

    def __call__(self, payload):
        self.calls += 1
        msgs = payload['messages']
        assert '铺市执行协议' in msgs[0]['content'] and '技能目录' in msgs[0]['content']
        results = self.tool_results(msgs)
        last = results[-1] if results else None
        self.errors = [r['error'] for r in results if not r.get('ok', True)]
        if self.calls == 1:
            return message(calls=[('status', {})])
        if self.calls == 2:
            # Gates: out-of-order stage and early finish are both refused.
            return message(calls=[('run_stage', {'stage': 'L2'}), ('finish', {})])
        if last and last.get('status') == 'completed':
            return message(json.dumps({'status': 'completed', 'summary': '已完成首访作战包。'}, ensure_ascii=False))
        state = next((r for r in reversed(results) if r.get('ok') and ('next_stage' in r)), None)
        nxt = state['next_stage'] if state else 'L0'
        if nxt is None:
            return message(calls=[('finish', {})])
        if self.done(results).get('L3') and not self.backed:
            self.backed = True
            return message(calls=[('backtrack', {'stage': 'L2', 'reason': '复核网格选择'})])
        if nxt == 'L1' and not self.probed:
            self.probed = True  # re-running a passed stage is refused
            return message(calls=[('run_stage', {'stage': 'L0'})])
        skill = for_stage(nxt)
        calls = ([('load_skill', {'name': skill['name']})] if skill else []) + [('run_stage', {'stage': nxt, 'guidance': f'按 {nxt} 技能执行'})]
        return message(calls=calls)

    @staticmethod
    def done(results):
        out = {}
        for r in results:
            if r.get('stage') and 'passed' in r:
                out[r['stage']] = r['passed']
        return out


def test_skills_cover_every_judgment_stage():
    stages = {s['stage'] for s in listing()}
    assert {'L0', 'L1', 'L1D', 'L2', 'L2C', 'L3', 'L3P', 'L4'} <= stages
    assert for_stage('PACK') is None


def test_full_run_agent_drives_state_machine(tmp_path):
    data = json.loads(RECORDING.read_text(encoding='utf-8'))
    agent = ScriptedRunAgent()
    inputs = build_inputs(True, llm_post=Replayer(data['model_replies'], data['model']), amap_replay=load_replay_pages(data['amap_pages']),
                          agent_l4=True, llm_post_l4=ScriptedModel(), llm_post_runtime=agent, agent_echo=False)
    directory, state = execute_agent(inputs, tmp_path)
    assert state['status'] == 'completed', state.get('violations')
    assert state['completed_stages'] == ['L0', 'L1', 'L1D', 'L2', 'L2C', 'L3', 'L3P', 'L4', 'PACK']
    assert state['agent_backtracks'] == 1 and state['agent']['outcome'] == 'completed'
    assert any('只能运行下一关 L0' in e for e in agent.errors)
    assert any('还有关卡未通过' in e for e in agent.errors)
    assert any('只能运行下一关 L1' in e for e in agent.errors)
    assert (directory / 'terminals.csv').is_file()
    # The stage skill and the agent's guidance reach the stage's model context.
    materials = read(directory / 'materials.json')
    assert materials['agent'] and materials['judged_by'] == 'llm'
    ledger = [json.loads(l) for l in (directory / 'ledger.jsonl').read_text(encoding='utf-8').splitlines()]
    runs = [e for e in ledger if e.get('event') == 'agent_runtime' and e.get('tool') == 'run_stage']
    assert [e['stage'] for e in runs].count('L2') == 2 and all(e['passed'] for e in runs)
    assert any(e.get('tool') == 'backtrack' and e['stage'] == 'L2' for e in ledger if e.get('event') == 'agent_runtime')

    from pipeline.view import build
    v = build(directory)
    ra = v['run_agent']
    assert ra['model'] == 'scripted-run-agent' and ra['backtracks'] == 1
    assert any(s.get('tool') == 'run_stage' and s['stage'] == 'L4' and s['passed'] for s in ra['steps'])
    assert any(s.get('tool') == 'load_skill' and s['skill'] == 'pitch-writing' for s in ra['steps'])
    assert all(s.get('stage', 'L1D') == 'L1D' for s in v['steps'])


def test_live_tasks_use_run_agent_unless_disabled(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pipeline.demo
    import pipeline.runtime
    import server.runs
    used = []

    def fake(name):
        def runner(inputs, root, run_id, observer, **kw):
            used.append((name, inputs.get('agent_runtime')))
            directory = root / 'runs' / run_id; directory.mkdir(parents=True)
            state = dict(run_id=run_id, status='completed', completed_stages=[])
            observer(dict(event='run_done', payload=state))
            return directory, state
        return runner
    monkeypatch.setattr(server.runs, 'execute', fake('engine'))
    monkeypatch.setattr(pipeline.runtime, 'execute_agent', fake('runtime'))
    monkeypatch.setattr(pipeline.demo, 'live_ready', lambda: [])
    for key, value in (('LLM_BASE_URL', 'http://model.invalid'), ('LLM_API_KEY', 'test'), ('LLM_MODEL', 'synthetic-model')):
        monkeypatch.setenv(key, value)
    request = SimpleNamespace(area='shishan', profile='xinxian-huoquan', input='狮山周边3公里推广新鲜活泉', mode='live',
                              parent_id=None, auto_approve=False, demo_replay=None, document=None, library=None)
    manager = server.runs.RunManager(tmp_path)
    try:
        assert manager.runs[manager.start(request)]['done'].wait(15)
        monkeypatch.setenv('PUSHI_AGENT_RUNTIME', '0')
        assert manager.runs[manager.start(request)]['done'].wait(15)
    finally:
        manager.close()
    assert used == [('runtime', True), ('engine', False)]


def test_unfinished_run_is_not_completed(tmp_path):
    """A run agent that stops early leaves the run blocked, never completed."""
    class Quitter:
        model = 'quitter'
        calls = 0
        def __call__(self, payload):
            self.calls += 1
            if self.calls == 1:
                return message(calls=[('status', {})])
            if self.calls == 2:
                return message(json.dumps({'status': 'completed', 'summary': '完成了'}, ensure_ascii=False))
            return message(json.dumps({'status': 'stopped', 'summary': '没有开始执行'}, ensure_ascii=False))
    inputs = build_inputs(False, llm_post_runtime=Quitter(), agent_echo=False)
    directory, state = execute_agent(inputs, tmp_path)
    assert state['status'] == 'blocked' and state['stopped_at'] == 'L0'
    steps = [json.loads(l) for l in (directory / 'agent' / 'steps.jsonl').read_text(encoding='utf-8').splitlines()]
    assert any(s['kind'] == 'rejected' and any('finish' in p for p in s['problems']) for s in steps)
