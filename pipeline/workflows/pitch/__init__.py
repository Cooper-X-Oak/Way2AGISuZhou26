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
            artifact = l4_agent.run(c, stage, context)
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
