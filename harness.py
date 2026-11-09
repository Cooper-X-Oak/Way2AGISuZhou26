"""CLI for isolated agent workshop validation: uv run python -m harness."""
import argparse
import json
import os
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog='harness')
    sub = parser.add_subparsers(dest='command', required=True)
    test = sub.add_parser('stage-test')
    test.add_argument('--stage', required=True, choices=['L0', 'L1', 'L2', 'L2C', 'L3', 'L3P', 'L4', 'PACK'])
    test.add_argument('--from-run', required=True, type=Path)
    test.add_argument('--model', required=True)
    test.add_argument('--root', type=Path)
    test.add_argument('--token-budget', type=int)
    args = parser.parse_args(argv)
    os.environ['LLM_MODEL'] = args.model
    from runtime.client import Client
    from runtime.stage_test import stage_test, print_step
    budget = args.token_budget or {'L1': 30000, 'L2': 40000}.get(args.stage, 30000)
    if budget <= 0:
        parser.error('token budget must be positive')
    directory, state = stage_test(args.stage, args.from_run,
        root=args.root or args.from_run.resolve().parent.parent,
        client=Client(budget=budget), observer=print_step, token_budget=budget)
    print(json.dumps({'directory': str(directory), **state}, ensure_ascii=False), flush=True)
    return 0 if state['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
