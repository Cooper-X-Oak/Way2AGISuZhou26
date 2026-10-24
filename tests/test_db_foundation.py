"""SQLite integration tests; synthetic rows here are test fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session
import yaml

from app.blobs import BlobStore
from app.db import ROOT, make_engine, migrate
from app.domain import models as m
from app.repo import (AreaRepository, ConfigRepository, ExecutionRepository,
                      ProductRepository, RunRepository)
from scripts.import_legacy import import_legacy


@pytest.fixture
def engine(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "app.db").as_posix())
    migrate(engine)
    yield engine
    engine.dispose()


def seed(session):
    repo = ConfigRepository(session)
    workspace = repo.add(m.Workspace(name="fixture"))
    user = repo.add(m.AppUser(workspace_id=workspace.id, name="fixture user", role="manager"))
    params = repo.add(m.ParamSet(version=1, content={}, sources={"test": True}))
    repo.add(m.Rule(kind="extreme", terms=["fixture term"], source="test", version=1))
    source = repo.add(m.SourceDoc(kind="brief", title="fixture brief", blob_sha="a" * 64))
    product = repo.add(m.Product(workspace_id=workspace.id, name="fixture product"))
    version = repo.add(m.ProfileVersion(product_id=product.id, version=1, source_doc_id=source.id, created_by="human"))
    product.current_version_id = version.id
    repo.add(m.ProfileFact(version_id=version.id, kind="fact", key="fixture", text="fixture fact"))
    area = repo.add(m.Area(workspace_id=workspace.id, name="fixture area", center_lat=31.3,
                           center_lon=120.6, radius_m=3000, crs="GCJ-02"))
    capture = repo.add(m.Capture(area_id=area.id, source="amap", kind="dining", calls=1,
                                budget=1, status="completed", truncated={}, raw_blob_sha="b" * 64))
    pois = []
    for index in range(4):
        poi = repo.add(m.Poi(source="amap", source_id=f"fixture-{index}", name=f"fixture POI {index}",
                            lat=31.3, lon=120.6, first_capture_id=capture.id))
        pois.append(poi)
        repo.add(m.AreaPoi(area_id=area.id, poi_id=poi.id, distance_m=index, cell_key="0:0"))
        repo.add(m.PoiClass(poi_id=poi.id, rule_version=1, channel_id="old", role="terminal", method="test", judged_by="rules"))
        repo.add(m.PoiClass(poi_id=poi.id, rule_version=2, channel_id="tea" if index < 3 else "other",
                            role="terminal", method="test", judged_by="rules"))
    repo.add(m.GridCell(area_id=area.id, cell_key="0:0", centroid_lat=31.3, centroid_lon=120.6,
                       counts={"tea": 3}, availability=1, rule_version=2))
    runs = []
    for index in range(2):
        run = repo.add(m.Run(workspace_id=workspace.id, product_version_id=version.id,
                            area_id=area.id, param_set_id=params.id, rule_version=2, mode="rules",
                            status="completed" if index == 0 else "failed", input_text="test"))
        runs.append(run)
    run = runs[0]
    stage = repo.add(m.StageRun(run_id=run.id, stage="L1", revision=1, status="completed"))
    artifact = repo.add(m.Artifact(run_id=run.id, stage="L1", name="A1", blob_sha="c" * 64,
                                 item_count=4, schema_version="1"))
    repo.add(m.Attempt(stage_run_id=stage.id, n=1, status="failed", violations=[{"code": "fixture"}]))
    repo.add(m.Attempt(stage_run_id=stage.id, n=2, status="completed", output_artifact_id=artifact.id))
    for seq in (3, 1, 2):
        repo.add(m.Event(run_id=run.id, seq=seq, actor="rules", kind="fixture", title=str(seq)))
    repo.add(m.Event(run_id=runs[1].id, seq=4, actor="rules", kind="fixture", title="other run"))
    repo.add(m.Gate(run_id=run.id, stage="L1", proposal={}, decided_by=user.id))
    repo.add(m.Clarification(run_id=run.id, question="fixture?"))
    for index, poi in enumerate(pois):
        repo.add(m.TerminalPick(run_id=run.id, poi_id=poi.id, channel_id="tea", cell_key="0:0",
                               score=10 if index < 2 else index, components={}, grade="A" if index < 3 else "B",
                               next_action="fixture"))
    task = repo.add(m.Task(run_id=run.id, assignee_id=user.id, week=1, day=1, seq=1,
                          poi_id=pois[0].id, next_action="fixture", material_artifact_id=artifact.id, status="pending"))
    repo.add(m.Visit(task_id=task.id, outcome="later", note="fixture"))
    repo.add(m.LlmCall(run_id=run.id, stage="L1", model="test", tokens_in=10,
                       tokens_out=20, latency_ms=30, status="failed", error="fixture"))
    session.flush()
    return dict(workspace=workspace, user=user, product=product, version=version, area=area,
                capture=capture, pois=pois, runs=runs, stage=stage, task=task)


def test_migration_schema_pragmas_and_postgres_ddl(engine, monkeypatch):
    expected = {"workspace", "app_user", "param_set", "rule", "product", "profile_version",
                "profile_fact", "source_doc", "area", "capture", "poi", "poi_class", "area_poi",
                "grid_cell", "run", "stage_run", "attempt", "event", "gate", "clarification",
                "artifact", "terminal_pick", "task", "visit", "llm_call"}
    assert set(inspect(engine).get_table_names()) == expected | {"alembic_version"}
    with engine.connect() as connection:
        assert connection.scalar(text("PRAGMA foreign_keys")) == 1
        assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
        assert compare_metadata(MigrationContext.configure(connection), m.Base.metadata) == []
    # Exercise the actual migration in offline PostgreSQL mode, including the
    # product/profile-version cyclic FK. No PostgreSQL server is claimed here.
    monkeypatch.setenv("APP_DB_URL", "postgresql://fixture:fixture@localhost/fixture")
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "head", sql=True)
    sql = output.getvalue()
    assert sql.count("CREATE TABLE ") == 26
    assert "ALTER TABLE product ADD CONSTRAINT fk_product_current_version_id_profile_version" in sql
    assert "TIMESTAMP WITH TIME ZONE" in sql


def test_all_repositories(engine):
    with Session(engine) as session, session.begin():
        data = seed(session)
        config, product = ConfigRepository(session), ProductRepository(session)
        assert config.workspaces() == [data["workspace"]]
        assert config.users(data["workspace"].id) == [data["user"]]
        assert config.param_set(1).sources == {"test": True}
        assert len(config.rules(version=1, kind="extreme")) == 1
        assert config.rules(active=False) == []
        assert product.products(workspace_id=data["workspace"].id) == [data["product"]]
        assert product.product(data["product"].id) is data["product"]
        assert product.version(data["product"].id, 1) is data["version"]
        assert product.versions(data["product"].id) == [data["version"]]
        assert len(product.facts(data["version"].id, kind="fact")) == 1
        assert product.source(data["version"].source_doc_id).title == "fixture brief"
        areas = AreaRepository(session)
        assert areas.areas(workspace_id=data["workspace"].id) == [data["area"]]
        assert areas.area(data["area"].id) is data["area"]
        assert areas.captures(data["area"].id) == [data["capture"]]
        assert areas.cells(data["area"].id)[0].counts == {"tea": 3}
        assert areas.cells(data["area"].id, rule_version=1) == []
        first = areas.pois(data["area"].id, role="terminal", channel="tea", cell="0:0", limit=2)
        second = areas.pois(data["area"].id, role="terminal", channel="tea", cursor=first[-1].id, limit=2)
        assert len(first + second) == 3 and len({row.id for row in first + second}) == 3
        assert areas.pois(data["area"].id, channel="old") == []
        assert len(areas.pois(data["area"].id, channel="old", rule_version=1)) == 4
        assert len(areas.classifications(data["pois"][0].id)) == 2
        runs = RunRepository(session)
        run = data["runs"][0]
        assert runs.runs(status="completed", product=data["product"].id, area=data["area"].id) == [run]
        assert runs.runs(product=m.new_id()) == []
        assert runs.run(run.id) is run
        assert len(runs.runs(limit=1)) == 1
        assert runs.stages(run.id) == [data["stage"]]
        assert [row.n for row in runs.attempts(data["stage"].id)] == [1, 2]
        assert [row.seq for row in runs.events(run.id, after_seq=1)] == [2, 3]
        assert [row.seq for row in runs.events(run.id, after_seq=1, limit=1)] == [2]
        assert runs.events(run.id, after_seq=3) == []
        assert len(runs.gates(run.id, stage="L1", status="pending")) == 1
        assert len(runs.clarifications(run.id)) == 1
        assert len(runs.artifacts(run.id, name="A1")) == 1
        assert runs.trace(run.id, data["pois"][0].id)["product_version"] is data["version"]
        assert runs.trace(data["runs"][1].id, data["pois"][0].id) is None
        execution = ExecutionRepository(session)
        assert execution.tasks(run.id, assignee_id=data["user"].id, status="pending") == [data["task"]]
        assert execution.visits(data["task"].id)[0].outcome == "later"
        assert execution.usage(run.id) == {"calls": 1, "tokens_in": 10, "tokens_out": 20, "latency_ms": 30}
        assert len(execution.llm_calls(run.id)) == 1
        assert execution.usage(data["runs"][1].id)["calls"] == 0


@pytest.mark.parametrize("sort", ["score_desc", "score_asc", "grade", "channel", "cell", "id"])
def test_terminal_keyset_filters_and_ties(engine, sort):
    with Session(engine) as session, session.begin():
        data = seed(session)
        repo = RunRepository(session)
        run_id = data["runs"][0].id
        expected = repo.terminals(run_id, grade="A", channel="tea", cell="0:0", sort=sort)
        rows, cursor = [], None
        while page := repo.terminals(run_id, grade="A", channel="tea", cell="0:0", sort=sort, cursor=cursor, limit=1):
            rows.extend(page)
            cursor = page[-1].id
        assert rows == expected and len(rows) == 3
        if sort == "score_desc":
            assert [row.score for row in rows] == [10, 10, 2]
        with pytest.raises(ValueError):
            repo.terminals(data["runs"][1].id, cursor=rows[0].id)
        with pytest.raises(ValueError):
            repo.terminals(run_id, sort="score; DROP TABLE poi")
        with pytest.raises(ValueError):
            repo.terminals(run_id, limit=0)


def test_constraints_ulids_utc_and_transaction_rollback(engine):
    with Session(engine) as session, session.begin():
        data = seed(session)
        for table in m.Base.metadata.tables.values():
            if "id" in table.c:
                for identity in session.scalars(select(table.c.id)):
                    assert len(identity) == 26
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(m.Poi(source="amap", source_id="fixture-0", lat=0, lon=0,
                              first_capture_id=data["capture"].id))
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(m.Poi(source="amap", source_id="invalid-fk", lat=0, lon=0, first_capture_id=m.new_id()))
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(m.Event(run_id=data["runs"][0].id, seq=1, actor="rules", kind="test", title="duplicate"))
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(m.AppUser(workspace_id=data["workspace"].id, name="invalid", role="invented"))
            session.flush()
        workspace_id = data["workspace"].id
        data["workspace"].created_at = datetime(2026, 1, 2, 8, tzinfo=timezone(timedelta(hours=8)))
    with Session(engine) as session:
        assert session.get(m.Workspace, workspace_id).created_at == datetime(2026, 1, 2, tzinfo=timezone.utc)
    with pytest.raises(StatementError), Session(engine) as session, session.begin():
        session.add(m.Workspace(name="naive", created_at=datetime(2026, 1, 1)))
        session.flush()
    with Session(engine) as session:
        assert session.scalar(select(m.Workspace).where(m.Workspace.name == "naive")) is None


def test_blob_idempotence_concurrency_and_integrity(tmp_path):
    store = BlobStore(tmp_path / "blobs")
    content = "原始证据\r\n".encode()
    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(store.put, [content] * 8))
    sha = hashlib.sha256(content).hexdigest()
    assert set(ids) == {sha}
    assert store.path(sha) == tmp_path / "blobs" / sha[:2] / sha
    assert store.get(sha) == content
    assert [p for p in store.root.rglob("*") if p.is_file()] == [store.path(sha)]
    store.path(sha).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        store.get(sha)
    with pytest.raises(ValueError, match="hash mismatch"):
        store.put(content)
    with pytest.raises(ValueError, match="invalid SHA"):
        store.get("../../escape")


def legacy_fixture(tmp_path):
    """Self-contained synthetic import dataset, with two products/three areas."""
    root, work = tmp_path / "legacy", tmp_path / "harness"
    (root / "profiles").mkdir(parents=True)
    (root / "pipeline").mkdir()
    (root / "pipeline/params.yaml").write_bytes((ROOT / "pipeline/params.yaml").read_bytes())
    (work / "handbook").mkdir(parents=True)
    (work / "handbook/brief.md").write_bytes("测试原文\r\n产品事实；测试场景；图像；感受；禁词。".encode())
    source = ".work/handbook/brief.md"
    for i in range(2):
        profile = dict(id=f"fixture-{i}", source=source, product={"name": f"测试产品{i}"},
            selling_points=[dict(id="point", name="卖点", fact="产品事实", source=source,
                                 scene_ids=["scene"], mapping_source="test assumption")],
            scenes=[dict(id="scene", name="测试场景", visual_keywords="图像", emotion="感受", source=source)],
            compliance_rules=[dict(id="brief-extreme-a", terms=["禁词", "不在原文"], source=source)],
            channels=[dict(id="fixture-channel", name="fixture", categories=["restaurant"], pattern=None,
                           scene_ids=["scene"], source="test assumption")])
        (root / f"profiles/fixture-{i}.yaml").write_text(yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8")
    for slug in ("a", "b", "c"):
        directory = root / f"data/areas/{slug}"
        directory.mkdir(parents=True)
        raw = root / f"data/raw/amap/{slug}"
        raw.mkdir(parents=True)
        stamp = "2026-01-01T00:00:00+00:00"
        manifest = dict(area_id=slug, calls=1, budget=2, status="completed", started_at=stamp,
                        finished_at=stamp, requests=[dict(path="1.json", fetched_at=stamp, status="success")])
        (raw / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (raw / "1.json").write_text(json.dumps({"pois": [{"id": "fixture-shared"}]}), encoding="utf-8")
        site = dict(lat=31.3, lon=120.6, display_name=f"fixture area {slug}", coordinate_system="GCJ-02")
        coverage = dict(radius_m=3000, manifest=f"data/raw/amap/{slug}/manifest.json")
        point = dict(id="amap:fixture-shared", name="fixture", lat=31.3, lon=120.6,
                     category="restaurant", source="AMap", tags={}, fetched_at=stamp,
                     classification_source="test rules")
        for name, value in {"site": site, "coverage": coverage, "pois": [point, point], "market": {}}.items():
            (directory / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    return root, work


def test_import_idempotence_quotes_poi_dedup_and_evidence(engine, tmp_path):
    root, work = legacy_fixture(tmp_path)
    store = BlobStore(tmp_path / "blobs")
    first = import_legacy(engine, store, source_root=root, work_dir=work)
    blobs_before = {p.relative_to(store.root) for p in store.root.rglob("*") if p.is_file()}
    second = import_legacy(engine, store, source_root=root, work_dir=work)
    assert first == second
    assert blobs_before == {p.relative_to(store.root) for p in store.root.rglob("*") if p.is_file()}
    assert first["table_counts"]["product"] == 2
    assert first["table_counts"]["area"] == 3
    assert first["table_counts"]["poi"] == 1
    assert first["table_counts"]["area_poi"] == 3
    assert first["table_counts"]["poi_class"] == 1
    assert first["table_counts"]["run"] == 0
    assert first["quote_not_found"] == 4  # two product names + two missing terms
    with Session(engine) as session:
        for fact, version, source in session.execute(select(m.ProfileFact, m.ProfileVersion, m.SourceDoc)
                .join(m.ProfileVersion, m.ProfileFact.version_id == m.ProfileVersion.id)
                .join(m.SourceDoc, m.ProfileVersion.source_doc_id == m.SourceDoc.id)):
            if fact.quote_offset is not None:
                original = store.get(source.blob_sha).decode()
                assert original[fact.quote_offset:fact.quote_offset + len(fact.quote)] == fact.quote
            else:
                assert fact.quote is None
                assert "quote_status: matched" not in fact.text
        for capture in session.scalars(select(m.Capture)):
            bundle = json.loads(store.get(capture.raw_blob_sha))
            response = json.loads(store.get(bundle["files"]["1.json"]))
            assert response["pois"][0]["id"] == "fixture-shared"
        assert all(row.counts == {"restaurant": 1, "fixture-channel": 1}
                   for row in session.scalars(select(m.GridCell)))
    # A conflicting v1 import rolls back; it cannot silently rewrite evidence.
    path = root / "profiles/fixture-0.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\nmaterial: changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="profile changed"):
        import_legacy(engine, store, source_root=root, work_dir=work)
    with Session(engine) as session:
        assert len(list(session.scalars(select(m.Product)))) == 2


def test_migration_downgrade_and_upgrade(engine):
    with Session(engine) as session, session.begin():
        seed(session)
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    migrate(engine)
    assert len(inspect(engine).get_table_names()) == 26


def test_import_capture_role_aliases_failures_and_first_observation(engine, tmp_path):
    root, work = legacy_fixture(tmp_path)
    directory = root / "data/raw/amap/market"
    directory.mkdir()
    stamp = "2026-01-02T00:00:00+00:00"
    manifest = dict(started_at=stamp, finished_at=stamp, status="completed", calls=2, task_cap=10,
        jobs=[dict(area="a", role="partners", job_id=0), dict(area="a", role="competitors", job_id=1)],
        requests=[dict(job_id=i, path=f"{i}-1.json", status="success", fetched_at=stamp) for i in range(2)])
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    for i in range(2):
        (directory / f"{i}-1.json").write_text(json.dumps({"pois": [{"id": "fixture-shared"}]}), encoding="utf-8")
    points = json.loads((root / "data/areas/a/pois.json").read_text(encoding="utf-8"))
    point = dict(points[0], fetched_at=stamp, capture_manifest="data/raw/amap/market/manifest.json")
    (root / "data/areas/a/market.json").write_text(json.dumps({"partners": [point], "competitors": [point]}), encoding="utf-8")
    failed_dir = root / "data/raw/amap/failed"
    failed_dir.mkdir()
    (failed_dir / "manifest.json").write_text(json.dumps(dict(area_id="a", calls=0, budget=2,
        status="failed", started_at=stamp, finished_at=stamp, requests=[])), encoding="utf-8")
    store = BlobStore(tmp_path / "blobs")
    report = import_legacy(engine, store, source_root=root, work_dir=work)
    assert report == import_legacy(engine, store, source_root=root, work_dir=work)
    assert report["table_counts"]["capture"] == 6
    assert report["table_counts"]["poi_class"] == 3
    with Session(engine) as session:
        assert len(list(session.scalars(select(m.Capture).where(m.Capture.status == "failed")))) == 1
        for kind in ("partner", "competitor"):
            capture = session.scalar(select(m.Capture).where(m.Capture.kind == kind))
            assert capture.calls == 1 and capture.budget == 10
        poi = session.scalar(select(m.Poi))
        assert session.get(m.Capture, poi.first_capture_id).started_at.day == 1
        assert poi.last_seen_at.day == 2
