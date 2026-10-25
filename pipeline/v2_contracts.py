from typing import Literal
from pydantic import Field
from pipeline.contracts import Model, GridType, VisitDay


class Evidence(Model):
    evidence: list[str] = Field(min_length=1)
    judged_by: Literal['rules', 'llm', 'human']
    confidence: float = Field(ge=0, le=1)
    open: list[str]


class Envelope(Evidence):
    decision: str = Field(min_length=1)


class Commercial(Model):
    channel_margin: str | None
    price_band: str | None
    first_order_policy: str | None


class Channel(Evidence):
    channel_id: str
    scene_ids: list[str] = Field(min_length=1)
    selling_point_ids: list[str] = Field(min_length=1)
    priority: Literal['P1', 'P2', 'P3', 'excluded']
    reason: str = Field(min_length=1)
    commercial_conditions: Commercial


class ExcludedScene(Model):
    scene_id: str
    reason: str = Field(min_length=1)


class Channels(Envelope):
    items: list[Channel] = Field(min_length=1)
    excluded_scenes: list[ExcludedScene]


class Zone(Evidence):
    zone_id: str
    tier: Literal['城市级', '区域级', '社区级']
    type: GridType
    counts: dict[str, int]
    rank: int = Field(ge=1)
    selected: bool


class Zones(Envelope):
    items: list[Zone]


class Components(Model):
    channel_weight: float = Field(ge=0, le=1)
    zone_rank: float = Field(ge=0, le=1)
    distance_decay: float = Field(ge=0, le=1)
    data_completeness: float = Field(ge=0, le=1)


class Terminal(Evidence):
    poi_id: str
    channel_id: str
    zone_id: str
    score_components: Components
    score: float = Field(ge=0, le=1)
    grade: Literal['A', 'B', 'C']
    next_action: Literal['首访-带样品和桌牌', '首访-了解需求', '暂缓']
    reason: str = Field(min_length=1)


class Terminals(Envelope):
    items: list[Terminal]


class Compliance(Model):
    passed: bool
    hits: list[dict]


class Material(Evidence):
    target: str
    pitch: str = Field(min_length=1)
    table_card: str = Field(min_length=1)
    selling_point_ids: list[str] = Field(min_length=1)
    compliance: Compliance


class Materials(Envelope):
    items: list[Material]
    seeded_for_demo: bool = False
    agent: dict | None = None


class CompetitionRow(Evidence):
    zone_id: str
    counts: dict[str, int]
    intensity: Literal['低','中','高']
    strategy: Literal['补位','正面','避让']
    reason: str


class Competition(Envelope):
    items: list[CompetitionRow]


class Partner(Evidence):
    partner_id: str
    type: Literal['酒水批发','餐饮供应链','品牌自有网点','其他']
    covered_terminal_ids: list[str]
    coverage_count: int = Field(ge=0)
    recommendation: Literal['优先','备选','暂缓']
    recommended_zone_ids: list[str]
    reason: str


class Partners(Envelope):
    items: list[Partner]


class Pack(Envelope):
    items: list[Terminal]
    backlog: list[Terminal]
    channels: Channels
    zones: Zones
    materials: Materials
    days: list[VisitDay]
    trace: dict
    coverage: dict
    review_queue: list[dict]
    planning_note: str
    competition: Competition
    partners: Partners
    supply_assignments: dict
    recruitment_gaps: dict


class ChannelCoverage(Evidence):
    channel_id: str
    candidate_count: int = Field(ge=0)
    status: Literal['sufficient', 'acquired', 'insufficient_after_search', 'not_searched_offline']
    reason: str = Field(min_length=1)


class Discovery(Envelope):
    items: list[ChannelCoverage] = Field(min_length=1)
    added_poi_ids: list[str]
    added_pois: list[dict]
    agent: dict | None
