"""Bounded, cache-only background runs and revision-specific human gates."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pipeline.__main__ import prepare_data
from pipeline.engine import execute
from pipeline.io import now, write
from pipeline.judge import Judge
from pipeline.market_capture import load_market
from pipeline.profiles import load, parameters


class RunManager:
    def __init__(self, root=Path('.'), source_root=None):
        self.root = Path(root)
        if source_root is None:
            source_root = Path.cwd()
            git_file = source_root / '.git'
            if git_file.is_file():
                git_dir = Path(git_file.read_text().strip().removeprefix('gitdir: '))
                source_root = (source_root / git_dir).resolve().parents[2]
        self.source_root = Path(source_root)
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(2)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='pipeline')
        self.runs = {}
        self.closed = False

    def start(self, request):
        # Validation is cache-only and occurs before a slot or ID is allocated.
        profile = load(request.profile)
        params = parameters()
        pois, coverage = prepare_data(params, request.area)
        inputs = dict(request=request.input, profile=profile, params=params,
                      pois=pois, coverage=coverage, site=coverage['site'],
                      area_id=request.area, judge=Judge(True),
                      market=load_market(request.area), no_defaults=False,
                      auto_approve=request.auto_approve, demo_replay=request.demo_replay)
        with self.lock:
            if self.closed or not self.slots.acquire(blocking=False):
                raise RuntimeError('最多同时运行两个任务（含等待人工确认的任务）')
            run_id = uuid.uuid4().hex
            self.runs[run_id] = dict(run_id=run_id, status='queued', current_stage=None,
                                     completed_stages=[], gate=None, done=threading.Event())
            try:
                self.executor.submit(self._run, run_id, inputs)
            except Exception:
                del self.runs[run_id]
                self.slots.release()
                raise
        return run_id

    def directory(self, run_id):
        with self.lock:
            if run_id not in self.runs:
                raise KeyError(run_id)
        return self.root / 'runs' / run_id

    def status(self, run_id):
        with self.lock:
            row = self.runs[run_id]
            return {k: (list(v) if isinstance(v, list) else v) for k, v in row.items()
                    if k not in ('gate', 'done')}

    def _observe(self, run_id, entry):
        with self.lock:
            row = self.runs[run_id]
            kind = entry.get('event')
            if kind == 'stage_start':
                row.update(status='running', current_stage=entry['stage'])
            elif kind == 'stage_done':
                row['completed_stages'] = entry['payload']['completed_stages']
            elif kind == 'gate_pending':
                row.update(status='gate_pending', pending_artifact=f"{entry['stage']}.pending.json", gate=dict(stage=entry['stage'],
                           preview=self.directory(run_id) / entry['payload']['preview'],
                           signal=threading.Event(), decision=None))
            elif kind == 'backtrack' and entry['status'] == 'returning':
                row['completed_stages'] = [s for s in row['completed_stages'] if s in ('L0', 'L1')]
            elif kind in ('run_done', 'run_failed'):
                row.update(entry['payload'])
                row['current_stage'] = row.get('stopped_at')

    def _approve(self, run_id, stage, preview):
        with self.lock:
            if self.closed:
                return False
            gate = self.runs[run_id]['gate']
        gate['signal'].wait()
        with self.lock:
            self.runs[run_id].update(status='running', gate=None, pending_artifact=None)
        return gate['decision'] == 'approve'

    def decide(self, run_id, stage, decision, note):
        with self.lock:
            gate = self.runs[run_id]['gate']
            if not gate or gate['stage'] != stage or gate['decision'] is not None:
                raise ValueError('该关卡没有等待中的人工确认')
            # Preserve the engine preview; decisions are immutable revision siblings.
            write(gate['preview'].with_suffix('.decision.json'),
                  dict(stage=stage, decision=decision, note=note, approved_by='human', timestamp=now()))
            gate['decision'] = decision
            gate['signal'].set()

    def _run(self, run_id, inputs):
        try:
            execute(inputs, self.root, run_id=run_id, source_root=self.source_root,
                    observer=lambda entry: self._observe(run_id, entry),
                    approve=lambda stage, preview: self._approve(run_id, stage, preview))
        except Exception as exc:
            # Do not expose provider/environment exception text through the API.
            with self.lock:
                row = self.runs[run_id]
                row.update(status='failed', error_type=type(exc).__name__, finished_at=now())
                state = self.status(run_id)
            directory = self.directory(run_id)
            write(directory / 'status.json', state)
            with (directory / 'ledger.jsonl').open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(json.dumps(dict(event='run_failed', payload=state, timestamp=now())) + '\n')
        finally:
            self.slots.release()
            self.runs[run_id]['done'].set()

    def close(self):
        with self.lock:
            self.closed = True
            for row in self.runs.values():
                if row['gate'] and row['gate']['decision'] is None:
                    self.decide(row['run_id'], row['gate']['stage'], 'reject', '服务关闭，未获人工批准')
        self.executor.shutdown(wait=True)
