"""Small, self-contained public demo bundles; never evaluate or relax run gates."""
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from pipeline.io import read
from pipeline.geo import EARTH_M
from pipeline.quality import LAYERS

SCHEMA_VERSION = '1.0.0'
FILE_LIMIT = 2_000_000
BUNDLE_LIMIT = 15_000_000
STAGES = ('L0', 'L1', 'L2', 'L2C', 'L3', 'L3P', 'L4', 'PACK')
# Export only display fields from large stage objects. Preserve audit metadata.
META = ('evidence', 'judged_by', 'confidence', 'open')
FIELDS = {
    'L1': ('channel_id', 'scene_ids', 'selling_point_ids', 'priority', 'reason', 'commercial_conditions'),
    'L2': ('zone_id', 'tier', 'type', 'counts', 'rank', 'selected'),
    'L2C': ('zone_id', 'counts', 'intensity', 'strategy', 'reason'),
    'L3': ('poi_id', 'channel_id', 'zone_id', 'score', 'grade', 'next_action', 'reason', 'score_components'),
    'L3P': ('partner_id', 'type', 'recommendation', 'coverage_count', 'covered_terminal_ids', 'recommended_zone_ids', 'reason'),
    'L4': ('target', 'pitch', 'table_card', 'selling_point_ids', 'compliance'),
}
FIELDS['PACK'] = FIELDS['L3']
PRIVATE = re.compile(r'(?:phone|telephone|mobile|email|contact|wechat|weixin|secret|password|token|api.?key|authorization|电话|手机|联系|微信|密钥)', re.I)
PRIVATE_TEXT = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?:api[_-]?key|access[_-]?token|password|secret)\s*[=:]\s*\S+', re.I)


def public(value):
    """Strip private keys recursively and redact recognizable contact/secret values."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not PRIVATE.search(k)}
    if isinstance(value, list):
        return [public(v) for v in value]
    if isinstance(value, str):
        return PRIVATE_TEXT.sub('[redacted]', value)
    return value


def pick(value, fields):
    return {k: value[k] for k in fields if k in value}


def polygon(zone_id, site):
    """Invert pipeline.geo.grid_id's 500 m grid in its original GCJ-02 frame."""
    x, y = map(int, zone_id.split(':'))
    def point(dx, dy):
        return [site['lon'] + math.degrees(dx * 500 / (EARTH_M * math.cos(math.radians(site['lat'])))),
                site['lat'] + math.degrees(dy * 500 / EARTH_M)]
    return {'type': 'Polygon', 'coordinates': [[point(x, y), point(x+1, y), point(x+1, y+1), point(x, y+1), point(x, y)]]}


def encode(value):
    return (json.dumps(public(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def validate_references(documents, map_data):
    ids = {p['poi_id'] for key in ('terminals', 'competitors', 'availability', 'partners') for p in map_data[key]}
    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                refs = ([child] if key in ('poi_id', 'partner_id') and child is not None else
                        child if key in ('poi_ids', 'terminal_ids', 'covered_terminal_ids', 'backlog_ids') else [])
                if any(pid not in ids for pid in refs):
                    raise ValueError('POI reference missing from map')
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(documents)


def export_run(run, out):
    run, out = Path(run).resolve(), Path(out).resolve()
    if out == run or out.is_relative_to(run) or run.is_relative_to(out):
        raise ValueError('Output must be separate from the source run')
    sources = {}
    missing = []
    def artifact(name, default=None):
        path = run / name
        if not path.is_file():
            missing.append(name)
            return default
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return read(path)  # Verify/expand content-addressed run objects, including legacy runs.

    status = artifact('status.json')
    inputs = artifact('inputs.json')
    if not status or not inputs:
        raise ValueError('Run status and inputs are required')
    pois = artifact('pois.json', [])
    profile = artifact('profile.json', {})
    market = artifact('market.json', {})
    params = artifact('params.json', {})
    site = inputs['site']
    if site.get('coordinate_system') != 'GCJ-02':
        raise ValueError('Only verified GCJ-02 runs can be exported')
    for p in pois + [p for key in ('competitors', 'availability', 'partners') for p in market.get(key, [])]:
        if p.get('coordinate_system') != 'GCJ-02':
            raise ValueError('Mixed or missing point coordinate system')
    stages = {}
    raw = {}
    for stage in STAGES:
        a = artifact(stage + '.json', {}) or {}
        raw[stage] = a
        check = artifact(stage + '.check.json')
        stages[stage] = dict(stage=stage, available=bool(a), accepted=stage in status['completed_stages'],
                             decision=a.get('decision'), items=[pick(r, FIELDS.get(stage, ()) + META) for r in a.get('items', [])],
                             check=check, history=[], **pick(a, META + ('seeded_for_demo',)))
        if stage == 'L0':
            stages[stage]['brief'] = a
            stages[stage]['decision'] = ('需求参数检查通过。' if check and check['passed'] else '需求参数未通过或尚未检查。')
            stages[stage]['decision_source'] = 'rules: check.passed'
        # Repeated source/assumption paragraphs are kept once, with explicit links.
        notes = {}
        for item in stages[stage]['items']:
            opened = item.pop('open', None)
            if opened is not None:
                key = hashlib.sha256(encode(opened)).hexdigest()[:16]
                notes[key] = opened
                item['open_ref'] = key
        stages[stage]['item_notes'] = notes
    ledger = run / 'ledger.jsonl'
    if ledger.is_file():
        sources['ledger.jsonl'] = hashlib.sha256(ledger.read_bytes()).hexdigest()
        # JSONL is a stream, not a JSON artifact; never deserialize its run refs directly.
        # Read every referenced attempt/check using the verified artifact reader above.
        for line in ledger.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            stage = entry.get('stage', entry.get('backtrack', {}).get('from'))
            if stage not in stages:
                raise ValueError('Unrecognized ledger stage')
            row = pick(entry, ('timestamp', 'stage', 'event', 'revision', 'attempt', 'status', 'violations', 'duration_seconds', 'judged_by', 'approved_by', 'seeded_for_demo', 'backtrack', 'count', 'parameter', 'before', 'after'))
            if entry.get('artifact_path'):
                path = (run / entry['artifact_path']).resolve()
                if not path.is_relative_to(run):
                    raise ValueError('Attempt path escapes run')
                # Exact hash in ledger must match even for legacy attempts.
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if entry.get('artifact_ref', {}).get('sha256', digest) != digest:
                    raise ValueError('Ledger attempt hash mismatch')
                sources[entry['artifact_path']] = digest
                check_path = (run / entry['check_path']).resolve()
                if not check_path.is_relative_to(run):
                    raise ValueError('Check path escapes run')
                check_digest = hashlib.sha256(check_path.read_bytes()).hexdigest()
                if entry.get('check_ref', {}).get('sha256', check_digest) != check_digest:
                    raise ValueError('Ledger check hash mismatch')
                row['check'] = artifact(entry['check_path'])
                row['retry'] = entry['attempt'] > 1
                if entry.get('status') == 'failed':
                    attempt = read(path)
                    failed = attempt.get('artifact') or {}
                    # Retain offending material only; all violation details remain in check.
                    row['failed_items'] = [pick(r, FIELDS.get(stage, ()) + META) for r in failed.get('items', []) if r.get('compliance', {}).get('passed') is False]
            stages[stage]['history'].append(row)
    else:
        missing.append('ledger.jsonl')

    pack = raw['PACK'] if stages['PACK']['accepted'] else {}
    terminals = pack.get('items', raw['L3'].get('items', []))
    supplies = pack.get('supply_assignments', {})
    competition = {r['zone_id']: r for r in raw['L2C'].get('items', [])}
    partners = {r['partner_id']: r for r in raw['L3P'].get('items', [])}
    def point(p):
        return dict(poi_id=p['id'], **pick(p, ('name', 'lat', 'lon', 'category', 'coordinate_system', 'source', 'brands')))
    terminal_by_id = {t['poi_id']: t for t in terminals}
    map_points = [dict(**point(p), **pick(terminal_by_id.get(p['id'], {}), FIELDS['L3'][1:]),
                       suggested_supplier=supplies.get(p['id'])) for p in pois]
    zones = [dict(**pick(z, FIELDS['L2']), geometry=polygon(z['zone_id'], site),
                  intensity=competition.get(z['zone_id'], {}).get('intensity'), strategy=competition.get(z['zone_id'], {}).get('strategy')) for z in raw['L2'].get('items', [])]
    map_data = dict(coordinate_system='GCJ-02', site=site, grids=zones, terminals=map_points,
                    competitors=[point(p) for p in market.get('competitors', [])],
                    availability=[point(p) for p in market.get('availability', [])],
                    partners=[dict(**point(p), **pick(partners.get(p['id'], {}), ('partner_id', 'type', 'recommendation', 'coverage_count', 'recommended_zone_ids'))) for p in market.get('partners', [])],
                    coverage=market.get('coverage'), open=market.get('open', []))
    routes = {}
    for route in pack.get('days', []):
        for order, pid in enumerate(route['poi_ids'], 1):
            routes[pid] = dict(**pick(route, ('week', 'day', 'person', 'route_distance_m')), order=order, date=None)
    materials = {r['target']: r for r in raw['L4'].get('items', [])} if stages['L4']['accepted'] else {}
    poi_lookup = {p['id']: p for p in pois}
    terminal_rows = []
    for t in pack.get('items', []):
        pid = t['poi_id']
        target = pid if pid in materials else t['channel_id'] if t['channel_id'] in materials else None
        terminal_rows.append(dict(**t, name=poi_lookup[pid]['name'], route=routes.get(pid),
                                  suggested_supplier=supplies.get(pid), pitch_ref={'file': 'stages.json', 'stage': 'L4', 'target': target} if target else None,
                                  trace_ref='trace/' + quote(pid, safe='') + '.json' if t['grade'] == 'A' else None))
    checks = {s: [{'layer': r['layer'], 'status': r['status']} for r in stages[s]['check']['layers']] if stages[s]['check'] else [{'layer': layer, 'status': 'not_run'} for layer in LAYERS] for s in STAGES}
    counts = dict(grades={g: sum(t['grade'] == g for t in terminals) for g in ('A', 'B', 'C')},
                  channels=dict(Counter(t['channel_id'] for t in terminals)), zones=len(zones), selected_zones=sum(z['selected'] for z in zones),
                  competition_intensity=dict(Counter(z['intensity'] for z in zones if z['selected'] and z['intensity'] is not None)),
                  partners=len(map_data['partners']), recommended_partners=sum(r.get('recommendation') == '优先' for r in partners.values()),
                  recruitment_gaps=pack.get('recruitment_gaps', {}).get('terminal_count'),
                  review_queue=sum(len(stages[s]['check']['review_queue']) for s in STAGES if stages[s]['accepted'] and stages[s]['check']),
                  terminals=len(terminals), pack_terminals=len(terminal_rows))
    summary = dict(scenario_id=out.name, area_id=inputs.get('area_id'), area=site, product_profile=pick(profile, ('id', 'product', 'scenes', 'selling_points', 'source')),
                   status=status, judged_by=status.get('judged_by'), demo_replay=inputs.get('demo_replay'),
                   conclusions={s: stages[s]['decision'] for s in STAGES}, checks=checks, counts=counts,
                   assumptions={'brief': raw['L0'].get('assumptions', []), 'params': params}, missing_artifacts=missing,
                   terminal_count_source='PACK' if pack else 'L3' if stages['L3']['accepted'] else 'L3 latest attempt (not accepted)',
                   route_date_note='源run只含周、工作日、人员与顺序，无起始日期；date为null。')
    documents = {'summary.json': summary, 'stages.json': {'stages': list(stages.values())}, 'map.json': map_data,
                 'terminals.json': dict(available=bool(pack), items=terminal_rows, days=pack.get('days', []),
                                        backlog_ids=[t['poi_id'] for t in pack.get('backlog', [])], recruitment_gaps=pack.get('recruitment_gaps'), planning_note=pack.get('planning_note'))}
    if pack:
        trace = artifact('trace.json', {})
        for t in terminal_rows:
            if t['grade'] == 'A':
                if t['poi_id'] not in trace:
                    raise ValueError('Missing A terminal trace')
                documents[t['trace_ref']] = dict(poi_id=t['poi_id'], trace=trace[t['poi_id']])
    generated = dict(run_id=status['run_id'], files=dict(sorted(sources.items())))
    validate_references(documents, map_data)
    payloads = {}
    for name, body in documents.items():
        provenance = generated
        if name.startswith('trace/'):
            provenance = dict(run_id=status['run_id'], files={key: sources[key] for key in ('status.json', 'trace.json')})
        payloads[name] = encode(dict(schema_version=SCHEMA_VERSION, generated_from=provenance, **body))
    for name, payload in payloads.items():
        if len(payload) > FILE_LIMIT:
            raise ValueError(f'{name} exceeds {FILE_LIMIT} bytes')
    root = out.parent
    index_path = root / 'index.json'
    index = read(index_path) if index_path.is_file() else {'scenarios': []}
    entry = dict(id=out.name, area_id=inputs.get('area_id'), demo_replay=inputs.get('demo_replay'), status=status['status'],
                 summary=out.name + '/summary.json', generated_from=generated)
    scenarios = sorted([r for r in index['scenarios'] if r['id'] != out.name] + [entry], key=lambda r: r['id'])
    index_payload = encode(dict(schema_version=SCHEMA_VERSION, generated_from={'runs': [r['generated_from'] for r in scenarios]}, scenarios=scenarios))
    if len(index_payload) > FILE_LIMIT:
        raise ValueError('index.json exceeds file limit')
    # Include other scenarios and schema docs before replacing any files.
    existing = sum(p.stat().st_size for p in root.rglob('*') if p.is_file() and not p.is_relative_to(out) and p != index_path) if root.exists() else 0
    extras = sum(p.stat().st_size for p in out.rglob('*') if p.is_file() and p.relative_to(out).as_posix() not in payloads and p.parent != out / 'trace') if out.exists() else 0
    total = existing + extras + sum(map(len, payloads.values())) + len(index_payload)
    if total > BUNDLE_LIMIT:
        raise ValueError(f'Data bundle exceeds {BUNDLE_LIMIT} bytes')
    # All validation precedes writes. Remove only obsolete files owned by this exporter.
    for p in out.glob('trace/*.json'):
        if p.relative_to(out).as_posix() not in payloads:
            p.unlink()
    for name, payload in payloads.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    index_path.write_bytes(index_payload)
    return dict(run_id=status['run_id'], out=str(out), status=status['status'], missing_artifacts=missing,
                files={name: len(payload) for name, payload in payloads.items()}, bundle_bytes=total)
