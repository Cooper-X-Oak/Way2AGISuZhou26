"""`pipeline demo` 端到端：假模型 + 假高德 HTTP 客户端（测试替身，非真实商户）。

覆盖实况路径（search_merchants 走真实请求代码，只替换 HTTP 客户端）、
录制、以及用录制回放再跑一遍。不证明真实模型或真实高德可用。
"""
import json
import math
from pathlib import Path

import pytest

from pipeline import demo

ROOT = Path(__file__).resolve().parents[1]
KEY = 'TEST-ONLY-KEY-never-written'


def site():
    return json.loads((ROOT / 'data/areas/shishan/site.json').read_text(encoding='utf-8'))


def poi(i, name, typecode, type_text, dx, dy):
    s = site()
    lat = s['lat'] + dy / 111_000
    lon = s['lon'] + dx / (111_000 * math.cos(math.radians(s['lat'])))
    return {'id': f'TESTFAKE{i:04d}', 'name': name, 'type': type_text, 'typecode': typecode, 'location': f'{lon:.6f},{lat:.6f}', 'tel': '000'}


DATA = {
    '写字楼': [poi(i, f'测试替身大厦{i}', '120201', '商务住宅;楼宇;商务写字楼', 40 * (i % 5) + 20, 40 * (i // 5) + 20) for i in range(15)],
    '健身房': [poi(100 + i, f'测试替身健身{i}', '080111', '体育休闲服务;运动场馆;健身中心', 50 * (i % 4) + 30, 50 * (i // 4) + 30) for i in range(8)],
    '茶馆': [poi(200 + i, f'测试替身茶室{i}', '050600', '餐饮服务;茶艺馆;茶艺馆', 35 * i + 25, 60) for i in range(5)],
}


class FakeAmap:
    """Stands in for httpx.Client; asserts the request shape the real API expects."""

    def __init__(self):
        self.requests = []

    def get(self, url, params):
        assert url == 'https://restapi.amap.com/v5/place/around' and params['key'] == KEY
        assert params['radius'] == 3000 and params['page_size'] == 25
        self.requests.append(params)
        rows = DATA.get(params.get('keywords'), []) if params['page_num'] == 1 else []
        body = {'status': '1', 'info': 'OK', 'infocode': '10000', 'count': str(len(rows)), 'pois': rows}

        class Response:
            def raise_for_status(self): pass
            def json(self): return body
        return Response()


class Model:
    model = 'scripted-test-model'

    def __init__(self):
        self.plan = [('area_inventory', {}), ('search_merchants', {'keywords': '写字楼'}), ('search_merchants', {'keywords': '健身房'}),
                     ('search_merchants', {'keywords': '茶馆'}), ('area_inventory', {})]

    def __call__(self, payload):
        if self.plan:
            name, args = self.plan.pop(0)
            return {'choices': [{'message': {'role': 'assistant', 'content': None,
                                             'tool_calls': [{'id': f'c{len(self.plan)}', 'type': 'function',
                                                             'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}}]}
        inv = next(json.loads(m['content']) for m in reversed(payload['messages']) if m['role'] == 'tool' and '"radius_m"' in m['content'])
        final = {'decision': '补采写字楼、健身房、茶馆后三个渠道均有候选。', 'confidence': 0.8,
                 'items': [{'channel_id': k, 'candidate_count': v, 'status': 'acquired' if v else 'insufficient_after_search',
                            'evidence': [], 'reason': '高德周边搜索补采'} for k, v in inv['by_channel'].items()]}
        return {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(final, ensure_ascii=False)}}]}


@pytest.fixture(autouse=True)
def at_root(monkeypatch):
    monkeypatch.chdir(ROOT)


def test_live_then_replay(tmp_path, capsys):
    rec, web = tmp_path / 'rec.json', tmp_path / 'web' / 'xinxian-agent'
    amap = FakeAmap()
    code = demo.main('live', root=tmp_path, recording=rec, web=web, check=lambda: [],
                     inject={'llm_post': Model(), 'amap_client': amap, 'amap_key': KEY})
    out = capsys.readouterr().out
    assert code == 0, out
    assert '停在 L2' in out                       # rules baseline cannot proceed: no tea/office/gym in cache
    assert [r['keywords'] for r in amap.requests] == ['写字楼', '健身房', '茶馆']
    assert rec.is_file() and (web / 'summary.json').is_file()
    runs = sorted((tmp_path / 'runs').iterdir())
    for path in runs[-1].rglob('*'):
        if path.is_file():
            assert KEY not in path.read_text(encoding='utf-8', errors='ignore')
    report = next((tmp_path / '.work/reports').glob('demo-*.md')).read_text(encoding='utf-8')
    assert '（Agent 补采）' in report and '实况' in report

    code = demo.main('replay', root=tmp_path, recording=rec, web=None, check=lambda: ['LLM_API_KEY'])
    out = capsys.readouterr().out
    assert code == 0, out
    from pipeline.io import read
    l1d = read(sorted((tmp_path / 'runs').iterdir())[-1] / 'L1D.json')
    assert '模式：回放' in out and l1d['agent']['model'] == 'replay:scripted-test-model' and l1d['agent']['amap_calls'] == 0


def test_missing_everything_explains_what_to_set(tmp_path, capsys):
    code = demo.main('auto', root=tmp_path, recording=tmp_path / 'none.json', web=None, check=lambda: ['LLM_API_KEY', 'AMAP_KEY'])
    out = capsys.readouterr().out
    assert code == 2 and 'LLM_API_KEY' in out and '也没有回放录制' in out
