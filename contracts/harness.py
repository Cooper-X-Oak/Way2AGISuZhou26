"""Product Bible v1.0 sections 03–06 and 10, expressed as immutable JSON contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
BriefStatus = Literal["draft", "needs_user_input", "approved", "superseded"]
DeliveryProfile = Literal["restaurant_entry", "distributor_recruitment", "competition_inspection"]
ProductionStatus = Literal["ready", "running", "waiting_gate", "waiting_human", "blocked", "completed"]
ArtifactState = Literal["candidate", "frozen", "approved", "superseded", "blocked"]
GateOutcome = Literal["pass", "needs_evidence", "rework", "quarantine", "block"]


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TargetScope(Contract):
    city: Text | None = None
    territories: tuple[Text, ...] = ()
    radius_km: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    channel_types: tuple[Text, ...] = ()


class Resources(Contract):
    salespeople: int | None = Field(default=None, gt=0, strict=True)
    period_days: int | None = Field(default=None, gt=0, strict=True)
    daily_first_visits: int | None = Field(default=None, gt=0, strict=True)


class SourcedAssumption(Contract):
    statement: Text
    source: Text
    kind: Literal["assumption", "unknown"]


class BriefApproval(Contract):
    approved_by: Text
    version: int = Field(ge=1, strict=True)


class ChannelEntryBrief(Contract):
    schema_version: Literal["channel-entry-brief/v1"] = "channel-entry-brief/v1"
    project_id: Text
    brief_id: Text
    version: int = Field(default=1, ge=1, strict=True)
    status: BriefStatus = "draft"
    decision_question: Text | None = None
    product_profile_version: Text | None = None
    target_scope: TargetScope = Field(default_factory=TargetScope)
    resources: Resources = Field(default_factory=Resources)
    entry_modes: tuple[Literal["direct", "distributor", "recruit_distributor"], ...] = ()
    # None means unanswered; () means explicitly confirmed empty.
    exclusions: tuple[Text, ...] | None = None
    compliance_constraints: tuple[Text, ...] | None = None
    delivery_profile: DeliveryProfile | None = None
    assumptions_and_unknowns: tuple[SourcedAssumption, ...] | None = None
    stop_conditions: tuple[Text, ...] = ()
    approval: BriefApproval | None = None
    supersedes: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def version_links(self) -> ChannelEntryBrief:
        if self.supersedes is not None and self.supersedes >= self.version:
            raise ValueError("supersedes must reference an earlier version of this brief")
        if self.approval and self.approval.version != self.version:
            raise ValueError("approval version does not match brief")
        if self.status in ("approved", "superseded") and self.approval is None:
            raise ValueError("approved/superseded brief requires approval provenance")
        if self.status in ("draft", "needs_user_input") and self.approval is not None:
            raise ValueError("unapproved brief cannot carry approval")
        return self


class BusinessNode(Contract):
    node_id: Text
    objective: Text
    input_contracts: tuple[Text, ...] = Field(min_length=1)
    output_contracts: tuple[Text, ...] = Field(min_length=1)
    agent_role: Text
    skill_group: tuple[Text, ...] = Field(min_length=1)
    quality_gate: Text
    handoff_options: tuple[Text, ...] = ()
    rework_node: Text | None = None
    allowed_tools: tuple[Text, ...] = ()
    coverage_standards: tuple[Text, ...] = Field(min_length=1)
    human_review: bool = False


class ProductionGraph(Contract):
    version: Text = "v1"
    entry_node: Text
    nodes: tuple[BusinessNode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def references_exist(self) -> ProductionGraph:
        ids = {node.node_id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("duplicate node_id")
        if self.entry_node not in ids:
            raise ValueError("entry_node is absent")
        for node in self.nodes:
            refs = node.handoff_options + ((node.rework_node,) if node.rework_node else ())
            if not set(refs) <= ids:
                raise ValueError(f"unknown handoff/rework reference in {node.node_id}")
        return self

    def node(self, node_id: str) -> BusinessNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise ValueError(f"unknown node: {node_id}")


class GateResult(Contract):
    gate_id: Text
    outcome: GateOutcome
    reasons: tuple[Text, ...] = ()
    evidence_ids: tuple[Text, ...] = ()
    judgment_by: Literal["rules", "agent", "human"] = "rules"

    @model_validator(mode="after")
    def failure_reason(self) -> GateResult:
        if self.outcome != "pass" and not self.reasons:
            raise ValueError("non-passing gate requires reasons")
        return self


class NodeOutcome(Contract):
    node_id: Text
    outcome: Literal["pass", "rework", "wait", "block", "complete"]
    reason: Text | None = None
    gate: GateResult | None = None
    wait_for: Literal["gate", "human", "data"] | None = None
    approved_by: Text | None = None


class Transition(Contract):
    sequence: int = Field(ge=1)
    from_status: ProductionStatus
    to_status: ProductionStatus
    from_node: Text
    to_node: Text
    outcome: NodeOutcome
    accepted: bool = True
    rejection_reason: Text | None = None


class ScenarioComposition(Contract):
    schema_version: Literal["scenario-composition/v1"] = "scenario-composition/v1"
    project_id: Text
    brief_id: Text
    brief_version: int = Field(ge=1)
    delivery_profile: DeliveryProfile
    graph: ProductionGraph
    deliverables: tuple[Text, ...] = Field(min_length=1)
    configuration_source: Text


class ProductionState(Contract):
    project_id: Text
    brief_id: Text
    brief_version: int = Field(ge=1)
    graph: ProductionGraph
    status: ProductionStatus = "ready"
    current_node: Text
    artifact_state: ArtifactState | None = None
    wait_for: Literal["gate", "human", "data"] | None = None
    ledger: tuple[Transition, ...] = ()

    @model_validator(mode="after")
    def coherent_state(self) -> ProductionState:
        self.graph.node(self.current_node)
        if self.status == "ready" and self.current_node != self.graph.entry_node:
            raise ValueError("ready state must start at graph entry")
        if self.status == "waiting_human" and self.wait_for != "human":
            raise ValueError("waiting_human requires human wait reason")
        if self.status == "waiting_gate" and self.wait_for not in ("gate", "data"):
            raise ValueError("waiting_gate requires gate/data wait reason")
        if self.status not in ("waiting_gate", "waiting_human") and self.wait_for is not None:
            raise ValueError("non-waiting state cannot carry wait_for")
        return self
