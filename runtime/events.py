"""One durable event envelope; only public intentions, never hidden reasoning."""
import json
import threading
from pathlib import Path
from pipeline.io import now


class Events:
    def __init__(self, directory, observer=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'events.jsonl'
        self.lock = threading.RLock()
        self.observer = observer
        self.seq = 0
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines():
                self.seq = max(self.seq, json.loads(line)['seq'])

    def emit(self, kind, *, stage=None, actor='program', title=None, detail=None, refs=None, metrics=None,
             status='ok', parent_id=None):
        with self.lock:
            self.seq += 1
            row = dict(id=f'{self.directory.name}:{self.seq}', run_id=self.directory.name,
                       seq=self.seq, ts=now(), stage=stage, actor=actor, kind=kind,
                       title=title or kind, detail=detail or {}, refs=refs or [], metrics=metrics or {},
                       status=status, parent_id=parent_id)
            with self.path.open('a', encoding='utf-8', newline='\n') as stream:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                stream.flush()
            if self.observer:
                self.observer(row)
            return row
