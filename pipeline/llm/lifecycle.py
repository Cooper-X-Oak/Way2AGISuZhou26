"""Small engine hooks: run-scoped budget and serializable audit entries.

Call prepare_run before persisting inputs. For each attempt, remember
len(judge.records) and append subsequent records to the existing run ledger.
LLMError.violations/previous feed the existing bounded stage retry loop;
LLMError.fatal stops after the current failed attempt has been persisted.
"""
from copy import deepcopy

from pipeline.judge import Judge
from pipeline.llm.client import Gateway


def prepare_run(inputs):
    judge = inputs['judge']
    if isinstance(judge, Judge) and not judge.offline:
        judge = Gateway(inputs['params'])
    if not isinstance(judge, Gateway):
        return inputs
    judge.total_tokens = 0
    judge.records = []
    judge.stopped = False
    params = deepcopy(inputs['params'])
    params['llm'] = deepcopy(judge.config)
    params.setdefault('sources', {})['llm'] = (
        '来源：pipeline.llm.client.DEFAULTS（token_budget=200000）及本次 params.llm 显式覆盖；'
        '项目调用预算假设，非提供商额度；缺失 usage 按 UTF-8 字节、协议预留与输出上限保守扣账。'
    )
    return {**inputs, 'params': params, 'judge': judge}


def attempt_records(judge, cursor):
    return deepcopy(judge.records[cursor:]) if isinstance(judge, Gateway) else []
