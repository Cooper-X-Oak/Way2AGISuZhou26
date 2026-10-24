"""System design A–E (25 enumerated tables, despite the design's '21' heading).

All entity IDs are ULIDs. area_poi is the explicitly composite-key association.
Quote offsets count Unicode characters in the UTF-8 decoded source blob; NULL
means the legacy text was not found verbatim. No fuzzy quote is manufactured.
"""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, CheckConstraint, DateTime, Float, ForeignKey,
                        Index, Integer, MetaData, String, Text, UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator
from ulid import ULID


def new_id() -> str:
    return str(ULID())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """SQLite drops timezone metadata; restore UTC on reads, reject naive input."""
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("timestamp must be timezone-aware")
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return value


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


class Entity:
    id: Mapped[str] = mapped_column(String(26), primary_key=True, default=new_id)


def fk(table, **kwargs):
    return mapped_column(String(26), ForeignKey(table + ".id"), index=True, **kwargs)


class Workspace(Entity, Base):
    __tablename__ = "workspace"
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AppUser(Entity, Base):
    __tablename__ = "app_user"
    workspace_id: Mapped[str] = fk("workspace")
    name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    __table_args__ = (CheckConstraint("role IN ('manager','rep','admin')", name="role"),)


class ParamSet(Entity, Base):
    __tablename__ = "param_set"
    version: Mapped[int] = mapped_column(unique=True)
    content: Mapped[dict] = mapped_column(JSON)
    sources: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Rule(Entity, Base):
    __tablename__ = "rule"
    kind: Mapped[str] = mapped_column(String(20), index=True)
    terms: Mapped[list] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (CheckConstraint("kind IN ('extreme','efficacy','competitor','brand')", name="kind"),)


class Product(Entity, Base):
    __tablename__ = "product"
    workspace_id: Mapped[str] = fk("workspace")
    name: Mapped[str] = mapped_column(String(255))
    category_noun: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    current_version_id: Mapped[str | None] = mapped_column(String(26), ForeignKey(
        "profile_version.id", use_alter=True, name="fk_product_current_version_id_profile_version"))
    __table_args__ = (UniqueConstraint("workspace_id", "name"),
                     CheckConstraint("status IN ('draft','active')", name="status"))


class SourceDoc(Entity, Base):
    __tablename__ = "source_doc"
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(Text)
    blob_sha: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("kind IN ('brief','pasted','web')", name="kind"),)


class ProfileVersion(Entity, Base):
    __tablename__ = "profile_version"
    product_id: Mapped[str] = fk("product")
    version: Mapped[int]
    source_doc_id: Mapped[str] = fk("source_doc")
    created_by: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    __table_args__ = (UniqueConstraint("product_id", "version"),
                     CheckConstraint("created_by IN ('llm','human')", name="created_by"))


class ProfileFact(Entity, Base):
    __tablename__ = "profile_fact"
    version_id: Mapped[str] = fk("profile_version")
    kind: Mapped[str] = mapped_column(String(20))
    key: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)
    quote_offset: Mapped[int | None]
    __table_args__ = (UniqueConstraint("version_id", "kind", "key"),
                     CheckConstraint("kind IN ('selling_point','scene','fact','redline','competitor')", name="kind"),
                     CheckConstraint("quote_offset IS NULL OR (quote_offset >= 0 AND quote IS NOT NULL)", name="quote_offset"))


class Area(Entity, Base):
    __tablename__ = "area"
    workspace_id: Mapped[str] = fk("workspace")
    name: Mapped[str] = mapped_column(String(255))
    center_lat: Mapped[float]
    center_lon: Mapped[float]
    radius_m: Mapped[float]
    crs: Mapped[str] = mapped_column(String(20))
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)


class Capture(Entity, Base):
    __tablename__ = "capture"
    area_id: Mapped[str] = fk("area")
    source: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))
    calls: Mapped[int | None]
    budget: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(40))
    truncated: Mapped[dict] = mapped_column(JSON)
    raw_blob_sha: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (UniqueConstraint("area_id", "kind", "raw_blob_sha"),
                     CheckConstraint("source IN ('osm','amap')", name="source"),
                     CheckConstraint("kind IN ('dining','competitor','partner')", name="kind"))


class Poi(Entity, Base):
    __tablename__ = "poi"
    source: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(500))
    lat: Mapped[float]
    lon: Mapped[float]
    typecode: Mapped[str | None] = mapped_column(String(255))
    type_name: Mapped[str | None] = mapped_column(Text)
    first_capture_id: Mapped[str] = fk("capture")
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (UniqueConstraint("source", "source_id"),)


class PoiClass(Entity, Base):
    __tablename__ = "poi_class"
    poi_id: Mapped[str] = fk("poi")
    rule_version: Mapped[int] = mapped_column(index=True)
    channel_id: Mapped[str | None] = mapped_column(String(100), index=True)
    role: Mapped[str] = mapped_column(String(20), index=True)
    partner_type: Mapped[str | None] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(Text)
    judged_by: Mapped[str] = mapped_column(String(20))
    __table_args__ = (CheckConstraint("role IN ('terminal','competitor','retail','partner')", name="role"),)


class AreaPoi(Base):
    __tablename__ = "area_poi"
    area_id: Mapped[str] = mapped_column(String(26), ForeignKey("area.id"), primary_key=True)
    poi_id: Mapped[str] = mapped_column(String(26), ForeignKey("poi.id"), primary_key=True)
    distance_m: Mapped[float]
    cell_key: Mapped[str] = mapped_column(String(40))
    __table_args__ = (Index("ix_area_poi_area_cell", "area_id", "cell_key"), Index("ix_area_poi_poi_id", "poi_id"))


class GridCell(Entity, Base):
    __tablename__ = "grid_cell"
    area_id: Mapped[str] = fk("area")
    cell_key: Mapped[str] = mapped_column(String(40))
    centroid_lat: Mapped[float]
    centroid_lon: Mapped[float]
    counts: Mapped[dict] = mapped_column(JSON)
    availability: Mapped[int]
    rule_version: Mapped[int]
    __table_args__ = (UniqueConstraint("area_id", "cell_key", "rule_version"),)


class Run(Entity, Base):
    __tablename__ = "run"
    workspace_id: Mapped[str] = fk("workspace")
    product_version_id: Mapped[str] = fk("profile_version")
    area_id: Mapped[str] = fk("area")
    param_set_id: Mapped[str] = fk("param_set")
    rule_version: Mapped[int]
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    current_stage: Mapped[str | None] = mapped_column(String(40))
    input_text: Mapped[str] = mapped_column(Text)
    brief: Mapped[dict] = mapped_column(JSON, default=dict)
    tokens_used: Mapped[int] = mapped_column(default=0)
    calls_used: Mapped[int] = mapped_column(default=0)
    replay_seed: Mapped[int | None]
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (CheckConstraint("mode IN ('rules','llm')", name="mode"),
                     CheckConstraint("status IN ('queued','running','waiting','completed','failed')", name="status"))


class StageRun(Entity, Base):
    __tablename__ = "stage_run"
    run_id: Mapped[str] = fk("run")
    stage: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(40))
    attempts: Mapped[int] = mapped_column(default=0)
    judged_by: Mapped[str | None] = mapped_column(String(20))
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    __table_args__ = (UniqueConstraint("run_id", "stage", "revision"),)


class Artifact(Entity, Base):
    __tablename__ = "artifact"
    run_id: Mapped[str] = fk("run")
    stage: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(255), index=True)
    blob_sha: Mapped[str] = mapped_column(String(64))
    item_count: Mapped[int]
    schema_version: Mapped[str] = mapped_column(String(40))


class Attempt(Entity, Base):
    __tablename__ = "attempt"
    stage_run_id: Mapped[str] = fk("stage_run")
    n: Mapped[int]
    status: Mapped[str] = mapped_column(String(40))
    output_artifact_id: Mapped[str | None] = fk("artifact")
    violations: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str | None] = mapped_column(String(255))
    tokens_in: Mapped[int] = mapped_column(default=0)
    tokens_out: Mapped[int] = mapped_column(default=0)
    prompt_sha: Mapped[str | None] = mapped_column(String(64))
    seeded_for_demo: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (UniqueConstraint("stage_run_id", "n"),)


class Event(Entity, Base):
    __tablename__ = "event"
    run_id: Mapped[str] = fk("run")
    seq: Mapped[int]
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    stage: Mapped[str | None] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    refs: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str | None] = mapped_column(String(40))
    parent_id: Mapped[str | None] = fk("event")
    __table_args__ = (Index("ix_event_run_seq", "run_id", "seq", unique=True),)


class Gate(Entity, Base):
    __tablename__ = "gate"
    run_id: Mapped[str] = fk("run")
    stage: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    proposal: Mapped[dict] = mapped_column(JSON)
    selection: Mapped[dict | None] = mapped_column(JSON)
    decided_by: Mapped[str | None] = fk("app_user")
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (CheckConstraint("status IN ('pending','approved','rejected')", name="status"),)


class Clarification(Entity, Base):
    __tablename__ = "clarification"
    run_id: Mapped[str] = fk("run")
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    asked_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    answered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TerminalPick(Entity, Base):
    __tablename__ = "terminal_pick"
    run_id: Mapped[str] = fk("run")
    poi_id: Mapped[str] = fk("poi")
    channel_id: Mapped[str] = mapped_column(String(100), index=True)
    cell_key: Mapped[str] = mapped_column(String(40), index=True)
    score: Mapped[float] = mapped_column(index=True)
    components: Mapped[dict] = mapped_column(JSON)
    grade: Mapped[str] = mapped_column(String(20), index=True)
    next_action: Mapped[str] = mapped_column(Text)
    supplier_poi_id: Mapped[str | None] = fk("poi")
    __table_args__ = (UniqueConstraint("run_id", "poi_id"),)


class Task(Entity, Base):
    __tablename__ = "task"
    run_id: Mapped[str] = fk("run")
    assignee_id: Mapped[str | None] = fk("app_user")
    week: Mapped[int]
    day: Mapped[int]
    seq: Mapped[int]
    poi_id: Mapped[str] = fk("poi")
    next_action: Mapped[str] = mapped_column(Text)
    material_artifact_id: Mapped[str | None] = fk("artifact")
    status: Mapped[str] = mapped_column(String(40))
    __table_args__ = (Index("ix_task_run_schedule", "run_id", "week", "day", "seq"),)


class Visit(Entity, Base):
    __tablename__ = "visit"
    task_id: Mapped[str] = fk("task")
    outcome: Mapped[str] = mapped_column(String(20))
    competitor_seen: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    __table_args__ = (CheckConstraint("outcome IN ('stocked','declined','later')", name="outcome"),)


class LlmCall(Entity, Base):
    __tablename__ = "llm_call"
    run_id: Mapped[str] = fk("run")
    stage: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(255))
    tokens_in: Mapped[int]
    tokens_out: Mapped[int]
    latency_ms: Mapped[int]
    status: Mapped[str] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
