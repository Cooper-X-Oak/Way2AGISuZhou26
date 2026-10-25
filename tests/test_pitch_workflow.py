from copy import deepcopy
import pytest
from test_competition_partners import contexts
from pipeline import l4
from pipeline.io import read
from pipeline.workflows.pitch.research import prepare


def test_single_merchant_is_scoped_without_weakening_full_gate(contexts, tmp_path):
    original = contexts['L4']
    target = next(t for t in l4.targets(original) if t.startswith('test:'))
    scoped = {**original, 'pitch_targets': [target], 'run_directory': str(tmp_path)}
    artifact = l4.run(scoped)
    assert [r['target'] for r in artifact['items']] == [target]
    assert not l4.check(artifact, scoped)
    assert any(v.code == 'strategy_targets' for v in l4.check(artifact, original))
    folder = next((tmp_path / 'pitch').iterdir())
    assert read(folder / 'context.json')['research']['web_search_performed'] is False
    assert read(folder / 'review.json')['passed']
    assert read(folder / 'status.json')['judged_by'] == 'rules'
    assert 'pitch-writing' in read(folder / 'skills.json')


def test_invalid_target_rejected(contexts):
    for selection in ([], ['invented']):
        with pytest.raises(ValueError):
            l4.run({**contexts['L4'], 'pitch_targets': selection})


def test_model_payload_contains_merchant_context_and_skills(contexts):
    c = deepcopy(contexts['L4'])
    expected = l4.run(c)
    c['brief']['request'] = '只讨论试陈列'
    class CaptureJudge:
        label = 'rules'  # Test double; makes no real model call.
        def decide(self, stage, payload, rules):
            assert stage == 'L4'
            assert len(payload['research']['merchants']) == len(l4.targets(c))
            merchant = next(m for m in payload['research']['merchants'] if m['merchant'])
            assert merchant['merchant']['name'] == '测试火锅'
            assert merchant['terminal_decision']['poi_id'] == merchant['target']
            # One business skill at the repo root carries context gathering, writing and review guidance.
            assert set(payload['skill_instructions']) == {'pitch-writing'}
            assert '渠道兜底' in payload['skill_instructions']['pitch-writing']
            return {'decision': expected['decision'], 'items': expected['items']}
    c['judge'] = CaptureJudge()
    assert not l4.check(l4.run(c), c)


def test_failed_draft_retained(contexts, tmp_path):
    c = {**contexts['L4'], 'run_directory': str(tmp_path), 'demo_replay': 'compliance', 'attempt': 1}
    artifact = l4.run(c)
    assert l4.check(artifact, c)
    folder = next((tmp_path / 'pitch').iterdir())
    assert read(folder / 'status.json')['status'] == 'failed'
    assert read(folder / 'draft.json') == artifact


def test_research_does_not_invent_merchant_for_channel_fallback(contexts):
    c = deepcopy(contexts['L4'])
    c['l3']['items'] = []
    result = prepare(c)
    assert all(m['kind'] == 'channel_fallback' and m['merchant'] is None for m in result['merchants'])
