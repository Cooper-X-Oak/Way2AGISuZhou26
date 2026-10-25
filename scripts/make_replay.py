"""Build an --amap-replay file from a finished run (live AMap pages saved by L1D).

    python scripts/make_replay.py runs/<run_id> -o docs/demo/amap-replay-shishan.json

Only allow-listed base fields are kept (pipeline.amap.basic_payload); keys never appear in run files.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.amap import basic_payload  # noqa: E402
from pipeline.io import write  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('-o', '--output', type=Path, required=True)
    args = parser.parse_args()
    pages = {}
    for path in sorted((args.run / 'agent' / 'amap').glob('*.json')):
        row = json.loads(path.read_text(encoding='utf-8'))
        key = json.dumps({**row['query'], 'page': row['page']}, sort_keys=True, ensure_ascii=False)
        pages[key] = {**row['query'], 'page': row['page'], 'payload': basic_payload(row['payload'])}
    if not pages:
        raise SystemExit('该 run 没有保存任何高德补采页面（agent/amap/*.json）')
    write(args.output, {'source': f'recorded from {args.run.name}', 'note': '高德 /v5/place/around 基础字段白名单；仅供演示回放',
                        'pages': list(pages.values())})
    print(f'{len(pages)} 页 → {args.output}')


if __name__ == '__main__':
    main()
