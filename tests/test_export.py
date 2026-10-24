"""Exporter contracts and real offline bundle acceptance, without network access."""
import hashlib
import json
from pathlib import Path

import pytest

from pipeline import export
from pipeline.geo import grid_id
from pipeline.io import RunStore, read, write
from pipeline.quality import LAYERS


@pytest.fixture
def run(tmp_path):
    directory = tmp_path / 'run'
    store = RunStore(directory)
    site = dict(lat=31.29, lon=120.56, coordinate_system='GCJ-02')
    p = dict(id='fixture:1', name='synthetic fixture', category='restaurant', **site, phone='13800138000')
    write(directory / 'status.json', dict(run_id='fixture', status='completed', completed_stages=list(export.STAGES), judged_by='rules'))
    for name, value in {'inputs': dict(site=site, area_id='fixture'), 'pois': [p], 'profile': {'id': 'fixture'}, 'params': {}, 'market': {}}.items():
        store.write(directory / f'{name}.json', value)
    terminal = dict(poi_id=p['id'], channel_id='restaurant', zone_id='0:0', grade='A', score=0.9, next_action='fixture')
    for stage in export.STAGES:
        items = [terminal] if stage in ('L3', 'PACK') else []
        a = dict(decision='synthetic fixture', items=items, judged_by='rules')
        if stage == 'L2':
            a['items'] = [dict(zone_id='0:0', selected=True, type='混合', rank=1, counts={})]
        if stage == 'L4':
            a['items'] = [dict(target=p['id'], pitch='synthetic fixture', table_card='fixture')]
        if stage == 'PACK':
            a.update(days=[dict(week=1, day=1, person=1, poi_ids=[p['id']], route_distance_m=0)], recruitment_gaps={'terminal_count': 0, 'terminal_ids': [], 'zone_ids': []})
        store.write(directory / f'{stage}.json', a)
        store.write(directory / f'{stage}.check.json', dict(passed=True, layers=[dict(layer=k, status='passed', violations=[]) for k in LAYERS], review_queue=[], violations=[]))
    store.write(directory / 'trace.json', {p['id']: {'terminal': {'data': terminal}, 'email': 'fixture@example.com'}})
    (directory / 'ledger.jsonl').write_text('', encoding='utf-8')
    return directory


def test_reference_storage_export_and_repeat(run, tmp_path):
    out = tmp_path / 'web/data/fixture'
    result = export.export_run(run, out)
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob('*.json')}
    export.export_run(run, out)
    assert before == {p.relative_to(out): p.read_bytes() for p in out.rglob('*.json')}
    assert result['missing_artifacts'] == []
    summary = read(out / 'summary.json')
    assert summary['counts']['grades'] == {'A': 1, 'B': 0, 'C': 0}
    assert summary['generated_from']['files']['pois.json'] == hashlib.sha256((run / 'pois.json').read_bytes()).hexdigest()
    row = read(out / 'terminals.json')['items'][0]
    assert row['route']['order'] == 1 and row['route']['date'] is None
    assert row['pitch_ref']['target'] == 'fixture:1'
    assert read(out / row['trace_ref'])['poi_id'] == 'fixture:1'
    assert not any(b'13800138000' in data or b'fixture@example.com' in data for data in before.values())


def test_tampered_object_rejected(run, tmp_path):
    ref = json.loads((run / 'pois.json').read_text())
    (run / ref['$run_ref']).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA-256'):
        export.export_run(run, tmp_path / 'out/fixture')


@pytest.mark.parametrize('limit', ['FILE_LIMIT', 'BUNDLE_LIMIT'])
def test_size_gate_before_writes(run, tmp_path, monkeypatch, limit):
    monkeypatch.setattr(export, limit, 1)
    out = tmp_path / 'out/fixture'
    with pytest.raises(ValueError, match='exceeds'):
        export.export_run(run, out)
    assert not out.exists()


def test_coordinate_mismatch_rejected(run, tmp_path):
    inputs = read(run / 'inputs.json')
    inputs['site']['coordinate_system'] = 'WGS-84'
    write(run / 'inputs.json', inputs)
    with pytest.raises(ValueError, match='GCJ-02'):
        export.export_run(run, tmp_path / 'out/fixture')


def test_dangling_poi_rejected(run, tmp_path):
    pack = read(run / 'PACK.json')
    pack['recruitment_gaps']['terminal_ids'] = ['missing']
    write(run / 'PACK.json', pack)
    with pytest.raises(ValueError, match='POI reference'):
        export.export_run(run, tmp_path / 'out/fixture')


def test_blocked_run_has_no_pack_or_stale_trace(run, tmp_path):
    out = tmp_path / 'out/fixture'
    export.export_run(run, out)
    state = read(run / 'status.json')
    state.update(status='blocked', completed_stages=['L0', 'L1', 'L2', 'L2C'])
    write(run / 'status.json', state)
    export.export_run(run, out)
    assert read(out / 'terminals.json')['items'] == []
    assert not list((out / 'trace').glob('*.json'))
    assert read(out / 'summary.json')['counts']['recruitment_gaps'] is None


def test_grid_inverse():
    site = dict(lat=31.29, lon=120.56)
    for zid in ('0:0', '-3:2', '2:-3'):
        ring = export.polygon(zid, site)['coordinates'][0]
        assert ring[0] == ring[-1]
        assert grid_id(dict(lon=(ring[0][0]+ring[2][0])/2, lat=(ring[0][1]+ring[2][1])/2), site) == zid


ROOT = Path(__file__).resolve().parents[1] / 'web/data'
SCENARIOS = ('shishan', 'guanqian', 'ligongdi', 'guanqian-compliance', 'backtrack')


@pytest.mark.parametrize('scenario', SCENARIOS)
def test_delivered_bundle_contract(scenario):
    directory = ROOT / scenario
    summary = read(directory / 'summary.json')
    stages = read(directory / 'stages.json')['stages']
    map_data = read(directory / 'map.json')
    terminals = read(directory / 'terminals.json')
    assert set(summary) >= {'area', 'product_profile', 'conclusions', 'checks', 'counts', 'generated_from', 'schema_version'}
    assert [s['stage'] for s in stages] == list(export.STAGES)
    assert all(len(v) == 5 for v in summary['checks'].values())
    assert map_data['coordinate_system'] == 'GCJ-02'
    assert map_data['competitors'] and map_data['partners']
    docs = {p.relative_to(directory).as_posix(): read(p) for p in directory.rglob('*.json')}
    export.validate_references(docs, map_data)
    for doc in docs.values():
        assert doc['schema_version'] == export.SCHEMA_VERSION
        assert doc['generated_from']['run_id'] == summary['status']['run_id']
        assert doc == export.public(doc)
    assert all(p.stat().st_size <= export.FILE_LIMIT for p in directory.rglob('*.json'))
    expected = {t['trace_ref'] for t in terminals['items'] if t['grade'] == 'A'}
    assert expected == {name for name in docs if name.startswith('trace/')}
    for stage in stages:
        for item in stage['items']:
            if 'open_ref' in item:
                assert item['open_ref'] in stage['item_notes']
    if scenario == 'guanqian-compliance':
        history = next(s for s in stages if s['stage'] == 'L4')['history']
        assert any(r.get('seeded_for_demo') and r['violations'] and r['failed_items'] for r in history)
        assert any(r.get('retry') and r['status'] == 'passed' for r in history)
    if scenario == 'backtrack':
        assert any(r.get('event') == 'backtrack' for s in stages for r in s['history'])


def test_bundle_index_and_total_size():
    index = read(ROOT / 'index.json')
    assert {s['id'] for s in index['scenarios']} == set(SCENARIOS)
    assert index['schema_version'] == export.SCHEMA_VERSION and index['generated_from']
    assert sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file()) <= export.BUNDLE_LIMIT
    assert all(p.stat().st_size <= export.FILE_LIMIT for p in ROOT.rglob('*') if p.is_file())


def test_private_fields_recursive():
    value = {'contact_info': 'hidden', 'nested': [{'api_key': 'secret', 'phone': '13800138000', 'note': 'email fixture@example.com'}]}
    assert export.public(value) == {'nested': [{'note': 'email [redacted]'}]}


def test_ledger_check_hash_mismatch(run, tmp_path):
    attempt = run / 'attempts/L4-r1-1.json'
    write(attempt, {'artifact': None, 'check': read(run / 'L4.check.json')})
    entry = dict(stage='L4', revision=1, attempt=1, status='failed', violations=[],
                 artifact_path='attempts/L4-r1-1.json', check_path='L4.check.json', check_ref={'sha256': '0'*64})
    (run / 'ledger.jsonl').write_text(json.dumps(entry) + '\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Ledger check hash mismatch'):
        export.export_run(run, tmp_path / 'out/fixture')


def test_cli_dispatch(run, tmp_path, monkeypatch, capsys):
    from pipeline.__main__ import main
    out = tmp_path / 'out/fixture'
    monkeypatch.setattr('sys.argv', ['pipeline', 'export', '--run', str(run), '--out', str(out)])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'completed'
    assert (out / 'summary.json').is_file()
