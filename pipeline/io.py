import json
import hashlib
import tempfile
from copy import deepcopy
from pathlib import Path
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    """Read legacy JSON or expand and verify a run's content-addressed references."""
    path = Path(path).resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    root = next((p for p in path.parents if (p / 'storage.json').is_file()), None)
    return resolve(value, root) if root else value


def resolve(value, root):
    """Expand run-relative references, verifying exact file bytes before decoding.

    Also usable for individual JSONL ledger entries. Old runs need no migration.
    The cache is per read; callers cannot mutate a shared decoded object.
    """
    root = Path(root).resolve()
    cache = {}
    active = set()

    def expand(item):
        if isinstance(item, dict):
            if set(item) == {'$run_ref', 'sha256'}:
                target = (root / item['$run_ref']).resolve()
                if not target.is_relative_to(root):
                    raise ValueError('Run reference escapes its directory')
                key = (target, item['sha256'])
                if key in active:
                    raise ValueError('Cyclic run reference')
                if key not in cache:
                    payload = target.read_bytes()
                    if hashlib.sha256(payload).hexdigest() != item['sha256']:
                        raise ValueError('Run reference SHA-256 mismatch')
                    active.add(key)
                    cache[key] = expand(json.loads(payload))
                    active.remove(key)
                return deepcopy(cache[key])
            return {k: expand(v) for k, v in item.items()}
        if isinstance(item, list):
            return [expand(v) for v in item]
        return item

    return expand(value)


class RunStore:
    """Immutable, run-local JSON DAG; identical large values are stored once.

    Named output files remain entry points. Their nested values use filename +
    SHA-256 references. Hashes cover canonical UTF-8 file bytes, including LF.
    Small values stay inline; containers over 512 bytes and explicit snapshots
    are interned. Recursion deduplicates repeated trace ancestors and reports,
    without changing the full in-memory artifacts passed to any gate.
    """
    def __init__(self, directory):
        self.directory = Path(directory)
        self.seen = set()
        write(self.directory / 'storage.json', {
            'version': 1, 'format': 'run-json-dag', 'reference_root': '.',
            'reference_keys': ['$run_ref', 'sha256'], 'hash': 'sha256-file-bytes',
            'reader': 'pipeline.io.read', 'inline_limit_bytes': 512,
        })

    def encode(self, value, force=False):
        if isinstance(value, dict):
            encoded = {k: self.encode(v) for k, v in value.items()}
        elif isinstance(value, list):
            encoded = [self.encode(v) for v in value]
        else:
            encoded = value
        payload = (json.dumps(encoded, ensure_ascii=False, sort_keys=True,
                              separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
        if not force and (not isinstance(value, (dict, list)) or len(payload) <= 512):
            return encoded
        digest = hashlib.sha256(payload).hexdigest()
        filename = f'objects/{digest}.json'
        if digest not in self.seen:
            target = self.directory / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            self.seen.add(digest)
        return {'$run_ref': filename, 'sha256': digest}

    def write(self, path, value):
        write(path, self.encode(value, force=True))

    def reference(self, path):
        path = Path(path)
        return {'$run_ref': path.relative_to(self.directory).as_posix(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def write(path, value):
    """Publish complete JSON with a same-directory atomic replacement.

    Serialization/replacement failures leave an existing destination untouched.
    This protects concurrent readers and process interruption, not power loss.
    """
    path = Path(path)
    payload = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                                         dir=path.parent, prefix='.' + path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
