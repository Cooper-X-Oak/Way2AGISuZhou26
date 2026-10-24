"""Storage contracts: immutable snapshots, verified references and lossless reads."""
import hashlib
import json

import pytest

from pipeline.io import RunStore, read, resolve, write


def raw(path):
    return json.loads(path.read_text(encoding='utf-8'))


def test_large_object_stored_once_and_all_hashes_verify(tmp_path):
    store = RunStore(tmp_path)
    pois = [{'id': str(i), 'evidence': 'fixture' * 100} for i in range(30)]
    for name in ('pois', 'inputs', 'attempts/first', 'trace'):
        store.write(tmp_path / f'{name}.json', {'pois': pois})
    roots = [raw(tmp_path / f'{name}.json') for name in ('pois', 'inputs', 'attempts/first', 'trace')]
    assert all(ref == roots[0] for ref in roots)
    # One stored copy of each unique payload; all named files are references.
    blobs = list((tmp_path / 'objects').glob('*.json'))
    assert len(blobs) == len({p.read_bytes() for p in blobs})
    for blob in blobs:
        assert hashlib.sha256(blob.read_bytes()).hexdigest() == blob.stem
    assert read(tmp_path / 'trace.json') == {'pois': pois}
    before = len(blobs)
    store.write(tmp_path / 'another.json', {'pois': pois})
    assert len(list((tmp_path / 'objects').glob('*.json'))) == before


def test_snapshot_mutation_keeps_original_and_nested_reads_independent(tmp_path):
    store = RunStore(tmp_path)
    params = {'threshold': 2, 'source': 'fixture' * 200}
    store.write(tmp_path / 'inputs.json', {'params': params, 'other': params})
    params['threshold'] = 3
    store.write(tmp_path / 'params.json', params)
    original = read(tmp_path / 'inputs.json')
    assert original['params']['threshold'] == 2
    assert read(tmp_path / 'params.json')['threshold'] == 3
    original['params']['threshold'] = 999
    assert original['other']['threshold'] == 2


def test_tampering_missing_and_path_escape_rejected(tmp_path):
    store = RunStore(tmp_path)
    store.write(tmp_path / 'snapshot.json', {'data': list(range(1000))})
    ref = raw(tmp_path / 'snapshot.json')
    blob = tmp_path / ref['$run_ref']
    blob.write_bytes(blob.read_bytes() + b' ')
    with pytest.raises(ValueError, match='SHA-256'):
        read(tmp_path / 'snapshot.json')
    blob.unlink()
    with pytest.raises(FileNotFoundError):
        read(tmp_path / 'snapshot.json')
    with pytest.raises(ValueError, match='escapes'):
        resolve({'$run_ref': '../outside.json', 'sha256': '0' * 64}, tmp_path)


def test_legacy_json_is_unchanged(tmp_path):
    value = {'pois': [{'id': 'fixture'}], 'params': {'threshold': 2}}
    write(tmp_path / 'legacy.json', value)
    assert read(tmp_path / 'legacy.json') == value
