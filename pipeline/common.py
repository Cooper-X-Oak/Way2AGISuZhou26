import re
from collections import Counter
from pipeline.contracts import Violation, violation
from pipeline.geo import distance, grid_id


def decide(inputs, stage, payload, rules, contract):
    return inputs["judge"].decide(stage, {
        **payload, "previous": inputs.get("previous"),
        "violations": inputs.get("violations", []), "output_contract": contract,
    }, rules)


def provenance(artifact, inputs):
    return [] if artifact["judged_by"] == inputs["judge"].label.split(":", 1)[0] else [violation("provenance", "judged_by", "判断来源不符")]


def exact_set(rows, key, expected, path):
    ids = [r[key] for r in rows]
    return [] if len(ids) == len(set(ids)) and set(ids) == set(expected) else [violation("item_set", path, "条目遗漏、重复或多出")]


def scan(value, profile, path=""):
    errors = []
    if isinstance(value, dict):
        for k, v in value.items(): errors.extend(scan(v, profile, f"{path}.{k}".strip(".")))
    elif isinstance(value, list):
        for i, v in enumerate(value): errors.extend(scan(v, profile, f"{path}[{i}]"))
    elif isinstance(value, str):
        for rule in profile["compliance_rules"]:
            for term in rule["terms"]:
                for match in re.finditer(re.escape(term), value):
                    errors.append(Violation(code="compliance", path=path, message=f"命中禁用词；出处：{rule['source']}", rule_id=rule["id"], matched_term=term, offset=match.start()))
    return errors


def selected_pois(inputs):
    brief = inputs["brief"]
    return [p for p in inputs["pois"] if distance(brief["lat"], brief["lon"], p["lat"], p["lon"]) <= brief["radius_m"]]


def channel_for(poi, profile):
    """Ordered matching, one channel per POI: no double counting or invented subtype."""
    if poi.get('source') == 'AMap':
        from pipeline.amap import AmapAdapter
        tags=poi.get('tags', {})
        # Reclassify cached rows too; explicit API subtype precedes name inference.
        subtypes=' '.join(AmapAdapter().subtypes(str(tags.get('typecode','')),str(tags.get('type',''))))
        for c in profile['channels']:
            if poi['category'] in c['categories'] and c.get('pattern') and re.search(c['pattern'],subtypes,re.I):
                return c
        mapping=AmapAdapter().mapping
        name=poi.get('name') or ''
        named=[r['subtype'] for r in mapping.get('named_restaurants',[]) if re.fullmatch(re.escape(r['name'])+r'(?:[（(][^（）()]+[）)])?',name)]
        name_subtypes=' '.join(named+[value for word,value in mapping.get('name_subtypes',{}).items() if word in name])
        for c in profile['channels']:
            if poi['category'] in c['categories'] and c.get('pattern') and re.search(c['pattern'],name_subtypes,re.I):
                return c
    text = " ".join(str(poi.get(k) or "") for k in ("name", "restaurant_types")) + " " + str(poi.get("tags", {}).get("cuisine", ""))
    for c in profile["channels"]:
        if poi["category"] not in c["categories"]: continue
        if c.get("pattern") and not re.search(c["pattern"], text, re.I): continue
        return c
    return None


def grid_evidence(inputs):
    from pipeline.geo import CATEGORIES
    keys = list(CATEGORIES) + [c["id"] for c in inputs["profile"]["channels"]]
    result = {}
    for p in selected_pois(inputs):
        counts = result.setdefault(grid_id(p, inputs["brief"]), Counter({k: 0 for k in keys}))
        counts[p["category"]] += 1
        channel = channel_for(p, inputs["profile"])
        if channel: counts[channel["id"]] += 1
    return {k: dict(v) for k, v in sorted(result.items())}


def coverage_counts(pois, profile):
    counts = {c["id"]: 0 for c in profile["channels"]}
    for p in pois:
        c = channel_for(p, profile)
        if c: counts[c["id"]] += 1
    return counts
