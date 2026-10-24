from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class Violation(Model):
    code: str
    path: str
    message: str
    rule_id: str | None = None
    matched_term: str | None = None
    offset: int | None = None


class Artifact(Model):
    judged_by: str = Field(pattern=r"^(rules|llm|human|llm:.+)$")


class Brief(Artifact):
    evidence: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    open: list[str]
    request: str
    lat: float | None = None
    lon: float | None = None
    radius_m: int | None = None
    profile_id: str | None = None
    delivery_people: int | None = None
    weeks: int | None = None
    visits_per_person_day: int | None = None
    planned_visits_day: int | None = None
    site_note: str
    location_note: str
    questions: list[str]
    assumptions: list[str]


GridType = Literal['夜宵餐饮街','商务宴请区','社区餐饮','产业园食堂/简餐','混合','非目标']


class VisitDay(Model):
    week: int = Field(ge=1)
    day: int = Field(ge=1, le=7)
    person: int = Field(ge=1)
    poi_ids: list[str]
    route_distance_m: float = Field(ge=0)


def validate(schema, artifact):
    from pydantic import ValidationError
    try:
        schema.model_validate(artifact)
        return []
    except ValidationError as exc:
        return [Violation(code="schema", path=".".join(map(str, e["loc"])), message=e["msg"]) for e in exc.errors(include_input=False)]


def violation(code, path, message):
    return Violation(code=code, path=path, message=message)
