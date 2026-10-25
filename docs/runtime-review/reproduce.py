"""Public baseline probe; no network calls, fabricated sources or silent overwrite."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-sources', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--area', choices=['shishan', 'guanqian', 'ligongdi'], default='shishan')
    parser.add_argument('--profile', choices=['jiangnan-gongquan', 'xinxian-huoquan'], default='jiangnan-gongquan')
    args = parser.parse_args()
    sources = Path(__file__).parent / 'sources'
    manifest = json.loads((sources / 'manifest.json').read_text(encoding='utf-8'))
    source_status = []
    for row in manifest['files']:
        payload = (sources / row['file']).read_bytes()
        if hashlib.sha256(payload).hexdigest() != row['sha256']:
            raise ValueError('Published source SHA-256 mismatch: ' + row['file'])
        destination = ROOT / '.work/handbook' / row['file']
        if args.prepare_sources and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open('xb') as stream:
                stream.write(payload)
        source_status.append(dict(file=row['file'], available=destination.is_file(),
                                  equals_public_copy=destination.is_file() and destination.read_bytes() == payload))
    from pipeline.__main__ import prepare_data
    from pipeline.common import coverage_counts
    from pipeline.profiles import load, parameters
    matrix = []
    # Current loader resolves cache paths relative to cwd; this is explicit.
    import os
    os.chdir(ROOT)
    for area in ('shishan', 'guanqian', 'ligongdi'):
        pois, coverage = prepare_data(parameters(), area)
        matrix.append(dict(area=area, total=len(pois), truncated=coverage.get('truncated'),
                           profile_counts={p:coverage_counts(pois,load(p)) for p in ('jiangnan-gongquan','xinxian-huoquan')}))
    print(json.dumps(dict(sources=source_status, area_matrix=matrix), ensure_ascii=False, indent=2), flush=True)
    if not args.run:
        return 0
    if not all(row['available'] for row in source_status):
        print('Missing source documents. Run --prepare-sources explicitly before --run.', file=sys.stderr)
        return 2
    report = ROOT / '.work/reports/public-runtime-repro'
    report.mkdir(parents=True, exist_ok=True)
    from datetime import datetime, timezone
    name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json'
    command = [sys.executable, '-m', 'pipeline', 'run', '--offline', '--auto-approve',
               '--area', args.area, '--profile', args.profile,
               '--input', f'{args.area}周边3公里，2个地推，做4周', '-o', str(report/name)]
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == '__main__':
    raise SystemExit(main())
