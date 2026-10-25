"""SSE IDs are durable ledger line numbers, including skipped diagnostic rows."""
import asyncio
import json

from pipeline.io import resolve

EVENT_TYPES = {'stage_start', 'attempt', 'check', 'violation', 'backtrack',
               'tool_start', 'tool_done', 'gate_pending', 'gate_approved', 'stage_done', 'run_done', 'run_failed'}


async def stream_events(manager, run_id, last_id, request):
    path = manager.directory(run_id) / 'ledger.jsonl'
    offset = 0
    line_id = 0
    while True:
        if await request.is_disconnected():
            return
        # Snapshot completion BEFORE reading: a final append cannot be missed.
        done = manager.runs[run_id]['done'].is_set()
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(offset)
                while True:
                    line = stream.readline()
                    if not line or not line.endswith(b'\n'):
                        break
                    offset = stream.tell()
                    line_id += 1
                    if line_id <= last_id:
                        continue
                    entry = resolve(json.loads(line), path.parent)
                    kind = entry.get('event', 'attempt')
                    if kind not in EVENT_TYPES:
                        continue
                    event = dict(type=kind, stage=entry.get('stage') or entry.get('backtrack', {}).get('from'),
                                 attempt=entry.get('attempt'), payload=entry.get('payload', entry))
                    yield dict(id=str(line_id), event=kind, data=json.dumps(event, ensure_ascii=False))
        if done:
            return
        await asyncio.sleep(0.05)
