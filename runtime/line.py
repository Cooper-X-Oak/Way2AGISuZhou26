"""Validate the declarative line against installed contracts, checks and tools."""
from pathlib import Path
import yaml
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from pipeline.contracts import Brief
from pipeline import v2_contracts
from pipeline.engine import STAGES
from pipeline.quality import LAYERS
from runtime.tools import Toolbox

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = {'pipeline.contracts.Brief': Brief, **{
    'pipeline.v2_contracts.' + name: getattr(v2_contracts, name)
    for name in ('Channels', 'Zones', 'Competition', 'Terminals', 'Partners', 'Materials', 'Pack')}}
CHECKS = {f'pipeline.{module.__name__.split(".")[-1]}.inspect': module.inspect for _, module in STAGES}


class Stage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    sop: str = Field(min_length=1)
    inputs: list[str]
    output_contract: str
    skills_allowed: list[str]
    tools_allowed: list[str]
    checks: str
    layers: list[str]
    gate: Literal['none', 'human']
    max_steps: int = Field(default=16, ge=1, le=128)
    max_attempts: int = Field(default=3, ge=1, le=3)
    token_budget: int = Field(default=150_000, ge=1)


def skills_catalog(root=ROOT / 'skills'):
    catalog = {}
    for path in sorted(Path(root).glob('*/SKILL.md')):
        text = path.read_text(encoding='utf-8')
        if not text.startswith('---\n'): continue
        metadata = yaml.safe_load(text.split('---', 2)[1])
        if 'when_to_use' not in metadata: continue  # #7 legacy prompts remain usable by rules/llm.
        if not all(isinstance(metadata.get(k), str) and metadata[k].strip() for k in ('name', 'description', 'when_to_use')):
            raise ValueError('invalid_skill_metadata')
        name = metadata['name']
        if name != path.parent.name or name in catalog: raise ValueError('invalid_skill_name')
        catalog[name] = {**metadata, 'path': path}
    return catalog


def load_line(path=ROOT / 'production/line.yaml', *, skills_root=ROOT / 'skills'):
    stages = [Stage.model_validate(row) for row in yaml.safe_load(Path(path).read_text(encoding='utf-8'))['stages']]
    if [s.name for s in stages] != [s for s, _ in STAGES]: raise ValueError('invalid_stage_order')
    catalog = skills_catalog(skills_root)
    seen = {'brief'}
    for stage in stages:
        if stage.output_contract not in CONTRACTS or stage.checks not in CHECKS: raise ValueError('unknown_contract_or_check')
        if not set(stage.inputs) <= seen: raise ValueError('input_not_upstream')
        if not set(stage.skills_allowed) <= catalog.keys(): raise ValueError('unknown_skill')
        if not set(stage.tools_allowed) <= Toolbox.names(): raise ValueError('unknown_tool')
        if not {'submit_artifact', 'load_skill'} <= set(stage.tools_allowed): raise ValueError('missing_agent_tools')
        if stage.layers != list(LAYERS): raise ValueError('invalid_check_layers')
        if (stage.gate == 'human') != (stage.name in ('L2', 'L3')): raise ValueError('invalid_gate')
        seen.add(stage.name)
    return stages, catalog
