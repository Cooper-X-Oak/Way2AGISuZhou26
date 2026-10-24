"""Summaries contain candidate IDs and permitted facts, never raw POI dumps."""
from copy import deepcopy
from pathlib import Path

from pipeline.llm.schemas import decision_only

SKILLS = Path(__file__).resolve().parents[2] / 'skills'


def instruction(stage):
    return (SKILLS / stage.lower() / 'SKILL.md').read_text(encoding='utf-8')


def build_context(c, stage, payload):
    data = deepcopy(payload)
    if stage == 'L3':
        data['candidates'] = [dict(poi_id=r['poi']['id'],
                                   channel_id=r['channel']['channel_id'],
                                   zone_id=r['zone']['zone_id'],
                                   allowed_reason=r['allowed_reason'])
                              for r in payload['candidates']]
    elif stage == 'L3P':
        data['candidates'] = [dict(partner_id=r['id'], allowed_type=r['allowed_type'],
                                   allowed_reason=r['allowed_reason'])
                              for r in payload['candidates']]
    elif stage == 'L4':
        data.pop('terminals', None)
        data.pop('competition', None)
    # Product facts and compliance rules are necessary even for summary-only stages.
    data.setdefault('compliance_rules', c['profile']['compliance_rules'])
    data['profile_id'] = c['profile']['id']
    data['assumption_sources'] = c['params'].get('sources', {})
    if stage in ('L2C', 'L3P'):
        key = 'competition' if stage == 'L2C' else 'partners'
        data['assumption_sources'] = {key: c['params'][key]['source']}
        data['market_open'] = c.get('market', {}).get('open', [])
    data['previous'] = decision_only(stage, c.get('previous'))
    data['violations'] = c.get('violations', [])
    return data
