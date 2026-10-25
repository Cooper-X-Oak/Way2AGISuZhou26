"""Rerun pitches from immutable saved upstream decisions."""
import argparse
from pathlib import Path
from pipeline.io import read, write
from pipeline.judge import Judge
from . import run, inspect


def load_context(directory, judge, selection=None):
    directory = Path(directory)
    c = read(directory / 'inputs.json')
    for key, filename in [('brief', 'brief'), ('l1', 'channels'), ('l2', 'zones'),
                          ('l2c', 'competition'), ('l3', 'terminals'), ('l3p', 'partners')]:
        c[key] = read(directory / (filename + '.json'))
    # Discovery may have added merchants after the input snapshot.
    c['pois'] = read(directory / 'pois.json')
    c['params'] = read(directory / 'params.json')
    for key in ('demo_replay', 'previous', 'violations', 'run_directory'):
        c.pop(key, None)
    c.update(judge=judge, attempt=1)
    if selection is not None:
        c['pitch_targets'] = selection
    return c


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--target', action='append', help='Repeat to select multiple eligible targets')
    parser.add_argument('--output', required=True, type=Path, help='New directory; must not exist')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--offline', action='store_true')
    mode.add_argument('--live', action='store_true')
    args = parser.parse_args()
    c = load_context(args.run, Judge(offline=args.offline), args.target)
    from .context import targets
    targets(c)  # Validate scope before creating output.
    args.output.mkdir(parents=True, exist_ok=False)
    c['run_directory'] = str(args.output)
    write(args.output / 'source.json', {'run': str(args.run.resolve()), 'targets': args.target})
    artifact = run(c)
    report = inspect(artifact, c)
    write(args.output / 'materials.json', artifact)
    write(args.output / 'review.json', report)
    print(f"{c['judge'].label}: {len(artifact['items'])} targets; passed={report['passed']}; {args.output}")
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
