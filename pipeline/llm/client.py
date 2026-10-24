"""Bounded OpenAI-compatible JSON transport with auditable budget accounting.

Budget is shared by every stage/revision in one run. Without provider usage,
UTF-8 bytes plus framing and the output cap are charged conservatively; those
values are explicitly estimates, never presented as measured provider tokens.
"""
import hashlib
import json
import os
import time
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from pipeline.llm.schemas import SCHEMAS, Probe

DEFAULTS = {'token_budget': 200_000, 'max_output_tokens': 16_384,
            'transport_attempts': 3, 'timeout_seconds': 90, 'backoff_seconds': 0.5}


class LLMError(RuntimeError):
    def __init__(self, code, *, fatal=True, violations=None, previous=None):
        super().__init__(code)
        self.code = code
        self.fatal = fatal
        self.violations = violations or [dict(code=code, path='llm', message=code)]
        self.previous = previous


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Gateway:
    offline = False

    def __init__(self, params=None, *, transport=None, sleep=time.sleep):
        self.config = {**DEFAULTS, **(params or {}).get('llm', {})}
        for key in ('token_budget', 'max_output_tokens', 'transport_attempts'):
            if type(self.config[key]) is not int or self.config[key] <= 0:
                raise ValueError('Invalid llm parameter: ' + key)
        if self.config['transport_attempts'] > 3:
            raise ValueError('At most three transport attempts')
        for key in ('timeout_seconds', 'backoff_seconds'):
            value = self.config[key]
            if type(value) not in (int, float) or not 0 <= value <= 300:
                raise ValueError('Invalid llm parameter: ' + key)
        self.base_url = os.getenv('LLM_BASE_URL', '').rstrip('/')
        self.key = os.getenv('LLM_API_KEY', '')
        self.model = os.getenv('LLM_MODEL', '')
        if not all((self.base_url, self.key, self.model)):
            raise LLMError('llm_configuration_missing')
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise LLMError('llm_base_url_invalid')
        self.provider = parsed.hostname
        self.label = 'llm:' + self.model
        self.models = {stage: os.getenv('LLM_MODEL_' + stage) or self.model for stage in SCHEMAS}
        self.transport = transport
        self.sleep = sleep
        self.total_tokens = 0
        self.records = []
        self.schema_support = {}
        self.stopped = False

    def _failure(self, code, stage, model, prompt_hash):
        self.stopped = True
        self.records.append(dict(event='llm_call', stage=stage, judged_by='llm', model=model,
                                 prompt_sha256=prompt_hash, status=code, request_sent=False,
                                 charged_tokens=0, run_tokens=self.total_tokens))
        raise LLMError(code)

    @staticmethod
    def _unsupported(response):
        if response.status_code not in (400, 422):
            return False
        # Inspect only to negotiate the format; never save the provider error body.
        message = response.text.lower()
        return (('json_schema' in message or 'response_format' in message)
                and any(term in message for term in ('not support', 'unsupported', 'not available', 'unknown parameter')))

    def decide(self, stage, context):
        from pipeline.llm.prompts import instruction
        schema = SCHEMAS[stage]
        messages = [{'role': 'system', 'content': instruction(stage)},
                    {'role': 'user', 'content': serialized({'stage': stage, **context,
                                                          'output_contract': schema.model_json_schema()})}]
        return self._complete(stage, schema, messages)

    def _complete(self, stage, schema, messages, *, output_limit=None):
        model = self.models.get(stage, self.model)
        prompt_hash = hashlib.sha256(serialized(messages).encode('utf-8')).hexdigest()
        if self.stopped:
            self._failure('llm_run_stopped', stage, model, prompt_hash)
        use_schema = self.schema_support.get(model, True)
        retries = 0
        while True:
            response_format = ({'type': 'json_schema', 'json_schema': {
                'name': stage.lower() + '_decision', 'strict': True, 'schema': schema.model_json_schema()}}
                if use_schema else {'type': 'json_object'})
            output_cap = min(self.config['max_output_tokens'], output_limit or self.config['max_output_tokens'])
            body = dict(model=model, messages=messages, response_format=response_format,
                        max_tokens=output_cap)
            # Deliberately conservative across unknown provider tokenizers. Includes
            # the schema and framing; no request is sent without an output reserve.
            input_bound = len(serialized(body).encode('utf-8')) + 1024
            reserve = input_bound + output_cap
            if self.total_tokens + reserve > self.config['token_budget']:
                self._failure('llm_budget_exceeded', stage, model, prompt_hash)
            record = dict(event='llm_call', stage=stage, judged_by='llm', model=model,
                          provider=self.provider, prompt_sha256=prompt_hash,
                          response_format=response_format['type'], request_sent=True,
                          transport_attempt=retries + 1, reserved_tokens=reserve)
            started = time.perf_counter()
            response = None
            response_data = None
            network_error = False
            try:
                with httpx.Client(timeout=self.config['timeout_seconds'], transport=self.transport) as client:
                    response = client.post(self.base_url + '/chat/completions',
                                           headers={'Authorization': 'Bearer ' + self.key}, json=body)
                try:
                    response_data = response.json()
                except ValueError:
                    pass
            except httpx.RequestError:
                network_error = True
            finally:
                record['duration_seconds'] = round(time.perf_counter() - started, 6)
            usage = response_data.get('usage') if isinstance(response_data, dict) else None
            valid_usage = (isinstance(usage, dict)
                           and all(type(usage.get(k)) is int and usage[k] >= 0
                                   for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                           and usage['total_tokens'] >= usage['prompt_tokens'] + usage['completion_tokens'])
            charged = usage['total_tokens'] if valid_usage else reserve
            self.total_tokens += charged
            record.update(token_usage={k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
                          if valid_usage else None,
                          token_accounting='provider' if valid_usage else 'conservative_reservation',
                          charged_tokens=charged, run_tokens=self.total_tokens,
                          http_status=response.status_code if response is not None else None)
            self.records.append(record)
            if self.total_tokens > self.config['token_budget']:
                record['status'] = 'llm_budget_exceeded'
                self.stopped = True
                raise LLMError('llm_budget_exceeded')
            if response is not None and use_schema and self._unsupported(response):
                record['status'] = 'schema_unsupported'
                self.schema_support[model] = use_schema = False
                continue
            transient = network_error or response.status_code in (408, 429, 500, 502, 503, 504)
            if transient:
                record['status'] = 'network_error' if network_error else 'transient_http_error'
                retries += 1
                if retries >= self.config['transport_attempts']:
                    self.stopped = True
                    raise LLMError('llm_transport_exhausted')
                delay = min(30, self.config['backoff_seconds'] * 2 ** (retries - 1))
                if response is not None:
                    try:
                        delay = max(delay, min(30, max(0, float(response.headers.get('retry-after', '0')))))
                    except ValueError:
                        pass
                record['retry_delay_seconds'] = delay
                self.sleep(delay)
                continue
            if response.is_error:
                record['status'] = 'http_error'
                self.stopped = True
                raise LLMError('llm_http_error')
            if use_schema:
                self.schema_support[model] = True
            try:
                raw = response_data['choices'][0]['message']['content']
                result = json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            except (KeyError, IndexError, TypeError, ValueError):
                record['status'] = 'invalid_json'
                raise LLMError('llm_invalid_json', fatal=False) from None
            try:
                validated = schema.model_validate(result).model_dump()
            except ValidationError as exc:
                violations = [dict(code='schema', path='.'.join(map(str, e['loc'])), message=e['msg'])
                              for e in exc.errors(include_input=False, include_url=False)]
                record['status'] = 'schema_invalid'
                raise LLMError('llm_schema_invalid', fatal=False, violations=violations, previous=result) from None
            record['status'] = 'completed'
            return validated

    def check(self):
        started = time.perf_counter()
        result = self._complete('CHECK', Probe, [
            {'role': 'system', 'content': 'Return only a JSON object with ok=true.'},
            {'role': 'user', 'content': '{"ok": true}'}], output_limit=16)
        if result['ok'] is not True:
            raise LLMError('llm_probe_failed')
        return dict(provider=self.provider, model=self.model,
                    latency_seconds=round(time.perf_counter() - started, 6),
                    supports_json_schema=self.schema_support.get(self.model, False),
                    charged_tokens=self.total_tokens,
                    token_accounting=self.records[-1]['token_accounting'])
