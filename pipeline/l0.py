import re
from pipeline.contracts import Brief, validate, violation
from pipeline.common import decide, provenance
from pipeline.profiles import PROFILE_DIR

REQUIRED = ("lat", "lon", "radius_m", "profile_id", "delivery_people", "weeks", "visits_per_person_day", "planned_visits_day")


def facts(inputs):
    s = inputs["site"]
    supplied = {"lat": s["lat"], "lon": s["lon"], "profile_id": inputs["profile"].get("id")}
    assumptions = [s["location_note"] + "；中心来自缓存 coverage.site，来源证据见 location_evidence；不是配送门店定位。"]
    text = inputs["request"]
    matches = list(re.finditer(r"(?:半径|周边|覆盖)(?:\s*(?:改成|改为|调整为|为|是))?\s*(-?\d+(?:\.\d+)?)\s*(km|公里|千米|m|米)", text, re.I))
    m = matches[-1] if matches else None
    if m:
        supplied["radius_m"] = int(float(m[1]) * (1000 if m[2].lower() in ("km", "公里", "千米") else 1))
    patterns = {
        "delivery_people": [r"(-?\d+)\s*(?:个|名|位)?\s*(?:地推|人)", r"(?:地推|配送员)\s*(-?\d+)\s*人"],
        "weeks": [r"(?:做|执行|计划)?\s*(-?\d+)\s*周"],
        "visits_per_person_day": [r"每人每天(?:可)?(?:进店|拜访)\s*(-?\d+)"],
        "planned_visits_day": [r"每天总(?:进店|拜访)\s*(-?\d+)"],
    }
    for key, options in patterns.items():
        matches = [m for pattern in options for m in re.finditer(pattern, text)]
        if matches:
            supplied[key] = int(max(matches, key=lambda m: m.start())[1])
    if "visits_per_person_day" not in supplied and not inputs.get("no_defaults"):
        spec = inputs["params"]["execution"]["visits_per_person_day"]
        supplied["visits_per_person_day"] = spec["default"]
        assumptions.append(f"每人每天进店数={spec['default']}，来自 params.yaml；{spec['source']}，非用户明确输入。")
    return supplied, assumptions


def run(inputs):
    supplied, assumptions = facts(inputs)
    def rules():
        result = {k: supplied.get(k) for k in REQUIRED}
        result["questions"] = [f"请补充 {k}" for k in REQUIRED if k != "planned_visits_day" and result[k] is None]
        if result["radius_m"] is not None and not 0 < result["radius_m"] <= 3000:
            result["questions"].append("半径必须大于0且不超过3000米，请明确调整范围。")
        return result
    result = decide(inputs, "L0", {"request": inputs["request"], "supplied": supplied}, rules,
                    "输出 lat,lon,radius_m,profile_id,delivery_people,weeks,visits_per_person_day,planned_visits_day,questions[]；未知=null，不得改变supplied；不计算人数乘积。")
    if result.get("planned_visits_day") is None and all(type(result.get(k)) is int for k in ("delivery_people", "visits_per_person_day")):
        result["planned_visits_day"] = result["delivery_people"] * result["visits_per_person_day"]
    result.update(request=inputs["request"], judged_by=inputs["judge"].label.split(":", 1)[0], site_note=inputs['site'].get('site_note', inputs['site']['location_note']),
                  location_note=inputs["site"]["location_note"], assumptions=assumptions)
    from pipeline.quality import meta
    result.update(meta(inputs, ["profile:"+inputs["profile"]["id"]], opened=["来源："+a for a in assumptions]))
    return result


def check(artifact, inputs):
    errors = validate(Brief, artifact)
    if errors: return errors
    errors += provenance(artifact, inputs)
    for k in REQUIRED:
        if artifact[k] is None: errors.append(violation("missing", k, "关键字段缺失，等待澄清"))
    if artifact["questions"]: errors.append(violation("clarification", "questions", "仍有未澄清问题"))
    if artifact["lat"] is not None and artifact["lon"] is not None and not (30.7 <= artifact["lat"] <= 32.1 and 119.9 <= artifact["lon"] <= 121.4):
        errors.append(violation("bbox", "lat/lon", "不在苏州市域约束 bbox 内"))
    if artifact["radius_m"] is not None and not 0 < artifact["radius_m"] <= 3000:
        errors.append(violation("radius", "radius_m", "半径必须大于0且不超过3000米"))
    for k in ("delivery_people", "weeks", "visits_per_person_day"):
        if artifact[k] is not None and artifact[k] <= 0: errors.append(violation("positive", k, "必须为正整数"))
    if all(artifact[k] is not None for k in ("planned_visits_day", "delivery_people", "visits_per_person_day")):
        if not 0 < artifact["planned_visits_day"] <= artifact["delivery_people"] * artifact["visits_per_person_day"]:
            errors.append(violation("capacity", "planned_visits_day", "每日总量超出人员容量或不为正"))
    supplied, assumptions = facts(inputs)
    for k, value in supplied.items():
        if artifact[k] != value: errors.append(violation("changed_fact", k, "不得修改明确输入或数据坐标"))
    pid = artifact["profile_id"]
    if not pid or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", pid) or not (PROFILE_DIR / f"{pid}.yaml").is_file():
        errors.append(violation("profile", "profile_id", "产品档案不存在"))
    if artifact["site_note"] != inputs['site'].get('site_note', inputs['site']['location_note']) or artifact["request"] != inputs["request"] or artifact["location_note"] != inputs["site"]["location_note"] or artifact["assumptions"] != assumptions:
        errors.append(violation("provenance", "brief", "来源、需求或假设说明被改写"))
    return errors


legacy_check = check


def inspect(a,c):
    from pipeline.quality import evaluate
    ref_codes={'profile','provenance','changed_fact'}
    return evaluate('L0',a,c,Brief,
                    lambda a,c:[v for v in legacy_check(a,c) if v.code in ref_codes],
                    lambda a,c:[v for v in legacy_check(a,c) if v.code not in ref_codes])


def check(a,c):
    from pipeline.quality import errors
    return errors(inspect(a,c))
