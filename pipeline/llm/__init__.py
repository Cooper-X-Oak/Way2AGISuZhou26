"""Stage call-point adapter; offline rules are always explicitly selected."""
from pipeline.llm.client import Gateway, LLMError


def decide(context, stage, payload, rules, contract):
    from pipeline.judge import Judge
    judge = context['judge']
    if not isinstance(judge, (Judge, Gateway)) or judge.offline:
        from pipeline.common import decide as offline_decide
        return offline_decide(context, stage, payload, rules, contract)
    if not isinstance(judge, Gateway):
        judge = context['judge'] = Gateway(context['params'])
    from pipeline.llm.prompts import build_context
    return judge.decide(stage, build_context(context, stage, payload))


__all__ = ['Gateway', 'LLMError', 'decide']
