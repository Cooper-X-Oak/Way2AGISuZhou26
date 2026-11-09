"""Production harness with durable gates and one agent across all revisions."""
from copy import deepcopy
import json
from pathlib import Path
import uuid

from pipeline.engine import NAMES
from pipeline.io import RunStore, read, write, now
from pipeline.llm.client import LLMError
from pipeline import l3
from pipeline.quality import registry
from runtime.agent import Agent, summary
from runtime.decisions import SCHEMAS
from runtime.assembly import map_violations
from runtime.client import Client
from runtime.events import Events
from runtime.line import load_line, CONTRACTS, CHECKS
from runtime.tools import Toolbox


class Session:
    def __init__(self, inputs, root=Path('.'), *, client=None, run_id=None,
                 observer=None, source_root=Path('.'), line=None):
        self.client = client if client is not None else Client()
        self.line, self.catalog = load_line() if line is None else line
        self.directory = Path(root) / 'runs' / (run_id or uuid.uuid4().hex)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.events = Events(self.directory, observer)
        self.store = RunStore(self.directory)
        self.agent = Agent(self.client, self.events.emit)
        public = deepcopy({k: v for k, v in inputs.items() if k != 'judge'})
        public['source_documents'] = {
            row['source'].split('#')[0]: (Path(source_root) / row['source'].split('#')[0]).read_text(encoding='utf-8')
            for row in public['profile']['scenes'] + public['profile']['selling_points']}
        self.c = {**public, 'judge': self.client, 'checks': {}, 'approvals': {}, 'excluded_zones': []}
        for name, value in [('inputs', public), *[(k, public[k]) for k in ('params', 'pois', 'coverage', 'profile')],
                            ('market', public.get('market', {}))]:
            self.store.write(self.directory / (name + '.json'), value)
        self.state = dict(run_id=self.directory.name, runtime='agent', agent_id=self.agent.id,
                          status='running', started_at=now(), completed_stages=[], stopped_at=None,
                          index=0, revision=1, attempt=0, backtracks=0)
        self.toolbox = None
        self.pending = None
        self.agent.checkpoint=self.save
        self.events.emit('run.started', actor='system', detail={'agent_id': self.agent.id})
        self.save()

    def save(self):
        self.state['total_tokens'] = self.client.total_tokens
        write(self.directory / 'status.json', self.state)
        snapshot = dict(state=self.state, context={k: v for k, v in self.c.items() if k != 'judge'},
                        agent=self.agent.snapshot(), pending=self.pending,
                        values=self.toolbox.values if self.toolbox else None,
                        source_map=self.toolbox.source_map if self.toolbox else {},
                        tools_used=sorted(self.toolbox.used) if self.toolbox else [],
                        loaded_skills=sorted(self.toolbox.loaded_skills) if self.toolbox else [],
                        last_decision=self.toolbox.last_decision if self.toolbox else None,
                        budget=self.client.budget, max_output_tokens=self.client.max_output_tokens,
                        total_tokens=self.client.total_tokens)
        # Atomic checkpoint; never contains URL, key, model or a client instance.
        target = self.directory / 'session.json'
        temporary = self.directory / 'session.next.json'
        self.store.write(temporary, snapshot)
        temporary.replace(target)

    @classmethod
    def restore(cls, directory, *, client=None, observer=None, line=None):
        self = cls.__new__(cls)
        self.directory = Path(directory)
        data = read(self.directory / 'session.json')
        self.client = client if client is not None else Client(budget=data['budget'], max_output_tokens=data['max_output_tokens'])
        self.client.total_tokens = max(self.client.total_tokens, data['total_tokens'])
        self.client.budget = min(self.client.budget, data['budget'])
        self.line, self.catalog = load_line() if line is None else line
        self.events = Events(self.directory, observer)
        self.store = RunStore(self.directory)
        self.agent = Agent(self.client, self.events.emit)
        self.agent.restore(data['agent'])
        self.agent.checkpoint=self.save
        self.c = {**data['context'], 'judge': self.client}
        self.state, self.pending = data['state'], data['pending']
        self.toolbox = None
        if data['values'] is not None and self.state['index'] < len(self.line):
            self.toolbox = self.make_tools(self.line[self.state['index']])
            self.toolbox.values = data['values']
            self.toolbox.source_map = data.get('source_map',{})
            self.toolbox.used=set(data.get('tools_used',[]))
            self.toolbox.loaded_skills=set(data.get('loaded_skills',[]))
            self.toolbox.last_decision = data.get('last_decision')
        return self

    def make_tools(self, stage):
        return Toolbox(self.c, stage.name, {k: self.catalog[k] for k in stage.skills_allowed}, stage.tools_allowed)

    def ledger(self, entry):
        with (self.directory / 'ledger.jsonl').open('a', encoding='utf-8', newline='\n') as f:
            f.write(json.dumps({'timestamp': now(), **entry}, ensure_ascii=False) + '\n')

    def inspect(self, stage, artifact):
        report = CHECKS[stage.checks](artifact, self.c)
        if self.toolbox and self.toolbox.submission_errors:
            errors=self.toolbox.submission_errors
            report['violations']=errors
            report['passed']=False
            report['layers'][0].update(status='failed',violations=errors)
        group = self.events.emit('check.started', stage=stage.name, detail={'attempt': self.state['attempt']})
        for layer in report['layers']:
            self.events.emit('check.layer', stage=stage.name, parent_id=group['id'], detail=layer,
                             status='ok' if layer['status'] == 'passed' else 'warn')
        for violation in report['violations']:
            self.events.emit('check.violation', stage=stage.name, parent_id=group['id'], detail=violation, status='blocked')
        stem = f"{stage.name}-r{self.state['revision']}-{self.state['attempt']}"
        # Gate rechecks are separate immutable siblings of the original failed inspection.
        if (self.directory / f'attempts/{stem}.json').exists():
            stem += '-gate'
        self.store.write(self.directory / f'attempts/{stem}.json', {'artifact': artifact, 'check': report, 'decision': self.toolbox.last_decision if self.toolbox else None, 'source_map': self.toolbox.source_map if self.toolbox else {}})
        self.store.write(self.directory / f'checks/{stem}.json', report)
        self.store.write(self.directory / f'{stage.name}.check.json', report)
        self.ledger(dict(stage=stage.name, revision=self.state['revision'], attempt=self.state['attempt'],
                         status='passed' if report['passed'] else 'failed', violations=report['violations'],
                         artifact_path=f'attempts/{stem}.json', check_path=f'checks/{stem}.json'))
        return report

    def accept(self, stage, artifact, report):
        self.c['brief' if stage.name == 'L0' else stage.name.lower()] = artifact
        self.c['checks'][stage.name] = report
        self.store.write(self.directory / f'{stage.name}.json', artifact)
        self.store.write(self.directory / f'{NAMES[stage.name]}.json', artifact)
        self.state['completed_stages'].append(stage.name)
        self.events.emit('artifact.ready', stage=stage.name, refs=['artifact:' + stage.name], detail=summary(artifact))
        self.events.emit('stage.completed', stage=stage.name, actor='system',
                         detail={'completed_stages': self.state['completed_stages'][:]})
        self.agent.compress(stage.name, artifact, self.c.get('brief'))
        self.state.update(index=self.state['index'] + 1, attempt=0)
        self.toolbox = None
        self.pending = None
        self.save()

    def fail(self, code, violations=None):
        name = self.line[self.state['index']].name if self.state['index'] < len(self.line) else None
        self.state.update(status='blocked', stopped_at=name, error=code, violations=violations or [], finished_at=now())
        self.ledger(dict(event='run_failed', stage=name, code=code, violations=violations or []))
        self.events.emit('run.failed', actor='system', stage=name, status='blocked', detail=deepcopy(self.state))
        self.save()
        return self.directory, self.state

    def backtrack(self, target, reason, excluded_zones=()):
        index = next(i for i, stage in enumerate(self.line) if stage.name == target)
        if index >= self.state['index']:
            raise ValueError('backtrack_must_be_upstream')
        if self.state['backtracks'] >= min(2, self.c['params']['max_backtracks']):
            raise LLMError('backtrack_limit')
        origin = self.line[self.state['index']].name
        invalid = [s.name for s in self.line[index:]]
        for name in invalid:
            self.c.pop('brief' if name == 'L0' else name.lower(), None)
            self.c['checks'].pop(name, None)
            self.c['approvals'].pop(name, None)
            self.agent.memory.pop(name, None)
            self.c.get('decision_drafts',{}).pop(name,None)
            self.c.get('assembly_provenance',{}).pop(name,None)
            if name=='L3': self.c.pop('terminal_reviews',None)
            # Revision artifacts stay in attempts; remove stale current pointers.
            for filename in (f'{name}.json', f'{NAMES[name]}.json', f'{name}.check.json'):
                (self.directory / filename).unlink(missing_ok=True)
        self.c['excluded_zones'] = sorted(set(self.c['excluded_zones']) | set(excluded_zones))
        self.state['completed_stages'] = [n for n in self.state['completed_stages'] if n not in invalid]
        self.state.update(index=index, attempt=0, revision=self.state['revision'] + 1,
                          backtracks=self.state['backtracks'] + 1, status='running', backtrack_reason=reason)
        self.agent.messages.append({'role': 'user', 'content': json.dumps({'backtrack': target, 'reason': reason, 'invalidated': invalid}, ensure_ascii=False)})
        self.events.emit('flow.backtrack', stage=origin, actor='system', detail={'from': origin, 'to': target, 'reason': reason, 'invalidated': invalid})
        self.ledger(dict(event='backtrack', origin=origin, target=target, reason=reason))
        self.toolbox = None
        self.pending = None
        self.save()

    def resolve_gate(self, approved, note='', *, approved_by='human'):
        if self.state['status'] != 'gate_pending' or self.pending is None:
            raise ValueError('no_pending_gate')
        stage = self.line[self.state['index']]
        self.events.emit('gate.resolved', stage=stage.name, actor='human' if approved_by == 'human' else 'system',
                         detail={'approved': approved, 'approved_by': approved_by, 'note': note})
        self.ledger(dict(event='gate_resolved', stage=stage.name, approved=approved, approved_by=approved_by, note=note))
        if not approved:
            return self.fail('gate_rejected')
        self.c['approvals'][stage.name] = approved_by
        artifact = self.pending['artifact']
        report = self.inspect(stage, artifact)
        if not report['passed']:
            return self.fail('gate_recheck_failed', report['violations'])
        self.state['status'] = 'running'
        self.agent.messages.append({'role': 'user', 'content': f'{stage.name} 关口批准来源：{approved_by}；{note}'})
        self.accept(stage, artifact, report)
        return self.directory, self.state

    def run(self, approve=None, *, only_stage=None):
        only_stage = only_stage or self.state.get('stage_test')
        if self.state['status'] != 'running':
            return self.directory, self.state
        try:
            while self.state['index'] < len(self.line):
                stage = self.line[self.state['index']]
                if only_stage is not None and stage.name != only_stage:
                    break
                if self.toolbox is None:
                    self.toolbox = self.make_tools(stage)
                    upstream = {'artifacts': list(self.agent.memory),
                                'profile_evidence': 'profile:' + self.c['profile']['id'],
                                'instruction': '只引用本会话已展示的编号。'}
                    self.agent.enter(stage, SCHEMAS[stage.name], self.catalog, upstream,
                                     self.state.pop('backtrack_reason', None), toolbox=self.toolbox)
                    self.events.emit('stage.started', stage=stage.name, actor='system', detail={'agent_id': self.agent.id, 'tools': stage.tools_allowed})
                if self.state['attempt'] >= stage.max_attempts:
                    return self.fail('agent_max_attempts')
                self.state['attempt'] += 1
                artifact = self.agent.submit(stage, self.toolbox)
                report = self.inspect(stage, artifact)
                if report['passed']:
                    self.accept(stage, artifact, report)
                    continue
                codes = {v['code'] for v in report['violations']}
                if codes == {'approval_pending'}:
                    self.pending = {'artifact': artifact, 'check': report}
                    preview = self.directory / f"pending/{stage.name}-r{self.state['revision']}.json"
                    self.store.write(preview, self.pending)
                    self.state['status'] = 'gate_pending'
                    self.save()  # Durable messages and artifact BEFORE notifying manager.
                    self.events.emit('gate.pending', stage=stage.name, actor='human', status='waiting',
                                     detail={'preview': preview.relative_to(self.directory).as_posix()})
                    if self.c.get('auto_approve'):
                        self.resolve_gate(True, '显式自动确认演示', approved_by='auto (demo)')
                    elif approve is not None:
                        self.resolve_gate(bool(approve(stage.name, preview)))
                    else:
                        return self.directory, self.state
                    if self.state['status'] != 'running':
                        return self.directory, self.state
                    continue
                if stage.name == 'L3' and 'zone_shortage' in codes and codes <= {'zone_shortage', 'approval_pending'}:
                    if only_stage is not None:
                        return self.fail('stage_test_backtrack_required', report['violations'])
                    self.backtrack('L2', 'A/B终端不足，保持阈值并补选下一商圈', l3.shortage(artifact, self.c))
                    continue
                if self.state['attempt'] >= stage.max_attempts:
                    return self.fail('agent_max_attempts', report['violations'])
                hints = None
                if 'evidence' in codes:
                    allowed = registry(self.c, stage.name)
                    hints = {'invalid_evidence': sorted({ref for row in [artifact, *artifact.get('items', [])]
                              for ref in row.get('evidence', []) if ref not in allowed}),
                             'valid_profile_reference': 'profile:' + self.c['profile']['id'],
                             'channel_reference_prefix': 'profile_channel:',
                             'instruction': 'evidence是引用ID列表，不是自然语言；channel:不是已注册前缀。'}
                self.agent.feedback(map_violations(report['violations'],self.toolbox.source_map), hints)
                self.events.emit('agent.retry', stage=stage.name, actor='agent', detail={'attempt': self.state['attempt'] + 1, 'violations': report['violations']})
                self.save()
            if 'pack' in self.c:
                self.store.write(self.directory / 'trace.json', self.c['pack']['trace'])
            self.state.update(status='completed', finished_at=now())
            self.save()
            self.events.emit('run.completed', actor='system', detail=deepcopy(self.state))
            return self.directory, self.state
        except LLMError as exc:
            return self.fail(exc.code, exc.violations)
        except Exception as exc:
            # Preserve safe failure evidence and the session, never provider text.
            return self.fail('runtime_' + type(exc).__name__)


def execute(inputs, root=Path('.'), *, client=None, run_id=None, observer=None, approve=None, source_root=Path('.')):
    return Session(inputs, root, client=client, run_id=run_id, observer=observer, source_root=source_root).run(approve=approve)
