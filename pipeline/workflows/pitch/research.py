"""Prepare source-labelled merchant context; no implied network research."""
from pathlib import Path
from pipeline.common import task_context
from .context import targets, angles


SKILL = Path(__file__).resolve().parents[3] / 'skills' / 'pitch-writing' / 'SKILL.md'


def instructions():
    """The business method for 怎么说 lives in one skill file at the repo root (skills/pitch-writing/SKILL.md)."""
    return {'pitch-writing': SKILL.read_text(encoding='utf-8')}


def prepare(c):
    pois = {p['id']: p for p in c['pois']}
    terminals = {p['poi_id']: p for p in c['l3']['items']}
    merchants = []
    for target, (channel, strategy) in targets(c).items():
        poi = pois.get(target)
        merchants.append({
            'target': target,
            'kind': 'merchant' if target in terminals else 'channel_fallback',
            'merchant': poi,
            'terminal_decision': terminals.get(target),
            'channel': channel,
            'strategy': strategy,
            'required_angle': angles(c)[strategy],
            'evidence': ['L1:' + channel['channel_id']] +
                        (['poi:' + target, 'L3:' + target] if poi and target in terminals else []),
            'unknowns': ['未核实店内在售产品、采购条件及当前需求；沟通时确认。'],
        })
    return {'task_context': task_context(c, 'L4'), 'product': c['profile'],
            'merchants': merchants,
            'research': {'mode': 'existing_data', 'web_search_performed': False,
                         'sources': ['profile', 'pois', 'L1', 'L2C', 'L3'],
                         'limitation': '本步骤只整理已有资料，尚未接入网页搜索。'}}
