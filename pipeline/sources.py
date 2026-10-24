"""Replaceable source adapter; OSM coordinates are WGS84, never GCJ02."""
from pathlib import Path
from typing import Protocol
import re
import time
import httpx
from pipeline.geo import CATEGORIES, distance
from pipeline.io import read, write, now

SITE_NOTE = "示例配送点：比赛场地，非洞庭山泉真实门店"
WATER_PATTERN = "水站|桶装水|农夫山泉|怡宝|百岁山|娃哈哈|景田|洞庭山"


class SourceAdapter(Protocol):
    def normalize(self, payload: dict, site: dict, radius_m: int) -> list[dict]: ...


def category(tags):
    name = tags.get("name", "")
    if re.search(WATER_PATTERN, name): return "water_station"
    if tags.get("shop") == "tea" or (tags.get("amenity") == "cafe" and "茶" in name): return "tea"
    if tags.get("amenity") in ("restaurant", "fast_food"): return "restaurant"
    if tags.get("amenity") in ("school", "college", "university", "kindergarten"): return "school"
    if tags.get("amenity") == "hospital": return "hospital"
    if tags.get("leisure") == "fitness_centre" or tags.get("amenity") == "gym": return "gym"
    if "office" in tags: return "office"
    if tags.get("building") in ("office", "commercial"): return "office_building"
    if tags.get("landuse") in ("industrial", "commercial"): return "industrial_park"
    return None


class OSMAdapter:
    def normalize(self, payload, site, radius_m):
        result, seen = [], set()
        for el in payload["elements"]:
            tags = el.get("tags", {})
            if "_osm_type" in tags:
                el = {"type": tags["_osm_type"], "id": el["id"],
                      "lat": float(tags["_lat"]), "lon": float(tags["_lon"])}
                tags = {k: v for k,v in tags.items() if not k.startswith("_") and v != ""}
            cat = category(tags)
            pos = el.get("center", el)
            if not cat or "lat" not in pos: continue
            d = distance(site["lat"], site["lon"], pos["lat"], pos["lon"])
            key = f"osm:{el['type']}:{el['id']}"
            if d > radius_m or key in seen: continue
            seen.add(key)
            text = tags.get("name", "") + " " + tags.get("cuisine", "")
            subtypes = [k for k, pattern in {"海鲜": "海鲜|seafood", "火锅": "火锅|hot_?pot", "烧烤": "烧烤|烤肉|barbecue|bbq", "酒楼": "酒楼|酒家|宴"}.items() if re.search(pattern, text, re.I)] if cat == "restaurant" else []
            result.append({"id": key, "name": tags.get("name"), "category": cat, "lat": pos["lat"], "lon": pos["lon"], "tags": tags, "distance_m": round(d, 2), "restaurant_types": subtypes, "source": "OpenStreetMap", "coordinate_system": "WGS84"})
        return sorted(result, key=lambda p: p["id"])


def fetch(root=Path(".")):
    root = Path(root)
    raw = root / "data/raw"
    raw.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "DongtingProspectHackathon/0.1 (research; low-volume; OSM attribution)"}
    site = None
    with httpx.Client(headers=headers, timeout=150, follow_redirects=True) as client:
        for index, query in enumerate(("苏州高新区 横山路 新技术产业园 1号楼A座", "落星桥 苏州")):
            path = raw / f"geocode-{index}.json"
            try:
                if path.exists():
                    data = read(path)
                else:
                    if index: time.sleep(1.1)
                    r = client.get("https://nominatim.openstreetmap.org/search", params={"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "cn"})
                    r.raise_for_status()
                    data = r.json()
                    write(path, data)
                candidates = [p for p in data if 30.7 <= float(p["lat"]) <= 32.1 and 119.9 <= float(p["lon"]) <= 121.4]
                if candidates:
                    p = candidates[0]
                    site = {"lat": float(p["lat"]), "lon": float(p["lon"]), "site_note": SITE_NOTE, "coordinate_system": "WGS84", "location_source": "Nominatim", "location_query": query, "location_evidence": str(path.relative_to(root)), "location_note": "比赛场地检索结果" if index == 0 else "比赛场地未精确定位，按任务授权采用落星桥地铁站附近坐标", "display_name": p["display_name"]}
                    break
            except (httpx.HTTPError, ValueError) as exc:
                write(raw / f"geocode-{index}-error.json", {"query": query, "error_type": type(exc).__name__, "time": now()})
        if site is None:
            raise RuntimeError("Nominatim 未取得比赛场地或落星桥坐标；不能编造坐标")
        write(root / "data/site.json", site)
        lat, lon = site["lat"], site["lon"]
        selectors = ['[office]', '[building~"^(office|commercial)$"]', '[landuse~"^(commercial|industrial)$"]', '[amenity~"^(school|college|university|kindergarten|hospital|gym|restaurant|fast_food)$"]', '[leisure=fitness_centre]', '[amenity=cafe][name~"茶"]', '[shop=tea]', f'[name~"{WATER_PATTERN}"]']
        # Server-side tag allowlist avoids fetching contact information.
        query = '[out:json][timeout:100];(' + ''.join(f'nwr(around:3000,{lat},{lon}){s};' for s in selectors) + ');convert item ::id=id(),_osm_type=type(),_lat=lat(),_lon=lon(),name=t["name"],office=t["office"],building=t["building"],landuse=t["landuse"],amenity=t["amenity"],leisure=t["leisure"],shop=t["shop"],cuisine=t["cuisine"];out;'
        (raw / "overpass-query.txt").write_text(query, encoding="utf-8")
        payload = None
        succeeded = False
        errors = []
        for endpoint in ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter", "https://overpass.private.coffee/api/interpreter"):
            try:
                r = client.post(endpoint, data={"data": query})
                r.raise_for_status()
                payload = r.json()
                if payload.get("remark"): raise ValueError(payload["remark"])
                (raw / "overpass.json").write_bytes(r.content)
                write(raw / "provenance.json", {"fetched_at": now(), "endpoint": endpoint, "query_file": "overpass-query.txt", "radius_m": 3000, "site": site, "attribution": "© OpenStreetMap contributors", "license": "ODbL-1.0", "license_url": "https://www.openstreetmap.org/copyright", "tag_policy": "服务端只返回分类与名称相关 tags；不请求联系方式。raw 为服务端响应原文。", "errors": errors})
                succeeded = True
                break
            except (httpx.HTTPError, ValueError) as exc:
                errors.append({"endpoint": endpoint, "error": str(exc)[:500]})
                write(raw / "fetch-errors.json", errors)
        if not succeeded: raise RuntimeError("Overpass 抓取失败，详见 data/raw/fetch-errors.json")
    elements = []
    for el in payload["elements"]:
        t = el["tags"]
        pos = {"lat": float(t["_lat"]), "lon": float(t["_lon"])}
        elements.append({"type": t["_osm_type"], "id": el["id"], **pos, "tags": {k: v for k, v in t.items() if not k.startswith("_") and v != ""}})
    pois = OSMAdapter().normalize({"elements": elements}, site, 3000)
    write(root / "data/pois.json", pois)
    write(root / "data/coverage.json", {"radius_m": 3000, "site": site, "categories": {c: sum(p["category"] == c for p in pois) for c in CATEGORIES}, "total": len(pois), "unnamed": sum(p["name"] is None for p in pois), "limitation": "OSM 点位与建筑/用地混合，覆盖不完整；不等于完整商户清单。建筑/园区不当作公司重复计入容量。"})
    return {"site": site, "poi_count": len(pois)}


if __name__ == "__main__":
    print(fetch())
