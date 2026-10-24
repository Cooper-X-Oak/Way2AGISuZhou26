"""Product differences are data, never branches on profile id in the engine."""
from pathlib import Path
import re
import yaml

PROFILE_DIR = Path(__file__).resolve().parent.parent / "profiles"


def load(profile_id, directory=PROFILE_DIR):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", profile_id):
        raise ValueError("产品档案 id 格式不合法")
    path = directory / f"{profile_id}.yaml"
    if not path.is_file():
        raise ValueError(f"产品档案不存在: {profile_id}")
    profile = yaml.safe_load(path.read_text(encoding="utf-8"))
    if profile["id"] != profile_id:
        raise ValueError("档案 id 与文件名不一致")
    for key in ("scenes", "selling_points", "channels", "compliance_rules"):
        ids = [row["id"] for row in profile[key]]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError(f"档案 {key} 必须非空且 id 唯一")
    scenes = {s["id"] for s in profile["scenes"]}
    for point in profile['selling_points']:
        if not point.get('scene_ids') or not set(point['scene_ids']) <= scenes:
            raise ValueError('卖点必须引用存在的适用场景')
    for channel in profile["channels"]:
        if not channel["scene_ids"] or not set(channel["scene_ids"]) <= scenes:
            raise ValueError("渠道假设引用了不存在的场景")
    competitors=profile.get('competitors',[])
    if len({r['brand'] for r in competitors})!=len(competitors) or any(not r.get('source') or r.get('category') not in ('瓶装水','苏打水','送水站') for r in competitors):
        raise ValueError('竞品必须有唯一品牌、品类和来源')
    profile['compliance_rules'].append(dict(id='competitor-naming',terms=[r['brand'] for r in competitors],
        source='项目规则：不得点名竞品；法律依据为《广告法》第十三条禁止贬低其他生产经营者的商品或者服务，非禁止所有比较广告；https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_5474cf75173c45d6a0379730fb4e8d97.html'))
    profile['compliance_rules'].append(dict(id='competitor-denigration',terms=['竞品垃圾','别家水质差','其他品牌不安全','不如我们','劣质竞品'],
        source='《广告法》第十三条；项目保守词表，非法律穷举；https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_5474cf75173c45d6a0379730fb4e8d97.html'))
    return profile


def parameters():
    return yaml.safe_load(Path(__file__).with_name("params.yaml").read_text(encoding="utf-8"))
