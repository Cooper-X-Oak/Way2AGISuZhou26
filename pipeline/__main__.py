import argparse
import json
import os
from pathlib import Path
from pipeline.engine import execute
from pipeline.io import read, write
from pipeline.judge import Judge
from pipeline.profiles import load, parameters, PROFILE_DIR
from pipeline.common import coverage_counts
from pipeline.sources import fetch


def prepare_data(params=None, area=None):
    """Runs consume snapshots only; fetching is an explicit, metered command."""
    from copy import deepcopy
    from pipeline.sources import OSMAdapter
    params = params or parameters()
    if area:
        from pipeline.amap import AREAS
        if area not in AREAS: raise ValueError('Unknown area')
        directory = Path('data/areas') / area
        pois, coverage = read(directory/'pois.json'), read(directory/'coverage.json')
        if coverage['coordinate_system'] != 'GCJ-02' or any(p['coordinate_system'] != 'GCJ-02' for p in pois):
            raise ValueError('Area coordinate mismatch')
        return pois, coverage
    site = read('data/site.json')
    pois = OSMAdapter().normalize(read('data/raw/overpass.json'), site, 3000)
    coverage = deepcopy(read('data/coverage.json'))
    coverage.update(source='OpenStreetMap', fetched_at=read('data/raw/provenance.json')['fetched_at'],
                    amap_calls=0, amap_budget=params['budget']['amap_calls'], truncated=False, source_order=['OpenStreetMap'])
    coverage['total'] = len(pois)
    coverage['channel_counts'] = {p.stem: coverage_counts(pois, load(p.stem)) for p in PROFILE_DIR.glob('*.yaml')}
    return pois, coverage


def load_replay(path):
    """Recorded AMap pages: {"pages": [{"keywords"|"types": ..., "page": n, "payload": {...}}]}."""
    from pipeline.amap import basic_payload
    data = read(path)
    return {json.dumps({k: row[k] for k in ('keywords', 'types') if row.get(k)} | {'page': row.get('page', 1)}, sort_keys=True, ensure_ascii=False):
            basic_payload(row['payload']) for row in data['pages']}


def main():
    parser = argparse.ArgumentParser(description='快消新品渠道进入 Agent L0-L4 + PACK')
    sub = parser.add_subparsers(dest='command', required=True)
    export = sub.add_parser('export', help='导出离线网页演示数据')
    export.add_argument('--run', type=Path, required=True)
    export.add_argument('--out', type=Path, required=True)
    sub.add_parser('fetch', help='显式刷新OSM缓存')
    market=sub.add_parser('fetch-market',help='三个商圈竞品和渠道伙伴；累计150搜索调用')
    market.add_argument('--amap-calls',type=int,default=150)
    market.add_argument('-o','--output',type=Path)
    supplement = sub.add_parser('supplement-amap', help='四格补抓三个商圈截断类型；grading-fix累计150次')
    supplement.add_argument('--amap-calls', type=int, default=150)
    supplement.add_argument('-o', '--output', type=Path)
    from pipeline.amap import AREAS
    amap = sub.add_parser('fetch-amap', help='直连高德并写入商圈缓存')
    amap.add_argument('--area', choices=list(AREAS), required=True)
    amap.add_argument('--amap-calls', type=int, default=parameters()['budget']['amap_calls'])
    amap.add_argument('-o', '--output', type=Path)
    run = sub.add_parser('run')
    run.add_argument('--offline', action='store_true')
    run.add_argument('--area', choices=list(AREAS), help='读取商圈缓存；不调用高德')
    run.add_argument('--input', required=True)
    run.add_argument('--profile', default='jiangnan-gongquan')
    run.add_argument('--demo-replay', choices=['compliance','backtrack'])
    run.add_argument('--auto-approve', action='store_true')
    run.add_argument('--agent-l1d', action='store_true', help='L1D 终端发现由 Agent 执行（真实模型+高德补采），其余关卡按 --offline 规则')
    run.add_argument('--amap-live', action='store_true', help='有录制数据时，未命中的查询仍实时调用高德（需 AMAP_KEY）')
    run.add_argument('--amap-replay', type=Path, help='高德录制数据 JSON（见 docs/demo），命中则回放、不消耗额度')
    run.add_argument('--no-defaults', '--no-demo-profile', action='store_true', dest='no_defaults')
    run.add_argument('-o', '--output', type=Path, help='写JSON执行摘要')
    args = parser.parse_args()
    try:
        if args.command == 'export':
            from pipeline.export import export_run
            print(json.dumps(export_run(args.run, args.out), ensure_ascii=False))
            return 0
        if args.command == 'fetch-market':
            from pipeline.market_capture import capture
            result=capture(max_calls=args.amap_calls)
            if args.output:write(args.output,result)
            print(json.dumps({'calls':result['calls'],'manifest':result['manifest']},ensure_ascii=False))
            return 0
        if args.command == 'supplement-amap':
            from pipeline.amap_supplement import supplement
            result=supplement(max_calls=args.amap_calls)
            if args.output:write(args.output,result)
            print(json.dumps(result,ensure_ascii=False))
            return 0
        if args.command == 'fetch-amap':
            from pipeline.amap import fetch_amap
            result = fetch_amap(area_id=args.area, max_calls=args.amap_calls)
            if args.output: write(args.output, result)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == 'fetch':
            fetch()
            pois, coverage = prepare_data(parameters())
            write('data/pois.json', pois)
            write('data/coverage.json', coverage)
            print(json.dumps({'source':coverage['source'],'poi_count':len(pois)},ensure_ascii=False))
            return 0
        judge = Judge(args.offline)
        profile = load(args.profile)
        params = parameters()
        pois, coverage = prepare_data(params, args.area)
        from pipeline.market_capture import load_market
        inputs = {'request': args.input, 'site': coverage['site'], 'area_id': args.area, 'pois': pois, 'coverage': coverage,
                  'params': params, 'judge': judge, 'profile': profile,
                  'demo_replay': args.demo_replay, 'auto_approve': args.auto_approve, 'no_defaults': args.no_defaults,
                  'agent_l1d': args.agent_l1d, 'agent_echo': args.agent_l1d, 'amap_live': args.amap_live}
        if args.amap_replay: inputs['amap_replay'] = load_replay(args.amap_replay)
        inputs['market']=load_market(args.area)
        directory, state = execute(inputs)
        result = {'run_directory': str(directory), **state}
        if args.output: write(args.output, result)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if state['status'] == 'completed' else 2
    except Exception as exc:
        # Source/client exception text may contain request URLs and environment keys.
        result = {'status': 'error', 'error_type': type(exc).__name__, 'error': '执行失败；检查档案、数据与环境配置。未回退或生成替代结果。'}
        from pipeline.amap import BudgetError
        if isinstance(exc, BudgetError): result['error'] = str(exc)
        if getattr(args, 'output', None): write(args.output, result)
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
