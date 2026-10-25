"""Merchant context -> composition -> unchanged business gates."""
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
        artifact = compose(c, context)
        persist('draft', artifact)
        report = inspect(artifact, c)
        persist('review', report)
        persist('status', {'status': 'passed' if report['passed'] else 'failed',
                           'judged_by': c['judge'].label.split(':', 1)[0],
                           'targets': [m['target'] for m in context['merchants']]})
        return artifact
    except Exception as exc:
        persist('status', {'status': 'failed', 'error_type': type(exc).__name__})
        raise
