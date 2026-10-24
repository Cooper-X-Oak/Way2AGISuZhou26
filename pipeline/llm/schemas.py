"""Model-owned fields only; arithmetic remains in the stage implementations."""
from typing import Literal

from pydantic import Field

from pipeline.contracts import GridType, Model
from pipeline.v2_contracts import Channels, Evidence


class BriefDecision(Model):
    lat: float | None
    lon: float | None
    radius_m: int | None
    profile_id: str | None
    delivery_people: int | None
    weeks: int | None
    visits_per_person_day: int | None
    planned_visits_day: int | None
    questions: list[str]


class ZoneDecision(Evidence):
    zone_id: str
    tier: Literal['城市级', '区域级', '社区级']
    type: GridType


class TerminalDecision(Evidence):
    poi_id: str
    channel_id: str
    zone_id: str
    reason: str = Field(min_length=1)


class CompetitionDecision(Evidence):
    zone_id: str
    strategy: Literal['补位', '正面', '避让']
    reason: str


class PartnerDecision(Evidence):
    partner_id: str
    type: Literal['酒水批发', '餐饮供应链', '品牌自有网点', '其他']
    reason: str


class MaterialDecision(Evidence):
    target: str
    pitch: str = Field(min_length=1)
    table_card: str = Field(min_length=1)
    selling_point_ids: list[str] = Field(min_length=1)


class ZonesDecision(Model):
    decision: str = Field(min_length=1)
    items: list[ZoneDecision]


class TerminalsDecision(Model):
    decision: str = Field(min_length=1)
    items: list[TerminalDecision]


class CompetitionDecisions(Model):
    decision: str = Field(min_length=1)
    items: list[CompetitionDecision]


class PartnersDecision(Model):
    decision: str = Field(min_length=1)
    items: list[PartnerDecision]


class MaterialsDecision(Model):
    decision: str = Field(min_length=1)
    items: list[MaterialDecision]


class Probe(Model):
    ok: bool


SCHEMAS = dict(L0=BriefDecision, L1=Channels, L2=ZonesDecision,
               L2C=CompetitionDecisions, L3=TerminalsDecision,
               L3P=PartnersDecision, L4=MaterialsDecision)


def decision_only(stage, artifact):
    """Strip code-owned artifact fields when sending a failed attempt back."""
    if not isinstance(artifact, dict):
        return artifact
    schema = SCHEMAS[stage]
    result = {k: v for k, v in artifact.items() if k in schema.model_fields}
    item_field = schema.model_fields.get('items')
    if item_field and isinstance(result.get('items'), list):
        item_schema = item_field.annotation.__args__[0]
        result['items'] = [
            {k: v for k, v in row.items() if k in item_schema.model_fields}
            if isinstance(row, dict) else row for row in result['items']
        ]
    return result
