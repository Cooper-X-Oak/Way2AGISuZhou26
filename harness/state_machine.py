"""Pure, auditable production transitions; persistence belongs to the caller.

Invalid transitions raise TransitionError carrying a state with an appended
rejection ledger entry. Persist that state before retrying to retain failures.
"""

from contracts.harness import ChannelEntryBrief, NodeOutcome, ProductionState, ScenarioComposition, Transition
from harness.clarity import require_approved


class TransitionError(ValueError):
    def __init__(self, message: str, state: ProductionState):
        super().__init__(message)
        self.state = state


def _record(state, outcome, *, accepted=True, rejection_reason=None, **changes):
    entry = Transition(sequence=len(state.ledger) + 1, from_status=state.status,
                       to_status=changes.get("status", state.status), from_node=state.current_node,
                       to_node=changes.get("current_node", state.current_node), outcome=outcome,
                       accepted=accepted, rejection_reason=rejection_reason)
    return ProductionState.model_validate({**state.model_dump(), **changes,
                                           "ledger": (*state.ledger, entry)})


def _reject(state, outcome, message):
    raise TransitionError(message, _record(state, outcome, accepted=False, rejection_reason=message))


def initialize(brief: ChannelEntryBrief, composition: ScenarioComposition, *, latest=None) -> ProductionState:
    require_approved(brief, latest=latest)
    if (brief.project_id, brief.brief_id, brief.version, brief.delivery_profile) != (
        composition.project_id, composition.brief_id, composition.brief_version, composition.delivery_profile
    ):
        raise ValueError("composition does not match approved brief")
    return ProductionState(project_id=brief.project_id, brief_id=brief.brief_id,
                           brief_version=brief.version, graph=composition.graph,
                           current_node=composition.graph.entry_node)


def start(state: ProductionState) -> ProductionState:
    event = NodeOutcome(node_id=state.current_node, outcome="pass", reason="start approved scenario")
    if state.status != "ready":
        _reject(state, event, "start requires ready state")
    return _record(state, event, status="running")


def resume(state: ProductionState, *, reason: str) -> ProductionState:
    """Resume a data wait at the same node; human/gate waits require advance."""
    event = NodeOutcome(node_id=state.current_node, outcome="wait", reason=reason, wait_for="data")
    if state.status != "waiting_gate" or state.wait_for != "data":
        _reject(state, event, "only data waits can resume without a gate or human approval")
    return _record(state, event, status="running", wait_for=None)


def advance(state: ProductionState, outcome: NodeOutcome, next_node: str | None = None) -> ProductionState:
    node = state.graph.node(state.current_node)
    if outcome.node_id != state.current_node:
        _reject(state, outcome, "outcome belongs to another node")
    if outcome.outcome == "block":
        if not outcome.reason or next_node is not None:
            _reject(state, outcome, "block requires reason and no handoff")
        return _record(state, outcome, status="blocked", wait_for=None, artifact_state="blocked")
    if state.status in ("ready", "blocked", "completed"):
        _reject(state, outcome, "state cannot advance; start ready states explicitly")
    if outcome.gate and outcome.gate.gate_id != node.quality_gate:
        _reject(state, outcome, "gate belongs to another workshop")

    if outcome.outcome == "wait":
        if next_node is not None or not outcome.reason or outcome.wait_for not in ("gate", "data"):
            _reject(state, outcome, "wait requires gate/data reason and cannot hand off")
        if state.status != "running":
            _reject(state, outcome, "only running work can be submitted or suspended")
        if outcome.gate is not None:
            _reject(state, outcome, "wait cannot smuggle a gate result")
        return _record(state, outcome, status="waiting_gate", wait_for=outcome.wait_for,
                       artifact_state="candidate" if outcome.wait_for == "gate" else state.artifact_state)

    if outcome.outcome == "rework":
        if state.status not in ("waiting_gate", "waiting_human") or not outcome.reason:
            _reject(state, outcome, "rework requires a waiting state and reason")
        target = next_node or node.rework_node
        if target is None or target != node.rework_node:
            _reject(state, outcome, f"rework must target the declared workshop; requested={target}")
        if outcome.gate and outcome.gate.outcome not in ("rework", "needs_evidence"):
            _reject(state, outcome, "rework conflicts with gate result")
        return _record(state, outcome, status="running", current_node=target,
                       wait_for=None, artifact_state="candidate")

    if state.status not in ("waiting_gate", "waiting_human"):
        _reject(state, outcome, "submit to the gate before passing or completing")
    if state.wait_for == "data":
        _reject(state, outcome, "resume data wait before submitting to gate")
    if state.status == "waiting_gate":
        if outcome.gate is None:
            _reject(state, outcome, "gate result is required")
        gate = outcome.gate
        if gate.outcome == "block":
            if next_node is not None:
                _reject(state, outcome, "blocked gate cannot hand off")
            return _record(state, outcome, status="blocked", wait_for=None, artifact_state="blocked")
        if gate.outcome == "quarantine":
            if next_node is not None:
                _reject(state, outcome, "quarantine cannot hand off")
            return _record(state, outcome, artifact_state="candidate")
        if gate.outcome != "pass":
            _reject(state, outcome, "non-passing gate requires explicit rework outcome")
        if node.human_review:
            if next_node is not None or outcome.approved_by is not None:
                _reject(state, outcome, "human approval must follow gate freeze in a separate transition")
            return _record(state, outcome, status="waiting_human", wait_for="human", artifact_state="frozen")
    else:
        if not outcome.approved_by or state.artifact_state != "frozen":
            _reject(state, outcome, "human approval of frozen artifact is required")
        if outcome.gate is not None:
            _reject(state, outcome, "human approval cannot replace the frozen gate result")

    artifact = "approved" if state.status == "waiting_human" else "frozen"
    if not node.handoff_options:
        if next_node is not None:
            _reject(state, outcome, "terminal workshop cannot hand off")
        return _record(state, outcome, status="completed", wait_for=None, artifact_state=artifact)
    if outcome.outcome == "complete":
        _reject(state, outcome, "cannot complete before final workshop")
    if next_node not in node.handoff_options:
        _reject(state, outcome, f"undeclared handoff: {next_node}")
    return _record(state, outcome, status="running", current_node=next_node,
                   wait_for=None, artifact_state=None)
