"""Runtime quick-fix regressions; all model responses are synthetic."""
import json
from unittest.mock import Mock
import pytest
from runtime.agent import Agent, SYSTEM
from runtime.session import Session
from runtime.line import load_line
from pipeline.io import read
from test_pipeline import raw, contexts
from test_agent_runtime import Script, fixtures, script_for


def test_system_reads_only_needed_data():
    assert '读取全部页' not in SYSTEM
    assert '配送点拓客agent' not in SYSTEM
    assert '快消渠道进入 agent' in SYSTEM
    assert '默认不翻页' in SYSTEM


def test_duplicate_returns_cached_summary_without_execution():
    stage = load_line()[0][0].model_copy(update={'max_steps': 2})
    client = Script([('lookup', {'x': 1, 'intent': 'first'}), ('lookup', {'intent': 'again', 'x': 1})])
    events = []
    def emit(kind, **kw):
        events.append((kind, kw))
        return {'id': str(len(events))}
    agent = Agent(client, emit)
    toolbox = Mock()
    toolbox.definitions.return_value = []
    toolbox.call.return_value = {'answer': 42}
    from pipeline.llm.client import LLMError
    with pytest.raises(LLMError, match='agent_max_steps'):
        agent.submit(stage, toolbox)
    assert toolbox.call.call_count == 1
    result = json.loads([m for m in agent.messages if m['role'] == 'tool'][-1]['content'])
    assert result['previous_step'] == 1
    assert result['summary'] == {'answer': 42}
    assert any(kind == 'tool.duplicate' for kind, _ in events)
    restored = Agent(client, emit)
    restored.restore(agent.snapshot())
    assert restored.tool_cache == agent.tool_cache


def test_stage_test_frozen_upstream_runs_only_l1(raw, contexts, tmp_path):
    from runtime.stage_test import stage_test
    stages, catalog = load_line()
    source = Session(raw, tmp_path / 'source', client=Script(script_for(fixtures(contexts, 1), stages[:1], retry=False)), line=(stages[:1], catalog))
    source.run()
    before = (source.directory / 'session.json').read_bytes()
    client = Script(script_for(fixtures(contexts, 2), stages[1:2], retry=False))
    directory, state = stage_test('L1', source.directory, root=tmp_path / 'test', client=client)
    assert state['status'] == 'completed', state
    assert {s for s, _, _ in client.seen} == {'L1'}
    assert read(directory / 'L0.json') == read(source.directory / 'L0.json')
    assert not (directory / 'L2.json').exists()
    assert (source.directory / 'session.json').read_bytes() == before
    with pytest.raises(ValueError, match='upstream_not_frozen:L1'):
        stage_test('L2', source.directory, root=tmp_path / 'missing', client=Script([]))
    assert not (tmp_path / 'missing').exists()
