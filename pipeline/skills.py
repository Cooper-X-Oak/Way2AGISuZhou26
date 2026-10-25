"""Business skills live in skills/<name>/SKILL.md (same layout as the 拓哥 hermes profile).

Front matter: name, description, stage (L0…L4, or null for protocol skills). Body: the business method.
Code owns contracts and checks; skills own judgment guidance, so the business can be refined in Markdown.
"""
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / 'skills'
RUNTIME_DIR = ROOT / 'runtime'


def _split(text):
    if text.startswith('---'):
        _, head, body = text.split('---', 2)
        return yaml.safe_load(head) or {}, body.strip()
    return {}, text.strip()


@lru_cache(maxsize=None)
def catalog():
    """name -> {name, description, stage, body}, sorted by name."""
    out = {}
    for path in sorted(SKILL_DIR.glob('*/SKILL.md')):
        meta, body = _split(path.read_text(encoding='utf-8'))
        name = str(meta.get('name') or path.parent.name)
        out[name] = dict(name=name, description=str(meta.get('description') or ''), stage=meta.get('stage'), body=body)
    return out


def listing():
    return [dict(name=s['name'], description=s['description'], stage=s['stage']) for s in catalog().values()]


def load(name):
    skill = catalog().get(name)
    return skill['body'] if skill else None


def for_stage(stage):
    """The business skill a stage's judgment follows (None when the stage is pure code)."""
    return next((s for s in catalog().values() if s['stage'] == stage), None)


def runtime_doc(name):
    return (RUNTIME_DIR / name).read_text(encoding='utf-8').strip()
