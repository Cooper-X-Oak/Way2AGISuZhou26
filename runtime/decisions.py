"""Small model decisions; full artifact schemas remain the gate boundary."""
from typing import Literal
from pydantic import Field
from pipeline.contracts import Model, GridType

class Row(Model):
    refs: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)

class L0(Model):
    fields: dict
    questions: list[str]

class Channel(Row):
    channel_id: str
    priority: Literal['P1','P2','P3','excluded']
class L1(Model):
    items: list[Channel] = Field(min_length=1, max_length=10)

class Zone(Row):
    zone_id: str
    tier: Literal['城市级','区域级','社区级']
    type: GridType
    selected: bool
class L2(Model):
    items: list[Zone] = Field(min_length=1, max_length=12)

class Competition(Row):
    zone_id: str
    intensity: Literal['低','中','高']
    strategy: Literal['补位','正面','避让']
class L2C(Model):
    items: list[Competition] = Field(min_length=1, max_length=5)

class Review(Row):
    poi_id: str
    channel_id: str | None = None
    exclude: bool = False
class L3(Model):
    items: list[Review]

class Partner(Row):
    zone_id: str
    partner_ids: list[str] = Field(max_length=3)
class L3P(Model):
    items: list[Partner] = Field(min_length=1, max_length=5)

class Template(Model):
    group_id: str
    pitch: str
    table_card: str
    selling_point_ids: list[str]
    refs: list[str] = Field(default_factory=list)
class L4(Model):
    items: list[Template] = Field(min_length=1, max_length=8)

class PackPoint(Row):
    refs: list[str] = Field(min_length=1)

class PACK(Model):
    items: list[PackPoint] = Field(min_length=1, max_length=10)

SCHEMAS = {k: globals()[k] for k in ('L0','L1','L2','L2C','L3','L3P','L4','PACK')}
