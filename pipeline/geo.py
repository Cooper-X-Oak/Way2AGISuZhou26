import math
from collections import Counter

EARTH_M = 6371008.8
CATEGORIES = ("office", "office_building", "industrial_park", "school", "hospital", "gym", "tea", "restaurant", "water_station")


def distance(lat1, lon1, lat2, lon2):
    a, b = math.radians(lat1), math.radians(lat2)
    dlat, dlon = b - a, math.radians(lon2 - lon1)
    h = math.sin(dlat / 2)**2 + math.cos(a)*math.cos(b)*math.sin(dlon / 2)**2
    return 2 * EARTH_M * math.asin(min(1, math.sqrt(h)))


def grid_id(poi, site):
    x = EARTH_M * math.radians(poi["lon"] - site["lon"]) * math.cos(math.radians(site["lat"]))
    y = EARTH_M * math.radians(poi["lat"] - site["lat"])
    return f"{math.floor(x / 500)}:{math.floor(y / 500)}"


def grids(pois, brief):
    counts = {}
    for p in pois:
        if distance(brief["lat"], brief["lon"], p["lat"], p["lon"]) <= brief["radius_m"]:
            key = grid_id(p, brief)
            counts.setdefault(key, Counter({c: 0 for c in CATEGORIES}))[p["category"]] += 1
    return {key: dict(value) for key, value in sorted(counts.items())}
