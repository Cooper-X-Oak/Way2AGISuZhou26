"""Minimal OpenAI-compatible tool-calling loop. No hidden fallback, no framework.

The model chooses which tool to call and with what arguments; tool results go
back into the conversation; the loop ends when the model returns a final JSON
object that passes `validate`, or when a hard limit is hit.

`post(payload) -> dict` is injectable so the loop is testable without network.
"""
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable


class AgentError(RuntimeError):
    """Safe diagnostic: never built from provider bodies, headers or keys."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., dict]

    def schema(self):
        return {'type': 'function', 'function': {'name': self.name, 'description': self.description, 'parameters': self.parameters}}


@dataclass
class Limits:
    max_steps: int = 10
    max_tool_calls: int = 8
    summary_max_chars: int = 4000


@dataclass
class Transcript:
    steps: list = field(default_factory=list)
    tool_calls: int = 0
    model_calls: int = 0


def http_post(timeout=120):
    """Default transport: OpenAI-compatible /chat/completions via httpx, env-configured."""
    import httpx
    base, key, model = (os.getenv(k, '') for k in ('LLM_BASE_URL', 'LLM_API_KEY', 'LLM_MODEL'))
    if not all((base, key, model)):
        raise AgentError('缺少 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL；Agent 模式已停止，未切换为规则。')

    def post(payload):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(base.rstrip('/') + '/chat/completions', headers={'Authorization': f'Bearer {key}'},
                                       json={'model': model, **payload})
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            raise AgentError(f'LLM 调用失败 ({type(exc).__name__})') from None
    post.model = model
    return post


def clip(value, limit):
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return text
    return json.dumps({'truncated': True, 'chars': len(text), 'head': text[:limit - 120]}, ensure_ascii=False)


def parse_final(content):
    text = (content or '').strip()
    fenced = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:
        raise ValueError('最终回答不是 JSON 对象')
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError('最终回答不是 JSON 对象')
    return value


def run_agent(system, user, tools, *, post, validate, limits=Limits(), on_step=lambda step: None):
    """Return (final_dict, transcript). `validate(final)` returns a list of error strings."""
    registry = {t.name: t for t in tools}
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    log = Transcript()

    def record(**step):
        step['step'] = len(log.steps) + 1
        log.steps.append(step)
        on_step(step)

    for _ in range(limits.max_steps):
        started = time.perf_counter()
        response = post({'messages': messages, 'tools': [t.schema() for t in tools], 'tool_choice': 'auto'})
        log.model_calls += 1
        try:
            message = response['choices'][0]['message']
        except (KeyError, IndexError, TypeError):
            raise AgentError('模型响应缺少 choices[0].message') from None
        calls = message.get('tool_calls') or []
        record(kind='model', tool_calls=[c['function']['name'] for c in calls],
               note=(message.get('content') or '')[:300], duration_seconds=round(time.perf_counter() - started, 3))
        messages.append({k: v for k, v in message.items() if k in ('role', 'content', 'tool_calls')} | {'role': 'assistant'})
        if calls:
            for call in calls:
                name = call['function']['name']
                if log.tool_calls >= limits.max_tool_calls:
                    result = {'ok': False, 'error': f'工具调用已达上限 {limits.max_tool_calls}；请基于已有结果给出最终 JSON。'}
                elif name not in registry:
                    result = {'ok': False, 'error': f'未知工具 {name}'}
                else:
                    log.tool_calls += 1
                    try:
                        args = json.loads(call['function'].get('arguments') or '{}')
                        if not isinstance(args, dict):
                            raise ValueError('arguments must be an object')
                    except ValueError:
                        args, result = {}, {'ok': False, 'error': '参数不是合法 JSON 对象'}
                    else:
                        t0 = time.perf_counter()
                        try:
                            result = registry[name].fn(**args)
                        except TypeError:
                            result = {'ok': False, 'error': '参数不符合工具定义'}
                        except AgentError as exc:
                            result = {'ok': False, 'error': str(exc)}
                        record(kind='tool', tool=name, args=args, ok=result.get('ok', True),
                               result=result, duration_seconds=round(time.perf_counter() - t0, 3))
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': clip(result, limits.summary_max_chars)})
            continue
        try:
            final = parse_final(message.get('content'))
            problems = validate(final)
        except ValueError as exc:
            final, problems = None, [str(exc)]
        if not problems:
            return final, log
        record(kind='rejected', problems=problems[:10])
        messages.append({'role': 'user', 'content': '最终 JSON 未通过检查，请修正后重新输出完整 JSON：\n- ' + '\n- '.join(problems[:10])})
    raise AgentError(f'达到步数上限 {limits.max_steps}，未得到合格结果')
