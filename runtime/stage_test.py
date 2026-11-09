"""Isolated workshop execution from accepted, frozen upstream artifacts."""
from copy import deepcopy
from pathlib import Path
import json
import uuid

from pipeline.engine import NAMES
from pipeline.io import read, now
from runtime.agent import Agent, summary
from runtime.events import Events
from runtime.line import load_line
from runtime.session import Session
from pipeline.io import RunStore


def stage_test(stage, from_run, *, root, client, observer=None, token_budget=None):
    source = Path(from_run).resolve()
    data = read(source / 'session.json')
    stages, catalog = load_line()
    index = next(i for i, s in enumerate(stages) if s.name == stage)
    # Only accepted files with passed checks qualify; failed drafts never seed a run.
    frozen = {}
    for upstream in stages[:index]:
        name = upstream.name
        if name not in data['state']['completed_stages']:
            raise ValueError('upstream_not_frozen:' + name)
        artifact, check = read(source / f'{name}.json'), read(source / f'{name}.check.json')
        if not check['passed']:
            raise ValueError('upstream_check_failed:' + name)
        frozen[name] = (artifact, check)
    session = Session.__new__(Session)
    session.client = client
    session.line, session.catalog = stages, catalog
    budget = token_budget if token_budget is not None else {'L1': 30000, 'L2': 40000}.get(stage, stages[index].token_budget)
    session.line[index] = stages[index].model_copy(update={'token_budget': budget})
    client.budget = min(client.budget, budget)
    session.directory = Path(root) / 'runs' / uuid.uuid4().hex
    session.directory.mkdir(parents=True, exist_ok=False)
    session.events = Events(session.directory, observer)
    session.store = RunStore(session.directory)
    session.agent = Agent(client, session.events.emit)
    # Start from immutable inputs, never the source's in-progress context/drafts.
    session.c = {**deepcopy(read(source / 'inputs.json')), 'judge': client,
                 'checks': {}, 'approvals': {}, 'excluded_zones': []}
    session.store.write(session.directory / 'inputs.json', {k: v for k, v in session.c.items() if k != 'judge'})
    for name, (artifact, check) in frozen.items():
        session.c['brief' if name == 'L0' else name.lower()] = artifact
        session.c['checks'][name] = check
        session.agent.memory[name] = summary(artifact)
        for filename, value in ((f'{name}.json', artifact), (f'{NAMES[name]}.json', artifact), (f'{name}.check.json', check)):
            session.store.write(session.directory / filename, value)
    session.state = dict(run_id=session.directory.name, runtime='agent', agent_id=session.agent.id,
                         status='running', started_at=now(), completed_stages=list(frozen), stopped_at=None,
                         index=index, revision=1, attempt=0, backtracks=0,
                         stage_test=stage, from_run=str(source))
    session.toolbox = session.pending = None
    session.agent.checkpoint = session.save
    session.events.emit('run.started', actor='system', detail={'stage_test': stage, 'from_run': str(source)})
    session.save()
    return session.run(only_stage=stage)


def print_step(event):
    if event['kind'] in ('tool.result', 'tool.duplicate', 'llm.usage', 'check.violation', 'stage.completed', 'run.failed'):
        detail = event['detail']
        if event['kind'] == 'tool.result':
            detail = {k: detail.get(k) for k in ('name', 'step', 'arguments', 'result_tokens', 'result_accounting', 'model_tokens', 'error')}
        print(json.dumps({'kind': event['kind'], 'stage': event['stage'], **detail}, ensure_ascii=False), flush=True)
