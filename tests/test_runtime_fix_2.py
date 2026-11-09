"""Fast submission regressions; synthetic model transport only."""
import json
from unittest.mock import Mock
import httpx
import pytest
from runtime.agent import Agent
from runtime.client import Client
from runtime.line import load_line
from pipeline.llm.client import LLMError


def test_l1_only_submit():
    stage = load_line()[0][1]
    assert stage.tools_allowed == ['submit_artifact']


def test_force_submit_request_from_n_minus_two_and_after_feedback(monkeypatch):
    for key, value in {'LLM_BASE_URL': 'https://example.test/v1', 'LLM_API_KEY': 'fake', 'LLM_MODEL': 'fake'}.items():
        monkeypatch.setenv(key, value)
    requests = []
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'tool_calls': [
            {'id': str(len(requests)), 'type': 'function', 'function': {'name': 'submit_artifact', 'arguments': '{"payload":{}}'}}]}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}})
    client = Client(transport=httpx.MockTransport(respond))
    agent = Agent(client, lambda *a, **k: {'id': 'e'})
    stage = load_line()[0][1].model_copy(update={'max_steps': 5})
    toolbox = Mock()
    toolbox.definitions.return_value = []
    toolbox.call.return_value = {}
    agent.steps = 1
    agent.submit(stage, toolbox)
    agent.feedback([{'code': 'test', 'decision_path': 'items'}])
    agent.tool_cache = {}
    agent.submit(stage, toolbox)
    assert requests[0]['tool_choice'] == 'required'
    assert requests[1]['tool_choice'] == {'type': 'function', 'function': {'name': 'submit_artifact'}}


def test_intent_is_not_part_of_duplicate_identity():
    class Fake:
        total_tokens = 0
        def complete(self, *args, **kwargs):
            self.total_tokens += 1
            return {'role': 'assistant', 'tool_calls': [{'id': str(self.total_tokens), 'function': {
                'name': 'lookup', 'arguments': json.dumps({'x': 1, 'intent': str(self.total_tokens)})}}]}
    agent = Agent(Fake(), lambda *a, **k: {'id': 'e'})
    toolbox = Mock()
    toolbox.definitions.return_value = []
    toolbox.call.return_value = {'answer': 42}
    with pytest.raises(LLMError, match='agent_max_steps'):
        agent.submit(load_line()[0][1].model_copy(update={'max_steps': 2}), toolbox)
    assert toolbox.call.call_count == 1
