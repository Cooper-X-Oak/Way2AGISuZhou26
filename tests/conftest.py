"""Isolate each invocation in the shared harness; never delete another run's temp tree."""
from pathlib import Path
import uuid


def pytest_configure(config):
    if not config.option.basetemp:
        parent = Path(__file__).resolve().parents[1] / '.work/tmp'
        parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(parent / ('pytest-v2-' + uuid.uuid4().hex))
