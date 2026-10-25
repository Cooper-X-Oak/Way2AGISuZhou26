"""把一次运行映射成一个离线网页：uv run python -m pipeline view --run runs/<id> -o demo.html

网页只展示，不计算：所有数值都从 run 目录的已校验产物读取（pipeline.io.read 会核对 SHA-256）。
单文件、无外部依赖，双击即可打开；以后做正式 UI 时可直接复用 build() 输出的数据。
"""
import json
import math
from pathlib import Path

from pipeline.geo import EARTH_M, distance
from pipeline.io import read

ORDER = ('L0', 'L1', 'L1D', 'L2', 'L2C', 'L3', 'L3P', 'L4', 'PACK')
NAMES = {'L0': '立项', 'L1': '卖给谁', 'L1D': '找终端', 'L2': '去哪里', 'L2C': '对手在哪', 'L3': '进哪家',
         'L3P': '谁来供货', 'L4': '怎么说', 'PACK': '作战包'}
TOOL_LABEL = {'area_inventory': '盘点现有商户', 'search_merchants': '高德补采', 'list_merchants': '查看候选'}


def _opt(directory, name, default=None):
    path = Path(directory) / name
    try: return read(path) if path.is_file() else default
    except (FileNotFoundError, ValueError): return default


def _xy(p, site):
    """Metres east/north of the site centre (same frame as pipeline.geo.grid_id)."""
    x = EARTH_M * math.radians(p['lon'] - site['lon']) * math.cos(math.radians(site['lat']))
    y = EARTH_M * math.radians(p['lat'] - site['lat'])
    return round(x, 1), round(y, 1)


def _ledger(directory):
    path = Path(directory) / 'ledger.jsonl'
    rows=[]
    for line in path.read_text(encoding='utf-8').splitlines() if path.is_file() else []:
        try: rows.append(json.loads(line))
        except ValueError: pass
    return rows


def _mode(model):
    if not model:
        return '规则'
    return '回放' if str(model).startswith('replay:') else '实况'


def build(directory, title=None, created_at=None):
    directory = Path(directory)
    status = read(directory / 'status.json')
    profile = read(directory / 'profile.json')
    brief = _opt(directory, 'brief.json') or _opt(directory,'L0.json') or _opt(directory,'inputs.json',{})
    coverage = read(directory / 'coverage.json')
    site = coverage['site']
    pois = read(directory / 'pois.json')
    names = {c['id']: c['name'] for c in profile['channels']}
    ledger = _ledger(directory)

    discovery = _opt(directory, 'discovery.json') or _opt(directory, 'L1D.json') or {}
    agent = discovery.get('agent') or {}
    added = {p['id']: p for p in discovery.get('added_pois', [])}
    # Supplemented points become visible while the agent is still searching.
    if not discovery.get('added_pois'):
        from pipeline.amap import AmapAdapter
        existing={p['id'] for p in pois}
        for path in (directory/'agent/amap').glob('*.json'):
            row=_opt(path.parent,path.name,{})
            for p in AmapAdapter().normalize(row.get('payload',{}),site,brief.get('radius_m') or 3000):
                if p['id'] not in existing:
                    added[p['id']]={**p,'raw_ref':str(path.relative_to(directory).as_posix())}

    # Agent steps: only the attempt that passed L1D.
    passed = [e['attempt'] for e in ledger if e.get('stage') == 'L1D' and e.get('status') == 'passed' and e.get('attempt')]
    steps_path = directory / 'agent' / 'steps.jsonl'
    raw_steps=[]
    for line in steps_path.read_text(encoding='utf-8').splitlines() if steps_path.is_file() else []:
        try: raw_steps.append(json.loads(line))
        except ValueError: pass
    steps = []
    for s in raw_steps:
        if s['kind'] == 'model':
            steps.append(dict(kind='model', calls=[TOOL_LABEL.get(c, c) for c in s.get('tool_calls', [])], note=''))
        elif s['kind'] == 'tool':
            r = s.get('result') or {}
            sources = set(r.get('evidence') or [])
            steps.append(dict(kind='tool', tool=s['tool'], label=TOOL_LABEL.get(s['tool'], s['tool']),
                              args={k: v for k, v in (s.get('args') or {}).items() if v not in (None, '')},
                              ok=bool(s.get('ok', True)), error=r.get('error'),
                              returned=r.get('returned'), new=r.get('new'), total=r.get('total'),
                              by_category=r.get('by_category'), by_channel=r.get('by_channel') or r.get('by_channel_after'), sample=r.get('sample') or r.get('items') or [],
                              added_ids=[pid for pid, p in added.items() if p.get('raw_ref') in sources]))
        elif s['kind'] == 'rejected':
            steps.append(dict(kind='rejected', problems=s.get('problems', [])[:5]))

    # Stages: final status from status.json, attempts/backtracks from ledger.
    attempts = {}
    for e in ledger:
        if e.get('attempt') and e.get('stage') in ORDER:
            attempts[e['stage']] = attempts.get(e['stage'], 0) + 1
    zones = (_opt(directory, 'zones.json') or {}).get('items', [])
    terminals = (_opt(directory, 'terminals.json') or {}).get('items', [])
    materials = (_opt(directory, 'materials.json') or {}).get('items', [])
    pack = _opt(directory, 'pack.json') or {}
    l1 = (_opt(directory, 'channels.json') or {}).get('items', [])
    grades = {g: sum(t['grade'] == g for t in terminals) for g in 'ABC'}
    selected = [z for z in zones if z.get('selected')]
    days = pack.get('days', [])
    first_day = days[0] if days else None
    scheduling_pending = any(brief.get(k) is None for k in ('delivery_people','weeks','visits_per_person_day','planned_visits_day'))
    summaries = {
        'L0': f"{(brief.get('radius_m') or 0) / 1000:g} km · " + ('人员排期待安排' if scheduling_pending else f"{brief['delivery_people']} 人 × {brief['weeks']} 周"),
        'L1': ' · '.join(f"{names.get(r['channel_id'], r['channel_id'])} {r['priority']}" for r in l1 if r['priority'] in ('P1', 'P2')) or '—',
        'L1D': (f"补采 +{len(added)} 家" if agent else '规则：只盘点缓存'),
        'L2': f"选中 {len(selected)} 个网格" if zones else '—',
        'L2C': f"{len(zones)} 个网格已评估竞争" if zones else '—',
        'L3': f"A {grades['A']} · B {grades['B']} · C {grades['C']}" if terminals else '—',
        'L3P': f"{len((_opt(directory, 'partners.json') or {}).get('items', []))} 个供货候选",
        'L4': f"{len(materials)} 条话术 · 合规{'通过' if materials and all(m['compliance']['passed'] for m in materials) else '—'}",
        'PACK': ('策略已生成 · 排期待安排' if scheduling_pending else f"{len(days)} 个人日 · 首日 {len((first_day or {}).get('poi_ids', [])) if first_day else 0} 家") if pack else '—',
    }
    FILES = {'L0': 'brief.json', 'L1': 'channels.json', 'L1D': 'discovery.json', 'L2': 'zones.json', 'L2C': 'competition.json',
             'L3': 'terminals.json', 'L3P': 'partners.json', 'L4': 'materials.json', 'PACK': 'pack.json'}
    LAYER = {'structure': '结构', 'references': '引用', 'business': '业务', 'judgment_quality': '判断质量', 'human_gate': '人工关口'}
    stages = []
    for s in ORDER:
        art = _opt(directory, FILES[s]) or {}
        chk = _opt(directory, f'{s}.check.json') or {}
        stages.append(dict(id=s, name=NAMES[s], done=s in status.get('completed_stages', []),
                           stopped=status.get('stopped_at') == s, attempts=attempts.get(s, 0), summary=summaries[s],
                           agent=s == 'L1D' and bool(agent),
                           decision=art.get('decision') or ('需求参数检查通过。' if chk.get('passed') else None),
                           judged_by={'llm': '模型', 'rules': '规则'}.get(art.get('judged_by'), art.get('judged_by')),
                           checks=[dict(layer=LAYER.get(l['layer'], l['layer']), status=l['status']) for l in chk.get('layers', [])],
                           review=len(chk.get('review_queue', [])),
                           approved=next((e.get('approved_by') for e in ledger if e.get('stage') == s and e.get('approved_by')), None)))

    by_id = {p['id']: p for p in pois}
    radius = brief.get('radius_m') or coverage.get('radius_m', 3000)

    def point(p):
        x, y = _xy(p, site)
        return [x, y]

    base = [point(p) for pid, p in by_id.items() if pid not in added and distance(site['lat'], site['lon'], p['lat'], p['lon']) <= radius]
    found = []
    for pid, p in added.items():
        from pipeline.common import channel_for
        ch = channel_for(p, profile)
        x, y = _xy(p, site)
        found.append(dict(id=pid, name=p.get('name'), x=x, y=y, channel=names.get(ch['id']) if ch else None))
    term_rows = []
    for t in sorted(terminals, key=lambda t: ('ABC'.index(t['grade']), -t.get('score', 0))):
        p = by_id.get(t['poi_id'], {})
        x, y = _xy(p, site) if p else (0, 0)
        term_rows.append(dict(id=t['poi_id'], name=p.get('name') or '（无名称）', grade=t['grade'], channel=names.get(t['channel_id'], t['channel_id']),
                              zone=t['zone_id'], x=x, y=y, distance=round(distance(site['lat'], site['lon'], p['lat'], p['lon'])) if p else None,
                              reason=t.get('reason'), action=t.get('next_action'), score=t.get('score'), found=t['poi_id'] in added))
    pitches, seen = [], set()
    for m in materials:
        ch = next((t['channel_id'] for t in terminals if t['poi_id'] == m['target']), m['target'])
        if ch in seen:
            continue
        seen.add(ch)
        pitches.append(dict(channel=names.get(ch, ch), pitch=m['pitch'], card=m.get('table_card'), passed=m['compliance']['passed']))

    first_inventory = next((s for s in steps if s['kind'] == 'tool' and s['tool'] == 'area_inventory'), None)
    model = agent.get('model')
    stop_reason = '；'.join(dict.fromkeys(v.get('message', '') for v in status.get('violations') or [] if v.get('message'))) or None
    return dict(
        id=status.get('run_id'), title=title or f"{profile['product']['name']} · {'Agent 找终端' if agent else '规则版'}",
        created_at=created_at or status.get('started_at'), stopped_at=status.get('stopped_at'), stop_reason=stop_reason,
        product=profile['product']['name'], area=site.get('display_name') or coverage.get('area_id'), request=brief.get('request'),
        run_id=status.get('run_id'), status=status.get('status'), mode=_mode(model), model=str(model or '').removeprefix('replay:') or None,
        radius=radius,
        brief={**{k: brief.get(k) for k in ('request', 'radius_m', 'delivery_people', 'weeks', 'assumptions', 'questions', 'site_note', 'location_note')}, 'product_background': profile.get('product', {}), 'library_selection': _opt(directory, 'inputs.json', {}).get('library_selection')},
        area_id=coverage.get('area_id'),
        channel_names=names,
        targets=[names.get(r['channel_id'], r['channel_id']) for r in l1 if r['priority'] in ('P1', 'P2')],
        cache=dict(total=len(base), by_category=(first_inventory or {}).get('by_category') or {}),
        counts=dict(model_calls=agent.get('model_calls'), tool_calls=agent.get('tool_calls'), amap_calls=agent.get('amap_calls'), added=len(added)),
        decision=discovery.get('decision'),
        channels=[dict(id=r['channel_id'], name=names.get(r['channel_id'], r['channel_id']), after=r.get('candidate_count',0)) for r in (discovery.get('items') or l1)],
        stages=stages, steps=steps,
        map=dict(base=base, found=found, zones=[dict(id=z['zone_id'], rank=z.get('rank')) for z in selected]),
        terminals=term_rows, pitches=pitches,
        route=dict(days=len(days), pending=scheduling_pending, planning_note=pack.get('planning_note'), first=[by_id.get(pid, {}).get('name') for pid in (first_day or {}).get('poi_ids', [])]),
        boundary=[_judgment_note(stages),
                  '补采商户来自高德 POI 基础字段，不代表实际经营情况或进货意愿，需走访确认。',
                  '价格、毛利、首单政策未知，话术中不做承诺。'],
    )


def _judgment_note(stages):
    """Say which stages the model actually judged in this run, from each artifact's judged_by."""
    # PACK only assembles code results; its label mirrors the run's judge, so it never counts as a model judgment.
    by_model = [s['name'] for s in stages if s['judged_by'] == '模型' and s['id'] != 'PACK' and s['done']]
    tail = '选区、打分、分级、路线由代码计算，每关结果都经过代码检查。'
    if not by_model:
        return '本次各关均按规则执行；' + tail
    return '由模型判断：' + '、'.join(f'「{n}」' for n in by_model) + '；' + tail


def render(tasks):
    """tasks: one build() dict or a list of them (newest first). The page lists them as task history."""
    tasks = tasks if isinstance(tasks, list) else [tasks]
    template = Path(__file__).with_name('view_template.html').read_text(encoding='utf-8')
    payload = json.dumps({'tasks': tasks}, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    return template.replace('/*__DATA__*/null', payload)


def write(directories, output, titles=None, created=None):
    """directories: a run dir or a list of run dirs; each becomes one task in the page."""
    directories = directories if isinstance(directories, (list, tuple)) else [directories]
    titles, created = titles or {}, created or {}
    tasks = [build(d, titles.get(str(d)), created.get(str(d))) for d in directories]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(tasks), encoding='utf-8')
    return output
