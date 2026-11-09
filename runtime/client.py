"""OpenAI compatible function calling with a shared, conservative token ceiling."""
import json
import time
import httpx
from pipeline.llm.client import LLMError, serialized


class Client:
    label = 'llm:agent'
    offline = False

    def __init__(self, *, budget=300_000, max_output_tokens=4096, transport=None):
        from pipeline.llm.client import Gateway
        config = Gateway()  # Reuse credential/URL validation; never persist configuration.
        self.base_url, self.key, self.model = config.base_url, config.key, config.model
        self.budget = budget
        self.max_output_tokens = max_output_tokens
        self.transport = transport
        self.total_tokens = 0
        self.records = []
        self.stopped = False
        self.call_token_limit = None

    def complete(self, messages, tools, *, stage, emit, tool_choice=None):
        if self.stopped:
            raise LLMError('llm_run_stopped')
        body = dict(model=self.model, messages=messages, tools=tools, tool_choice='required' if tool_choice is None else tool_choice,
                    max_tokens=self.max_output_tokens)
        from runtime.limits import size
        input_bound=size(dict(messages=messages,tools=tools))+256
        if input_bound>8000: raise LLMError('agent_context_limit')
        reserve = input_bound + self.max_output_tokens
        for attempt in range(1, 4):
            if self.call_token_limit is not None and self.total_tokens + reserve > self.call_token_limit:
                raise LLMError('agent_stage_budget_exceeded')
            if self.total_tokens + reserve > self.budget:
                self.stopped = True
                raise LLMError('llm_budget_exceeded')
            data, response = None, None
            try:
                with httpx.Client(timeout=120, transport=self.transport) as client:
                    response = client.post(self.base_url + '/chat/completions',
                                           headers={'Authorization': 'Bearer ' + self.key}, json=body)
                try:
                    data = response.json()
                except ValueError:
                    pass
            except httpx.RequestError:
                pass
            usage = data.get('usage') if isinstance(data, dict) else None
            valid = (isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0
                     for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                     and usage['total_tokens'] >= usage['prompt_tokens'] + usage['completion_tokens'])
            charged = usage['total_tokens'] if valid else reserve
            self.total_tokens += charged
            record = dict(input_token_bound=input_bound, charged_tokens=charged, total_tokens=self.total_tokens,
                          accounting='provider' if valid else 'conservative_reservation',
                          usage={k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')} if valid else None,
                          transport_attempt=attempt, http_status=response.status_code if response is not None else None)
            self.records.append(record)
            emit('llm.usage', stage=stage, detail=record, metrics={'tokens': charged})
            if self.total_tokens >= self.budget:
                self.stopped = True
                raise LLMError('llm_budget_exceeded')
            if response is None or response.status_code in (408, 429, 500, 502, 503, 504):
                if attempt < 3:
                    time.sleep(0.5 * attempt)
                    continue
                self.stopped = True
                raise LLMError('llm_transport_exhausted')
            if response.is_error:
                self.stopped = True
                error_text = response.text.lower()
                unsupported = response.status_code in (400, 422) and any(
                    word in error_text for word in ('tool', 'function')) and any(
                    word in error_text for word in ('unsupported', 'not support', 'not available', 'unknown parameter'))
                raise LLMError('llm_tools_unsupported' if unsupported else 'llm_http_error')
            try:
                choice = data['choices'][0]
                message = choice['message']
                if choice.get('finish_reason') == 'length':
                    raise LLMError('llm_output_truncated')
                if not message.get('tool_calls'):
                    self.stopped = True
                    raise LLMError('llm_tools_unsupported')
                # Explicitly discard reasoning_content and all provider-private fields.
                return {'role': 'assistant', 'content': message.get('content'),
                        'tool_calls': message['tool_calls']}
            except (KeyError, IndexError, TypeError):
                raise LLMError('llm_invalid_response') from None

    def check(self):
        schema = {'type': 'function', 'function': {'name': 'confirm_tools', 'description': 'Confirm tools support.',
                  'parameters': {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok'], 'additionalProperties': False}}}
        message = self.complete([{'role': 'user', 'content': 'Call confirm_tools with ok=true.'}],
                                [schema], stage='CHECK', emit=lambda *a, **k: None)
        call = message['tool_calls'][0]['function']
        if call['name'] != 'confirm_tools' or json.loads(call['arguments']) != {'ok': True}:
            raise LLMError('llm_tools_unsupported')
        return {'supports_tools': True, 'charged_tokens': self.total_tokens}
