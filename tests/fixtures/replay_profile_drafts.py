"""Reproduce fictional mock extraction and offline CLI runs; no real credentials.

Run: uv run python tests/fixtures/replay_profile_drafts.py
This fixture sets L0's profile directory explicitly because its production
existence check is currently hardcoded to profiles/. No gates are replaced.
Reports/runs belong to the main repository's .work, including in worktrees.
"""
from functools import partial
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from test_profile_builder import example, MockGateway
from pipeline.profile_builder import report_directory, save_draft
from pipeline.profiles import PROFILE_DIR
from pipeline.engine import execute
from pipeline import l0
import pipeline.__main__ as cli
from pipeline.io import write


def main():
    # Delete inherited model settings without reading them. All extraction
    # responses are explicit test fixtures; --offline handles stage judgments.
    for key in ('LLM_API_KEY', 'LLM_BASE_URL', 'LLM_MODEL'):
        os.environ.pop(key, None)
    os.chdir(ROOT)
    reports = report_directory()
    temporary = reports.parents[1] / 'tmp' / 'profile-wizard-replay'
    l0.PROFILE_DIR = PROFILE_DIR / 'drafts'
    cli.execute = partial(execute, root=temporary)
    results = []
    for kind in ('tea', 'beer'):
        payload, raw = example(kind)
        path = PROFILE_DIR / 'drafts' / (payload['id'] + '.yaml')
        if not path.exists():
            result = save_draft(payload, gateway=MockGateway(raw), report_path=reports / (kind + '-draft.check.json'))
            if result['status'] != 'passed':
                return 2
        output = reports / (kind + '-offline.json')
        sys.argv = ['pipeline', 'run', '--offline', '--area', 'shishan', '--profile', 'drafts/' + payload['id'],
                    '--input', '半径3公里，2个地推，做4周', '--auto-approve', '-o', str(output)]
        code = cli.main()
        results.append(dict(product=kind, exit_code=code, summary=str(output), mock_extraction=True, live_model_calls=0))
    write(reports / 'replay.json', results)
    return max(r['exit_code'] for r in results)


if __name__ == '__main__':
    raise SystemExit(main())
