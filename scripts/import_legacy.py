"""Offline, transactional import of legacy reference data (never old runs).

Run: uv run python scripts/import_legacy.py --work-dir <main-repo>/.work
     [-o <report.json>] [--db-url sqlite:///...] [--blobs-dir ...]
The CLI upgrades the schema and prints/writes counts. A repeated identical input
is a no-op. Changed v1 profiles/parameters fail rather than rewriting history.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.blobs import BlobStore
from app.db import make_engine, migrate
from app.domain.models import (Area, AreaPoi, Base, Capture, GridCell, ParamSet,
    Poi, PoiClass, Product, ProfileFact, ProfileVersion, Rule, SourceDoc, Workspace)
from pipeline.common import channel_for
from pipeline.geo import EARTH_M, distance, grid_id
from pipeline.l3p import kind as partner_kind
from pipeline.profiles import load as load_profile


def default_work_dir():
    """Resolve a linked worktree's shared harness without hardcoded machine paths."""
    common = subprocess.check_output(["git", "rev-parse", "--git-common-dir"],
                                     cwd=ROOT, text=True).strip()
    return (ROOT / common).resolve().parent / ".work"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def as_time(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"legacy timestamp lacks timezone: {value}")
    return parsed.astimezone(timezone.utc)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def ensure(session, model, identity, **values):
    row = session.scalar(select(model).filter_by(**identity))
    if row is None:
        row = model(**identity, **values)
        session.add(row)
        session.flush()
    return row


def describe(value):
    """Readable evidence text; structured source bytes are retained separately."""
    if isinstance(value, dict):
        return "\n".join(f"{key}: {describe(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "；".join(describe(item) for item in value)
    return "未提供" if value is None else str(value)


def profile_facts(profile):
    source = profile["source"]
    product = profile["product"]
    for key, value in product.items():
        if key == "specification":
            for subkey, subvalue in value.items():
                if subkey == "source":
                    continue
                items = subvalue if isinstance(subvalue, list) else [subvalue]
                for i, item in enumerate(items):
                    # Missing specifications and curator notes are metadata,
                    # not purported quotations from the brief.
                    metadata = item is None or subkey == "note"
                    yield ("fact", f"product.specification.{subkey}.{i}", describe(item),
                           None if metadata else describe(item),
                           f"profiles/{profile['id']}.yaml#product.specification.{subkey}" if metadata
                           else value.get("source", source))
        else:
            yield "fact", f"product.{key}", describe(value), describe(value), source
    for row in profile["selling_points"]:
        yield "selling_point", row["id"], describe(row), row["fact"], row["source"]
    for row in profile["scenes"]:
        for key in ("name", "visual_keywords", "emotion"):
            yield "scene", f"{row['id']}.{key}", row[key], row[key], row["source"]
    for row in profile["compliance_rules"]:
        for i, term in enumerate(row["terms"]):
            yield "redline", f"{row['id']}.{i}", term, term, row["source"]
    for i, row in enumerate(profile.get("competitors", [])):
        yield "competitor", f"competitor.{i}", describe(row), row["brand"], row["source"]
    # Keep channel/material assumptions with their original sources, never turn
    # them into brief-backed facts. The full original YAML also lives in blobs.
    for row in profile["channels"]:
        yield "fact", f"channel.{row['id']}", describe(row), None, row["source"]
    for key in ("material", "own_brand", "competitors_open"):
        if key in profile:
            yield "fact", key, describe(profile[key]), None, source


class LegacyImporter:
    def __init__(self, session, blobs, source_root=ROOT, work_dir=None):
        self.session = session
        self.blobs = blobs
        self.root = Path(source_root).resolve()
        self.work = Path(work_dir).resolve() if work_dir else default_work_dir()
        self.failures = []
        self.captures = {}
        self.channel_cache = {}
        self.first_observation = {}

    def resolve(self, reference):
        normalized = str(reference).replace("\\", "/")
        if "/.work/" in normalized:
            return self.work / normalized.split("/.work/", 1)[1]
        if normalized.startswith(".work/"):
            return self.work / normalized[6:]
        path = Path(normalized)
        return path if path.is_absolute() else self.root / path

    def source_doc(self, path, kind, title):
        sha = self.blobs.put(path.read_bytes())
        return ensure(self.session, SourceDoc, dict(kind=kind, title=title, blob_sha=sha), url=None)

    def profiles(self, workspace):
        profiles = []
        for path in sorted((self.root / "profiles").glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            profile = load_profile(raw["id"], directory=path.parent)
            profiles.append(profile)
            doc_path = self.resolve(profile["source"].split("#", 1)[0])
            doc = self.source_doc(doc_path, "brief", profile["source"])
            # Decode bytes without newline normalization: offsets address the blob.
            original = self.blobs.get(doc.blob_sha).decode("utf-8")
            snapshot_title = f"profiles/{path.name} (legacy v1)"
            previous = self.session.scalar(select(SourceDoc).where(SourceDoc.title == snapshot_title))
            if previous is not None and self.blobs.get(previous.blob_sha) != path.read_bytes():
                raise ValueError(f"legacy v1 profile changed: {path}; create a new version explicitly")
            self.source_doc(path, "pasted", snapshot_title)
            product = ensure(self.session, Product,
                dict(workspace_id=workspace.id, name=profile["product"]["name"]),
                category_noun=profile["product"].get("category_noun"), status="draft")
            version = ensure(self.session, ProfileVersion, dict(product_id=product.id, version=1),
                             source_doc_id=doc.id, created_by="human")
            if version.source_doc_id != doc.id:
                raise ValueError("legacy brief changed; create a new profile version explicitly")
            product.current_version_id = version.id
            for kind, key, text, candidate, source in profile_facts(profile):
                offset = original.find(candidate) if candidate else -1
                # A coincidental brand word in the brief cannot certify an external
                # competitor source or a project hypothesis as a brief quotation.
                is_brief = source.split("#", 1)[0] == profile["source"]
                matched = offset >= 0 and is_brief
                status = "matched" if matched else "not_found" if candidate and is_brief else "not_brief"
                ensure(self.session, ProfileFact, dict(version_id=version.id, kind=kind, key=key),
                       text=f"{text}\nsource: {source}\nquote_status: {status}",
                       quote=candidate if matched else None, quote_offset=offset if matched else None)
                if candidate and not matched:
                    self.failures.append(dict(profile=profile["id"], key=key, kind=kind,
                                              candidate=candidate, source=source, status=status))
        return profiles

    def configuration(self, profiles):
        params = yaml.safe_load((self.root / "pipeline/params.yaml").read_text(encoding="utf-8"))
        sources = {"file": "pipeline/params.yaml", "legacy_version": params["version"],
                   "sources": params.get("sources", {}), "note": params.get("note")}
        row = ensure(self.session, ParamSet, dict(version=1), content=params, sources=sources)
        if row.content != params or row.sources != sources:
            raise ValueError("legacy parameters changed; v1 is immutable")
        for profile in profiles:
            for rule in profile["compliance_rules"]:
                rid = rule["id"]
                kind = "competitor" if rid.startswith("competitor-") else (
                    "efficacy" if rid in ("brief-function", "project-conservative") else "extreme")
                # source retains the original rule identity, assumptions and URL.
                source = f"{rid}: {rule['source']}"
                existing = self.session.scalar(select(Rule).where(
                    Rule.version == 1, Rule.kind == kind, Rule.source == source))
                if existing is not None and existing.terms != rule["terms"]:
                    raise ValueError(f"conflicting legacy rule: {rid}")
                ensure(self.session, Rule, dict(version=1, kind=kind, source=source),
                       terms=rule["terms"], active=True)
        return params

    def capture_bundle(self, path):
        files = {}
        for file in sorted(path.parent.iterdir()):
            if file.is_file() and file.suffix in (".json", ".jsonl", ".txt"):
                files[file.name] = self.blobs.put(file.read_bytes())
        return self.blobs.put(json_bytes({"manifest": "manifest.json", "files": files}))

    def import_captures(self, areas, snapshots):
        paths = set((self.root / "data/raw/amap").glob("*/manifest.json"))

        def references(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if "manifest" in key and isinstance(item, str):
                        paths.add(self.resolve(item))
                    else:
                        references(item)
            elif isinstance(value, list):
                for item in value:
                    references(item)

        for snapshot in snapshots.values():
            references(snapshot)
        for path in sorted(paths):
            manifest = read_json(path)  # Missing evidence fails, never fabricates a capture.
            sha = self.capture_bundle(path)
            jobs = manifest.get("jobs", [])
            groups = {}
            if manifest.get("area_id") in areas:
                groups[(manifest["area_id"], "dining")] = []
            for job in jobs:
                kind = "partner" if job.get("role") in ("partner", "partners") else (
                    "competitor" if job.get("role") in ("competitor", "competitors", "availability") else "dining")
                if job["area"] in areas:
                    groups.setdefault((job["area"], kind), []).append(job)
            for (slug, kind), grouped_jobs in groups.items():
                if not jobs:
                    calls = manifest.get("calls")
                elif all("calls" in job for job in grouped_jobs):
                    calls = sum(job["calls"] for job in grouped_jobs)
                else:
                    ids = {job["job_id"] for job in grouped_jobs}
                    requests = manifest.get("requests", [])
                    calls = sum(request.get("job_id") in ids for request in requests)
                    if any("job_id" not in request for request in requests):
                        raise ValueError(f"cannot attribute request counts: {path}")
                truncated = {"jobs": grouped_jobs, "type_coverage": manifest.get("type_coverage", {}),
                    "manifest_path": str(path), "budget_scope": "whole_manifest",
                    "total_calls": manifest.get("calls"), "method": manifest.get("method"),
                    "source": manifest.get("source"), "policy": manifest.get("policy")}
                capture = ensure(self.session, Capture,
                    dict(area_id=areas[slug].id, kind=kind, raw_blob_sha=sha),
                    source="amap", calls=calls, budget=manifest.get("budget", manifest.get("task_cap")),
                    status=manifest["status"], truncated=truncated,
                    started_at=as_time(manifest.get("started_at")), finished_at=as_time(manifest.get("finished_at")))
                self.captures[(path.resolve(), slug, kind)] = capture
                for request in manifest.get("requests", []):
                    filename = request.get("path")
                    if not filename:
                        continue
                    if jobs:
                        matches = any(
                            ("job_id" in job and (request.get("job_id") == job["job_id"] or
                             filename.startswith(f"job-{job['job_id']}-"))) or
                            ("cell" in job and filename.startswith(f"{slug}-{job['type_code']}-{job['cell']}-"))
                            for job in grouped_jobs)
                        if not matches:
                            continue
                    response_path = path.parent / filename
                    if not response_path.is_file():
                        if request.get("status") == "success":
                            raise FileNotFoundError(response_path)
                        continue
                    response = read_json(response_path)
                    observed = as_time(request.get("fetched_at")) or capture.started_at
                    for point in response.get("pois", []):
                        identity = ("amap", point["id"])
                        previous = self.first_observation.get(identity)
                        if previous is None or (observed is not None and (previous[0] is None or observed < previous[0])):
                            self.first_observation[identity] = (observed, capture.id)

    def channel(self, point, profiles):
        identity = (point["id"], point.get("name"), json.dumps(point.get("tags"), sort_keys=True), point.get("category"))
        if identity not in self.channel_cache:
            self.channel_cache[identity] = sorted({channel["id"] for profile in profiles
                if (channel := channel_for(point, profile)) is not None})
        return self.channel_cache[identity]

    def areas(self, workspace, profiles, params):
        snapshots = {}
        areas = {}
        for directory in sorted((self.root / "data/areas").iterdir()):
            if not directory.is_dir():
                continue
            snapshot = {name: read_json(directory / f"{name}.json") for name in ("site", "coverage", "pois", "market")}
            snapshots[directory.name] = snapshot
            site, coverage = snapshot["site"], snapshot["coverage"]
            areas[directory.name] = ensure(self.session, Area,
                dict(workspace_id=workspace.id, name=site["display_name"]),
                center_lat=site["lat"], center_lon=site["lon"], radius_m=coverage["radius_m"], crs=site["coordinate_system"])
        self.import_captures(areas, snapshots)
        pois = {(p.source, p.source_id): p for p in self.session.scalars(select(Poi))}
        classes = {(p.poi_id, p.rule_version, p.role, p.channel_id, p.partner_type) for p in self.session.scalars(select(PoiClass))}
        links = {(p.area_id, p.poi_id) for p in self.session.scalars(select(AreaPoi))}
        for slug, snapshot in snapshots.items():
            area = areas[slug]
            site = snapshot["site"]
            counts, availability = {}, {}
            seen_count = set()
            seen_available = set()
            market = snapshot["market"]
            groups = [("terminal", snapshot["pois"]), ("competitor", market.get("competitors", [])),
                      ("retail", market.get("availability", [])), ("partner", market.get("partners", []))]
            for role, points in groups:
                for point in points:
                    kind = "dining" if role == "terminal" else "partner" if role == "partner" else "competitor"
                    reference = point.get("capture_manifest") or snapshot["coverage"]["manifest"]
                    capture = self.captures.get((self.resolve(reference).resolve(), slug, kind))
                    if capture is None:
                        # A retail search can refresh a partner candidate's fields.
                        # Capture kind describes the actual search, not the later role.
                        candidates = [value for (path, area_slug, _), value in self.captures.items()
                                      if path == self.resolve(reference).resolve() and area_slug == slug]
                        if len(candidates) != 1:
                            raise ValueError(f"ambiguous source capture: {reference}, {slug}, {kind}")
                        capture = candidates[0]
                    source, source_id = point["id"].split(":", 1)
                    identity = (source.lower(), source_id)
                    observed = as_time(point.get("fetched_at"))
                    tags = point.get("tags", {})
                    if identity not in pois:
                        poi = Poi(source=identity[0], source_id=source_id, name=point.get("name"),
                                  lat=point["lat"], lon=point["lon"], typecode=tags.get("typecode"),
                                  type_name=tags.get("type"), first_capture_id=self.first_observation.get(identity, (None, capture.id))[1],
                                  last_seen_at=observed)
                        self.session.add(poi)
                        self.session.flush()
                        pois[identity] = poi
                    poi = pois[identity]
                    if observed and (poi.last_seen_at is None or observed > poi.last_seen_at):
                        poi.last_seen_at = observed
                        poi.name, poi.lat, poi.lon = point.get("name"), point["lat"], point["lon"]
                        poi.typecode, poi.type_name = tags.get("typecode"), tags.get("type")
                    cell = grid_id(point, site)
                    if (area.id, poi.id) not in links:
                        self.session.add(AreaPoi(area_id=area.id, poi_id=poi.id,
                            distance_m=distance(site["lat"], site["lon"], point["lat"], point["lon"]), cell_key=cell))
                        links.add((area.id, poi.id))
                    channels = self.channel(point, profiles) if role == "terminal" else []
                    partner_type = partner_kind(point, {"params": params}) if role == "partner" else None
                    for channel in channels or [None]:
                        key = (poi.id, 1, role, channel, partner_type)
                        if key not in classes:
                            method = (point.get("classification_source") or params[
                                "amap_classification" if role == "terminal" else
                                "partners" if role == "partner" else "competition"]["source"])
                            self.session.add(PoiClass(poi_id=poi.id, rule_version=1, role=role,
                                channel_id=channel, partner_type=partner_type, method="rules; " + method, judged_by="rules"))
                            classes.add(key)
                    # Grid features include only points inside the dining radius;
                    # 5km partner candidates remain queryable through area_poi.
                    if distance(site["lat"], site["lon"], point["lat"], point["lon"]) <= area.radius_m:
                        bucket = counts.setdefault(cell, Counter())
                        if role == "terminal" and poi.id not in seen_count:
                            seen_count.add(poi.id)
                            bucket[point["category"]] += 1
                            bucket.update(channels)
                        if role in ("competitor", "retail") and poi.id not in seen_available:
                            code = str(tags.get("typecode", ""))
                            eligible = role == "competitor" or any(code.startswith(prefix)
                                for prefixes in params["competition"]["retail_typecodes"].values() for prefix in prefixes)
                            if eligible:
                                seen_available.add(poi.id)
                                availability[cell] = availability.get(cell, 0) + 1
            for cell, bucket in counts.items():
                x, y = map(int, cell.split(":"))
                ensure(self.session, GridCell, dict(area_id=area.id, cell_key=cell, rule_version=1),
                       centroid_lat=site["lat"] + math.degrees((y + .5) * 500 / EARTH_M),
                       centroid_lon=site["lon"] + math.degrees((x + .5) * 500 / (EARTH_M * math.cos(math.radians(site["lat"])))),
                       counts=dict(bucket), availability=availability.get(cell, 0))
            self.session.flush()

    def run(self):
        workspace = ensure(self.session, Workspace, dict(name="洞庭山泉"))
        profiles = self.profiles(workspace)
        params = self.configuration(profiles)
        self.areas(workspace, profiles, params)
        self.session.flush()
        counts = {name: self.session.scalar(select(func.count()).select_from(table))
                  for name, table in sorted(Base.metadata.tables.items())}
        return {"table_counts": counts, "quote_lookup_failures": len(self.failures),
                "quote_not_found": sum(item["status"] == "not_found" for item in self.failures),
                "quote_not_brief": sum(item["status"] == "not_brief" for item in self.failures),
                "quote_failures": self.failures}


def import_legacy(engine, blobs, *, source_root=ROOT, work_dir=None):
    with Session(engine) as session, session.begin():
        return LegacyImporter(session, blobs, source_root, work_dir).run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--db-url")
    parser.add_argument("--blobs-dir", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    args = parser.parse_args()
    engine = make_engine(args.db_url)
    try:
        migrate(engine)
        report = import_legacy(engine, BlobStore(args.blobs_dir),
                               source_root=args.source_root, work_dir=args.work_dir)
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output + "\n", encoding="utf-8")
        print(output)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
