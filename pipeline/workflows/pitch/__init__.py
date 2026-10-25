"""Merchant context -> composition -> unchanged business gates.

Composition is either the agent loop (live tasks: write, self-check each pitch, submit only when the
full gate passes) or the single-call/rules composer. Both save the same intermediate artifacts."""
from pathlib import Path
from pipeline.io import write, now
from .research import prepare, instructions
from .writing import compose
from .review import inspect, check


def run(c):
    directory = None
    if c.get('run_directory'):
        directory = Path(c['run_directory']) / 'pitch' / now().replace(':', '').replace('.', '')
        directory.mkdir(parents=True, exist_ok=False)

    def persist(name, value):
        if directory:
            write(directory / (name + '.json'), value)

    context = prepare(c)
    persist('context', context)
    persist('skills', instructions())
    try:
        if c.get('agent_l4'):
            from pipeline import l4 as stage, l4_agent
            from pipeline.agent_loop import AgentError
            try:
                artifact = l4_agent.run(c, stage, context)
            except AgentError as exc:
                # Stated fallback, never silent: the writing agent did not meet the bar within its step budget,
                # so this attempt uses the template baseline (judged by the run's own judge) and says so.
                persist('agent_failed', {'error': str(exc)})
                artifact = compose(c, context)
                artifact['open'] = list(artifact.get('open') or []) + ['来源：写作 Agent 未在步数上限内让全部话术达标（' + str(exc)[:80] + '），本版为模板基线，待人工打磨。']
                artifact['decision'] = '写作 Agent 未达标，本版话术为模板基线：' + artifact['decision']
        else:
            artifact = compose(c, context)
        persist('draft', artifact)
        report = inspect(artifact, c)
        persist('review', report)
        persist('status', {'status': 'passed' if report['passed'] else 'failed',
                           'judged_by': artifact.get('judged_by'), 'agent': artifact.get('agent'),
                           'targets': [m['target'] for m in context['merchants']]})
        return artifact
    except Exception as exc:
        persist('status', {'status': 'failed', 'error_type': type(exc).__name__})
        raise
