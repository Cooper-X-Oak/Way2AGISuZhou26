"""AMap v5 optional adapter. No business/contact fields are requested.
Docs: https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch
"""
import math
import os
from pathlib import Path
import uuid
import httpx
from functools import lru_cache
from pipeline.geo import CATEGORIES, distance
from pipeline.io import read, write, now

# Official code table, fetched 2026-10-24; Chinese leaf codes plus other dining groups.
TYPE_CODES = ('050119', '050117', '050118', '050101', '050100', '050200', '050300', '050400')
CODE_SOURCE = 'https://a.amap.com/lbs/static/code_resource/Amap_poicode.zip'


def wgs_to_gcj(lat, lon):
    """Mainland GCJ offset approximation; not survey-grade positioning."""
    if not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271): return lat, lon
    x, y = lon - 105.0, lat - 35.0
    dlat = -100 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*math.sqrt(abs(x))
    dlon = 300 + x + 2*y + 0.1*x*x + 0.1*x*y + 0.1*math.sqrt(abs(x))
    wave = (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
    dlat += wave + (20*math.sin(y*math.pi) + 40*math.sin(y*math.pi/3))*2/3
    dlat += (160*math.sin(y*math.pi/12) + 320*math.sin(y*math.pi/30))*2/3
    dlon += wave + (20*math.sin(x*math.pi) + 40*math.sin(x*math.pi/3))*2/3
    dlon += (150*math.sin(x*math.pi/12) + 300*math.sin(x*math.pi/30))*2/3
    rad = math.radians(lat)
    magic = 1 - 0.00669342162296594323*math.sin(rad)**2
    dlat = dlat*180/((6378245*(1-0.00669342162296594323)/(magic**1.5))*math.pi)
    dlon = dlon*180/(6378245/math.sqrt(magic)*math.cos(rad)*math.pi)
    return lat+dlat, lon+dlon


def gcj_to_wgs(lat, lon):
    result_lat, result_lon = lat, lon
    for _ in range(10):
        a,b = wgs_to_gcj(result_lat,result_lon)
        result_lat -= a-lat
        result_lon -= b-lon
    return result_lat,result_lon


# All limits are project/user constraints; 8 pages is a conservative capture ceiling.
import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

AREAS = {'shishan': '苏州高新区狮山路商圈', 'guanqian': '苏州姑苏区观前街', 'ligongdi': '苏州工业园区李公堤'}
SUBTYPES = {'050119': ['海鲜'], '050117': ['火锅'], '050101': ['酒楼'], '050300': ['简餐']}
MAPPING_NOTE = 'rules：映射配置及来源见params.amap_classification；高德type明确子类型及菜系别名优先，再类型码，再有来源的精确店名及菜系名称规则、档案名称规则；050118不直接映射烧烤；未知归其他餐饮；不推断营业时段或宴会能力。'


def load_key(root):
    """Read only the named dotenv value; never log secrets or modify the environment."""
    path = Path(root) / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            name, sep, value = line.strip().removeprefix('export ').partition('=')
            if sep and name.strip() == 'AMAP_KEY':
                value = value.strip()
                if value[:1] in ('"', "'"):
                    value = value[1:value.rfind(value[0])]
                else:
                    value = value.split(' #', 1)[0].strip()
                if value: return value
    key = os.environ.get('AMAP_KEY')
    if not key: raise RuntimeError('AMAP_KEY missing')
    return key


def basic_payload(payload):
    """Allowlist base fields: unsolicited contacts and extensions never reach disk."""
    result = {k: v for k, v in payload.items() if k in ('status', 'info', 'infocode', 'count')}
    if 'pois' in payload:
        fields = ('id', 'name', 'type', 'typecode', 'location', 'address', 'pname', 'cityname', 'adname', 'adcode', 'citycode', 'pcode', 'distance')
        result['pois'] = [{k: v for k, v in row.items() if k in fields and isinstance(v, (str, int, float, type(None)))} for row in payload['pois']]
    if 'geocodes' in payload:
        fields = ('formatted_address', 'country', 'province', 'city', 'citycode', 'district', 'street', 'number', 'adcode', 'location', 'level')
        result['geocodes'] = [{k: v for k, v in row.items() if k in fields and (isinstance(v, (str, type(None))) or v == [])} for row in payload['geocodes']]
    return result


def select_geocode(payload):
    matches = [g for g in payload.get('geocodes', []) if str(g.get('adcode', '')).startswith('3205')]
    if not matches: raise RuntimeError('Missing Suzhou geocode')
    preferred = [g for g in matches if g.get('level') == '兴趣点']
    if len(preferred) == 1: selected = preferred[0]
    elif len({(g.get('formatted_address'), g.get('level')) for g in matches}) == 1: selected = matches[0]
    else: raise RuntimeError('Ambiguous distinct Suzhou geocodes')
    lon, lat = map(float, selected['location'].split(','))
    if not (30.7 <= lat <= 32.1 and 119.9 <= lon <= 121.4): raise RuntimeError('Geocode outside Suzhou bbox')
    return selected, len(matches)


@lru_cache(maxsize=1)
def classification_config():
    from pipeline.profiles import parameters
    return parameters()['amap_classification']


class AmapAdapter:
    def __init__(self):
        self.mapping = classification_config()

    def subtypes(self, code, text):
        matched = [word for word in self.mapping['type_keywords'] if word in text]
        if not matched:
            matched = [value for word, value in self.mapping.get('subtype_aliases', {}).items() if word in text]
        return matched or ([self.mapping['typecode_subtypes'][code]] if code in self.mapping['typecode_subtypes'] else [])

    def normalize(self, payload, site, radius_m):
        # Legacy WGS84 site callers remain supported; new area snapshots use native GCJ-02.
        native = site.get('coordinate_system') == 'GCJ-02'
        result = {}
        for p in payload['pois']:
            code = str(p.get('typecode', ''))
            if code.startswith('0506'): cat = 'tea'
            elif code.startswith(('0501', '0502', '0503', '0504')): cat = 'restaurant'
            else: continue
            gcj_lon, gcj_lat = map(float, p['location'].split(','))
            lat, lon = (gcj_lat, gcj_lon) if native else gcj_to_wgs(gcj_lat, gcj_lon)
            d = distance(site['lat'], site['lon'], lat, lon)
            if d > radius_m: continue
            pid = 'amap:' + p['id']
            result[pid] = dict(id=pid, name=p.get('name') or None, category=cat, lat=lat, lon=lon,
                               tags={'typecode': code, 'type': p.get('type', '')}, distance_m=round(d, 2),
                               restaurant_types=self.subtypes(code, str(p.get('type', ''))), source='AMap',
                               coordinate_system='GCJ-02' if native else 'WGS84', source_location_gcj02=p['location'],
                               classification_method='rules', classification_source=self.mapping['source'])
        return sorted(result.values(), key=lambda p: p['id'])


@contextmanager
def usage_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / 'usage.lock'
    try: handle = lock.open('x')
    except FileExistsError: raise RuntimeError('AMap capture already running; usage lock exists') from None
    try:
        with handle:
            handle.write(str(os.getpid())); handle.flush()
            yield
    finally: lock.unlink()


class BudgetError(RuntimeError):
    """Safe budget-only diagnostic, never constructed from remote data."""


class Meter:
    def __init__(self, directory, budget, task_id, clock=time.monotonic, sleep=time.sleep, task_limit=400):
        if type(budget) is not int or not 0 <= budget <= 400: raise ValueError('AMap budget must be 0..400')
        self.path = directory / 'usage.json'
        self.data = read(self.path) if self.path.exists() else {'months': {}, 'tasks': {}, 'note': '本地请求尝试数（失败也计入），不含控制台其他调用；初始外部用量未知。', 'monthly_limit': 5000}
        self.budget, self.calls, self.task_id = budget, 0, task_id
        self.task_limit = task_limit
        self.clock, self.sleep, self.last = clock, sleep, None
        self.check_month()
        if self.data['tasks'].get(task_id, 0) + budget > task_limit: raise BudgetError(f'本任务搜索余额 {task_limit - self.data["tasks"].get(task_id, 0)} 小于本次预算 {budget}，拒绝运行（累计上限{task_limit}）')

    def month(self):
        return datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m')

    def check_month(self):
        remaining = 5000 - self.data['months'].get(self.month(), 0)
        if remaining < self.budget - self.calls: raise BudgetError(f'本月本地搜索余额 {remaining} 小于本次剩余预算 {self.budget - self.calls}，拒绝运行（月上限5000）')

    def before_request(self, search):
        if search:
            if self.calls >= self.budget: raise RuntimeError('AMap run budget exhausted')
            self.check_month()
            if self.data['tasks'].get(self.task_id, 0) >= self.task_limit: raise RuntimeError('AMap task cap reached')
        if self.last is not None:
            self.sleep(max(0, 0.4 - (self.clock() - self.last)))
        self.last = self.clock()
        if search:
            self.calls += 1
            month = self.month()
            self.data['months'][month] = self.data['months'].get(month, 0) + 1
            self.data['tasks'][self.task_id] = self.data['tasks'].get(self.task_id, 0) + 1
            self.data['updated_at'] = now()
            temporary = self.path.with_suffix('.json.tmp')
            write(temporary, self.data)
            temporary.replace(self.path)  # Durable count before dispatch; failed calls are not refunded.


def fetch_amap(root=Path('.'), client=None, type_codes=TYPE_CODES, max_calls=80, area_id=None, task_id='amap-fetch', clock=time.monotonic, sleep=time.sleep):
    root = Path(root)
    if area_id is not None and area_id not in AREAS: raise ValueError('Unknown area')
    key = load_key(root)
    raw = root / 'data/raw/amap'
    with usage_lock(raw):
        meter = Meter(raw, max_calls, task_id, clock, sleep)
        directory = raw / uuid.uuid4().hex
        directory.mkdir()
        manifest = dict(started_at=now(), status='running', area_id=area_id, type_codes=list(type_codes), type_source=CODE_SOURCE,
                        pages=[], requests=[], calls=0, budget=max_calls, params={'budget': {'amap_calls': max_calls}},
                        policy='基础响应白名单；丢弃全部联系方式与扩展字段；key打码；失败保留旧缓存', mapping=MAPPING_NOTE,
                        page_ceiling=8, page_ceiling_source='项目保守截断上限，每类型8页，每页25；不宣称全量')
        owned = client is None
        client = client or httpx.Client(timeout=45, trust_env=False)
        def request(endpoint, query, filename, search):
            meter.before_request(search)
            entry = dict(endpoint=endpoint, params={**query, 'key': '[REDACTED]'}, fetched_at=now(), path=filename, status='attempted')
            manifest['requests'].append(entry); manifest['calls'] = meter.calls
            write(directory / 'manifest.json', manifest)
            try:
                response = client.get('https://restapi.amap.com' + endpoint, params={**query, 'key': key})
                response.raise_for_status()
                original = response.json()
                if key in json.dumps(original, ensure_ascii=False): raise ValueError('Reflected credential')
                payload = basic_payload(original)
                write(directory / filename, payload)
                entry['status'] = 'success' if payload.get('status') == '1' else 'rejected'
                entry['infocode'] = payload.get('infocode')
                if payload.get('status') != '1': raise RuntimeError('AMap rejected request')
                return payload
            except Exception as exc:
                entry.update(status='failed', error_type=type(exc).__name__)
                raise RuntimeError('AMap request failed; see redacted manifest') from None
            finally:
                write(directory / 'manifest.json', manifest)
                with (directory / 'ledger.jsonl').open('a', encoding='utf-8') as log:
                    log.write(json.dumps(entry, ensure_ascii=False) + '\n')
        try:
            if area_id:
                payload = request('/v3/geocode/geo', {'address': AREAS[area_id], 'city': '苏州', 'output': 'JSON'}, 'geocode.json', False)
                geo, candidates = select_geocode(payload)
                lon, lat = map(float, geo['location'].split(','))
                site = dict(lat=lat, lon=lon, coordinate_system='GCJ-02', location_query=AREAS[area_id],
                            location_source='AMap geocode', location_evidence=str(directory / 'geocode.json'),
                            location_note='高德地理编码返回的商圈参考中心，非经销商门店；匹配级别：' + str(geo.get('level')),
                            display_name=geo.get('formatted_address'), site_note='商圈演示参考中心',
                            geocode_candidates=candidates, geocode_selection='唯一兴趣点优先于住宅区等结果；否则同名同级别按API排序取首项；项目参考中心规则，非精确场地定位')
            else: site = read(root / 'data/site.json')
            manifest['site'] = site
            lat, lon = (site['lat'], site['lon']) if site.get('coordinate_system') == 'GCJ-02' else wgs_to_gcj(site['lat'], site['lon'])
            rows = []; by_type = {}
            for code in type_codes:
                seen = set(); info = dict(calls=0, returned=0, truncated=False, reason=None)
                by_type[code] = info
                for page in range(1, 9):
                    if meter.calls >= max_calls:
                        info.update(truncated=True, reason='run_budget'); break
                    payload = request('/v5/place/around', {'location': f'{lon:.6f},{lat:.6f}', 'radius': 3000, 'types': code,
                                      'page_size': 25, 'page_num': page, 'sortrule': 'distance'}, f'{code}-{page}.json', True)
                    batch = payload.get('pois', [])
                    info['calls'] += 1; info['returned'] += len(batch)
                    manifest['pages'].append(dict(type_code=code, page=page, count=len(batch)))
                    if batch and not ({p['id'] for p in batch} - seen):
                        info.update(truncated=True, reason='repeated_page'); break
                    seen.update(p['id'] for p in batch); rows.extend(batch)
                    if len(batch) < 25: break
                    if page == 8: info.update(truncated=True, reason='page_ceiling')
            pois = AmapAdapter().normalize({'pois': rows}, site, 3000)
            fetched = now()
            for p in pois: p['fetched_at'] = fetched
            from pipeline.common import coverage_counts
            from pipeline.profiles import load, PROFILE_DIR
            truncated = any(v['truncated'] for v in by_type.values())
            coverage = dict(area_id=area_id, source='AMap', coordinate_system=site.get('coordinate_system', 'WGS84'),
                            site=site, radius_m=3000, fetched_at=fetched, amap_calls=meter.calls, amap_budget=max_calls,
                            truncated=truncated, budget_note='截断：详见type_coverage' if truncated else '未触及本地分页或调用上限',
                            type_coverage=by_type, total=len(pois), unnamed=sum(not p['name'] for p in pois),
                            manifest=str(directory / 'manifest.json'), categories={c: sum(p['category']==c for p in pois) for c in CATEGORIES},
                            channel_counts={p.stem: coverage_counts(pois, load(p.stem)) for p in PROFILE_DIR.glob('*.yaml')},
                            classification_note=MAPPING_NOTE, limitation='只覆盖所列餐饮类型可返回结果；所有业态数量均为样本计数，非普查；名称规则不能证明实际经营场景。')
            manifest.update(status='completed', finished_at=fetched, calls=meter.calls, poi_count=len(pois), type_coverage=by_type)
            write(directory / 'manifest.json', manifest)
            dest = root / 'data/areas' / area_id if area_id else root / 'data'
            write(dest / 'site.json', site); write(dest / 'pois.json', pois); write(dest / 'coverage.json', coverage)
            return dict(area_id=area_id, source='AMap', poi_count=len(pois), calls=meter.calls, truncated=truncated, manifest=str(directory / 'manifest.json'))
        except Exception as exc:
            manifest.update(status='failed', finished_at=now(), calls=meter.calls, error_type=type(exc).__name__, error='采集失败；旧缓存未替换')
            write(directory / 'manifest.json', manifest)
            raise RuntimeError('AMap capture failed; inspect redacted manifest') from None
        finally:
            if owned: client.close()
