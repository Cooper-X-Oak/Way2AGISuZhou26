"""Inject selected offline data into vite-plugin-singlefile output; standard library only."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(scenarios=None, output=None):
    data_dir = ROOT / 'web' / 'data'
    index = json.loads((data_dir / 'index.json').read_text(encoding='utf-8'))
    ids = [s['id'] for s in index['scenarios']]
    selected = list(dict.fromkeys(scenarios or ids))
    if set(selected) - set(ids):
        raise ValueError('Unknown scenario selection')
    index['scenarios'] = [s for s in index['scenarios'] if s['id'] in selected]
    bundle = {'index.json': index}
    for scenario in selected:
        for path in sorted((data_dir / scenario).rglob('*.json')):
            bundle[path.relative_to(data_dir).as_posix()] = json.loads(path.read_text(encoding='utf-8'))
    html = (ROOT / 'dist' / 'single' / 'index.html').read_text(encoding='utf-8')
    payload = json.dumps(bundle, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    default = 'guanqian' if 'guanqian' in selected else selected[0]
    bootstrap = f'<script>window.__BUNDLE__={payload};if(!location.hash)location.hash="#/run/{default}";</script>'
    html = html.replace('<head>', '<head>' + bootstrap, 1)
    raw = html.encode('utf-8')
    if len(raw) > 20_000_000:
        raise ValueError(f'Bundle exceeds 20MB: {len(raw)} bytes')
    output = Path(output) if output else ROOT / 'dist' / '铺市流水线.html'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(raw)
    return output, len(raw)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', action='append')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    path, size = build(args.scenario, args.output)
    print(f'{path} ({size:,} bytes)')
