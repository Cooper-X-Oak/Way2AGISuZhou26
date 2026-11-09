"""P2 control-plane acceptance tests; all inputs are synthetic test fixtures."""

import pytest
from pydantic import ValidationError

from contracts.harness import (
    ChannelEntryBrief, GateResult, NodeOutcome, ProductionGraph, ProductionState,
)
from harness.clarity import approve, assess, request_input, require_approved, supersede
from harness.state_machine import TransitionError, advance, initialize, resume, start
from production.graph import PRODUCTION_GRAPH, compose


def brief(profile="restaurant_entry", **overrides):
    data = dict(project_id="test-project", brief_id="test-brief", decision_question="测试决策",
                product_profile_version="test-profile/v1",
                target_scope=dict(city="测试城市", territories=("测试商圈",), radius_km=2,
                                  channel_types=("测试渠道",)),
                resources=dict(salespeople=2, period_days=10, daily_first_visits=3),
                entry_modes=("direct",), exclusions=(), compliance_constraints=("不抓个人联系方式",),
                delivery_profile=profile, assumptions_and_unknowns=(), stop_conditions=("测试预算耗尽",))
    return ChannelEntryBrief(**(data | overrides))


def approved(profile="restaurant_entry", **overrides):
    return approve(request_input(brief(profile, **overrides)), approved_by="test-manager")


def running(profile="restaurant_entry"):
    b = approved(profile)
    return start(initialize(b, compose(b)))


def event(state, kind, **kwargs):
    return NodeOutcome(node_id=state.current_node, outcome=kind, **kwargs)


def submit(state):
    return advance(state, event(state, "wait", wait_for="gate", reason="submitted"))


def passed(state, next_node=None):
    gate = GateResult(gate_id=state.graph.node(state.current_node).quality_gate, outcome="pass")
    return advance(state, event(state, "pass", gate=gate), next_node)


@pytest.mark.parametrize("status", ["draft", "needs_user_input"])
def test_unapproved_cannot_start(status):
    b = brief(status=status)
    with pytest.raises(ValueError):
        require_approved(b)
    with pytest.raises(ValueError):
        compose(b)
    with pytest.raises(ValueError):
        initialize(b, compose(approved()))


def test_clarity_gaps_and_lifecycle():
    b = ChannelEntryBrief(project_id="test", brief_id="test")
    result = assess(b)
    assert result.state == "needs_user_input"
    assert {"decision_question", "target_scope.city", "resources.salespeople", "exclusions"} <= set(result.missing_fields)
    with pytest.raises(ValueError, match="missing fields"):
        approve(request_input(b), approved_by="manager")
    assert assess(brief()).state == "draft"
    with pytest.raises(ValueError):
        approve(brief(), approved_by="manager")
    assert assess(approved()).can_start
    with pytest.raises(ValidationError):
        approve(request_input(brief()), approved_by=" ")


def test_superseded_and_stale_snapshots_rejected():
    old = approved()
    replacement = brief(version=2, supersedes=1)
    retired, new = supersede(old, replacement)
    assert assess(retired).state == "superseded"
    for b in (retired, new):
        with pytest.raises(ValueError):
            require_approved(b)
    with pytest.raises(ValueError):
        require_approved(old, latest=retired)
    with pytest.raises(ValueError):
        compose(old, latest=new)
    with pytest.raises(ValueError):
        approve(retired, approved_by="manager")
    with pytest.raises(ValueError):
        supersede(old, brief(version=2, supersedes=1, project_id="other"))
    require_approved(approve(request_input(new), approved_by="manager"))


@pytest.mark.parametrize("profile,ids", [
    ("restaurant_entry", ("scope_clarity", "scene_channel", "territory", "competition", "terminal", "distribution", "outreach", "delivery")),
    ("distributor_recruitment", ("scope_clarity", "territory", "distribution", "outreach", "delivery")),
    ("competition_inspection", ("scope_clarity", "competition", "delivery")),
])
def test_scenario_subgraphs_and_end_to_end(profile, ids):
    b = approved(profile)
    composition = compose(b)
    assert tuple(n.node_id for n in composition.graph.nodes) == ids
    assert composition.graph.nodes[-1].output_contracts == composition.deliverables
    state = start(initialize(b, composition))
    for index, node in enumerate(composition.graph.nodes):
        assert state.current_node == node.node_id
        state = submit(state)
        following = ids[index + 1] if index + 1 < len(ids) else None
        if node.human_review:
            state = passed(state)
            assert state.status == "waiting_human" and state.artifact_state == "frozen"
            state = advance(state, event(state, "pass", approved_by="manager"), following)
        else:
            state = passed(state, following)
    assert state.status == "completed"
    assert state.artifact_state == "frozen"
    assert ProductionState.model_validate_json(state.model_dump_json()) == state


def test_all_nine_workshops_and_references():
    assert len(PRODUCTION_GRAPH.nodes) == 9
    assert PRODUCTION_GRAPH.node("field_feedback").handoff_options == ("territory", "terminal")
    for field in ("handoff_options", "rework_node"):
        data = PRODUCTION_GRAPH.model_dump()
        data["nodes"][0][field] = ("missing",) if field == "handoff_options" else "missing"
        with pytest.raises(ValidationError, match="reference"):
            ProductionGraph.model_validate(data)
    with pytest.raises(ValidationError, match="duplicate"):
        ProductionGraph(entry_node="scope_clarity", nodes=PRODUCTION_GRAPH.nodes * 2)


def test_strict_frozen_contracts_and_nested_data():
    b = brief()
    with pytest.raises(ValidationError):
        b.version = 3
    with pytest.raises(ValidationError):
        b.resources.salespeople = 5
    with pytest.raises(ValidationError):
        brief(unexpected="no")
    with pytest.raises(ValidationError):
        brief(resources={"salespeople": True})
    with pytest.raises(ValidationError):
        brief(target_scope={"radius_km": float("inf")})
    with pytest.raises(ValidationError):
        brief(status="approved")
    with pytest.raises(ValidationError):
        brief(assumptions_and_unknowns=({"statement": "guess", "kind": "assumption"},))


def test_undeclared_handoff_and_failure_ledger_retained_on_retry():
    state = submit(running())
    with pytest.raises(TransitionError, match="undeclared") as failure:
        passed(state, "delivery")
    failed = failure.value.state
    assert failed.current_node == state.current_node
    assert failed.ledger[-1].accepted is False
    assert failed.ledger[:-1] == state.ledger
    retried = passed(failed, "scene_channel")
    assert retried.ledger[:-1] == failed.ledger


def test_rework_returns_declared_workshop():
    state = passed(submit(running()), "scene_channel")
    state = passed(submit(state), "territory")
    state = submit(state)
    with pytest.raises(TransitionError, match="declared"):
        advance(state, event(state, "rework", reason="missing evidence"), "delivery")
    result = advance(state, event(state, "rework", reason="bad coverage"))
    assert result.current_node == "scene_channel" and result.status == "running"
    assert result.ledger[-1].outcome.reason == "bad coverage"


def test_wait_resume_stays_same_node():
    state = running()
    waiting = advance(state, event(state, "wait", wait_for="data", reason="await capture"))
    assert waiting.status == "waiting_gate"
    resumed = resume(waiting, reason="capture arrived")
    assert resumed.status == "running" and resumed.current_node == state.current_node
    with pytest.raises(TransitionError):
        resume(submit(state), reason="attempt bypass")
    with pytest.raises(TransitionError):
        passed(waiting, "scene_channel")


def test_human_gate_cannot_be_bypassed():
    state = passed(submit(running("distributor_recruitment")), "territory")
    state = submit(state)
    with pytest.raises(TransitionError):
        passed(state, "distribution")
    waiting = passed(state)
    with pytest.raises(TransitionError):
        advance(waiting, event(waiting, "pass"), "distribution")
    with pytest.raises(TransitionError):
        resume(waiting, reason="skip human")
    result = advance(waiting, event(waiting, "pass", approved_by="manager"), "distribution")
    assert result.status == "running" and result.current_node == "distribution"


@pytest.mark.parametrize("gate_outcome", ["needs_evidence", "rework", "quarantine", "block"])
def test_nonpassing_gates_never_freeze_or_handoff(gate_outcome):
    state = submit(running())
    gate = GateResult(gate_id="G0_BRIEF", outcome=gate_outcome, reasons=("test issue",))
    if gate_outcome in ("rework", "needs_evidence"):
        with pytest.raises(TransitionError):
            advance(state, event(state, "pass", gate=gate), "scene_channel")
        result = advance(state, event(state, "rework", reason="test issue", gate=gate))
        assert result.status == "running"
    else:
        result = advance(state, event(state, "pass", gate=gate))
        assert result.status == ("blocked" if gate_outcome == "block" else "waiting_gate")
    assert result.artifact_state not in ("frozen", "approved")
    assert result.current_node == "scope_clarity"


def test_no_premature_completion_wrong_node_or_gate():
    state = running()
    with pytest.raises(TransitionError):
        advance(state, event(state, "complete"))
    waiting = submit(state)
    with pytest.raises(TransitionError):
        advance(waiting, NodeOutcome(node_id="delivery", outcome="pass"))
    with pytest.raises(TransitionError):
        advance(waiting, event(waiting, "pass", gate=GateResult(gate_id="G7_DELIVERY", outcome="pass")))
    with pytest.raises(TransitionError):
        advance(waiting, event(waiting, "complete", gate=GateResult(gate_id="G0_BRIEF", outcome="pass")))


def test_block_stops_execution_and_preserves_reason():
    state = running()
    result = advance(state, event(state, "block", reason="budget exhausted"))
    assert result.status == "blocked"
    assert result.ledger[-1].outcome.reason == "budget exhausted"
    with pytest.raises(TransitionError):
        advance(result, event(result, "wait", wait_for="data", reason="retry"))
    with pytest.raises(TransitionError):
        start(result)


def test_cross_project_and_version_initialization_rejected():
    b = approved()
    composition = compose(b)
    for other in (approved(project_id="other"), approved(version=2, supersedes=1)):
        with pytest.raises(ValueError):
            initialize(other, composition)
