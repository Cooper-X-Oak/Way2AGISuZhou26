# Product Bible

- 一句话：把新品铺市从拍脑袋变成流水线。
- 展开：新品进新渠道，不再靠扫街拍脑袋——给一个产品和一块区域，Agent 一关一关推出「先进哪些店、进去怎么说」，每个结论都有证据，每句话术都过合规。
- 用户：快消品牌/经销商的区域渠道经理（市场方案端，第一阶段）；一线业务员（业务员端：快速话术 + AI 生图，第二阶段）。
- 四问：卖给谁 → 去哪里 → 进哪家 → 怎么说 → 铺市作战包（终端清单 + 路线 + 合规话术 + 证据链）。
- 不做：销量预测、费用政策、个人联系方式、多城市。

﻿# 快消新品市场进入 Agent v2

面向区域渠道经理，用产品档案回答“卖给谁 → 去哪里 → 进哪家 → 怎么说”，输出可汇报、可执行的铺市作战包。产品差异保存在 `profiles/<id>.yaml`；引擎没有按品牌 id 分支。当前开发分支 `feat/pipeline`，Python 3.11+，uv；只提供数据、Python 与 JSON，不做 UI 或部署。

## 运行

```powershell
uv run python -m pipeline run --area shishan --offline --auto-approve --profile jiangnan-gongquan --input "想把江南贡泉铺进苏州高新区周边3公里的餐馆，2个地推，做4周" -o .work/reports/main.json
uv run python -m pipeline run --offline --auto-approve --profile jiangnan-gongquan --demo-replay compliance --input "苏州高新区周边3公里，2个地推，做4周" -o .work/reports/compliance.json
uv run python -m pipeline run --offline --auto-approve --profile jiangnan-gongquan --demo-replay backtrack --input "苏州高新区周边3公里，2个地推，做4周" -o .work/reports/backtrack.json
uv run python -m pipeline run --offline --auto-approve --profile xinxian-huoquan --input "苏州高新区周边3公里，2个地推，做4周" -o .work/reports/reuse.json
uv run pytest
```

`--offline` 只表示判断采用确定性规则，所有判断标 `rules`。`run` 只读缓存，设置 key 也不会调用高德；`--area shishan|guanqian|ligongdi` 读取对应高德 GCJ-02 快照。不指定 area 保留 OSM 缓存模式。真实判断模式需要 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`；调用失败会留下失败尝试，不偷偷切换规则。在线判断标 `llm`，PACK 拼装与算分由代码完成。测试 mock 不代表真实 LLM 或高德调用。

去掉 `--auto-approve` 后，L2/L3 将先保存 `pending/<stage>-r<revision>.json`，等待终端输入 `yes`；拒绝或没有 stdin 则停止。自动通过在 ledger 明确标 `approved_by: "auto (demo)"`，交互确认才标 `human`。低置信度只进复核队列，不自动判为错误。

## 六关与交付文件

| 关卡 | 问题 | 交付文件 | 固定检查 |
| --- | --- | --- | --- |
| L0 | 立项 | brief.json | 必填、档案存在、0＜半径≤3000米；缺项进 questions；明确输入不可改 |
| L1 | 卖给谁 | channels.json（A1） | 场景覆盖或有理由排除；P1/P2/P3/excluded；卖点与场景引用；禁用词；商业条件未知即 null 并进 open |
| L2 | 去哪里 | zones.json（A2） | 500米网格；商圈六类与层级三类枚举；只统计 P1/P2；POI 重算计数与排名；选中数量上限；人工关口 |
| L2C | 对手在哪 | competition.json（A2C） | 缓存竞品按品牌/网格重算、强度阈值假设、补位/正面/避让；L2 pending先提供计数与强度摘要；五层检查、重试与ledger |
| L3 | 进哪家 | terminals.json（A3） | POI/业态/选中商圈引用；分数、等级、动作重算；理由只用已知字段；禁止联系方式；人工定稿 |
| L3P | 谁来供货 | partners.json（A3P） | 5公里内候选、名称/类型规则、3公里A/B覆盖重算、推荐等级；资质待核实；五层检查、重试与ledger |
| L4 | 怎么说 | materials.json（A4） | 按网格切入策略生成A/B话术；不得点名或贬低竞品；老板视角、卖点原文及合规扫描；命中即重写 |
| PACK | 作战包 | pack.json、terminals.csv、trace.json | 上游重新验收、原文锚点、路线与容量；supply_assignments按等级/距离推荐供货方，recruitment_gaps汇总缺口；trace新增竞争强度→供货方 |

A1–A4 的外壳包含 `decision/items/evidence/judged_by/confidence/open`；每项也有独立的证据、判断来源、置信度和带来源的待验证项。证据使用 `profile:`、`profile_channel:`、`scene:`、`selling_point:`、`poi:`、`L1:`…前缀，以及 `params.<key>`，只能指向已知档案、POI、上游或参数。

每关提供 `run(context)`、`check(artifact, context)` 和 `inspect(artifact, context)`；后者返回五层详情：结构 → 引用 → 业务 → 判断质量 → 人工关口。结构或引用断裂会阻断依赖检查，不伪装已通过。`review_queue` 是低置信度复核清单；`needs_review` 不等于判断已被否定。非 L2/L3 的人工层记录 `required: false`。

每关每次进入最多尝试3次；下次判断收到 `previous` 和 `violations`。每次候选及检查均保存在 `attempts/`、`checks/`，ledger 引用精确路径，回退后 revision 递增，不覆盖旧尝试。失败候选也保留，所以交付验收应读取 `status.json` 和 check，不能只看某个产物文件存在。

## 公式、回退与演示

参数和假设来源在 `pipeline/params.yaml`。商圈按 `sum(count[channel] × priority_weights[priority])` 降序排列，同分按 zone_id 稳定排序；选择前4个非空且没有被回退排除的网格。层级暂按社区级规则假设，写入 open，需实地核实。

终端分数为四项加权和：渠道权重×0.4 + 商圈排名倒数×0.2 + `1/(1+距中心米数/半径米数)`×0.25 + 资料完整度×0.15。完整度检查 name/category/lat/lon 四项。分量和分数保留8位小数；每个run内按score降序、distance_m（中心距离保留2位小数）升序、poi_id升序排列；A前20%，B接下来30%，C其余，累计边界向上取整（ceil(N×0.2)、ceil(N×0.5)）；对应“首访-带样品和桌牌 / 首访-了解需求 / 暂缓”。这些是排序假设，不是销量预测。已删除容量/瓶数估算和转化率 KPI。

L3 发现任一选中商圈 A+B 数少于 `min_terminals_per_zone`，写入 `backtrack{from,to,reason,zone_ids}`，返回 L2 排除这些商圈、按排名补选，再跑 L3。阈值保持不变；最多回退两次，第三次请求停在 L2。候选耗尽则 L2 阻断，不生成空的成功作战包。

`compliance` 在 L4 首稿显式注入“最解腻、护血管”，标记 `seeded_for_demo`；扫描拦下后反馈重写。`backtrack` 先检查是否自然触发；未触发时，把最低终端数设为首轮选中商圈中最小 A+B 数+1，记录演示配置事件与参数来源，后续不再降低。数据如果不足以完成演示，仍按正常关卡停止。

路线按 A 优先、同级就近分组；默认每天每人20访、每周5天均为待确认的执行假设，可在输入写“每人每天进店12”。每天从示例点出发，用球面距离，不是道路导航；每终端首次拜访一次。路线只安排 A/B 终端，清单保留全部等级；有限执行容量下未排入的终端仍留在清单。

## 证据与来源

每次 run 保存 `inputs.json`（含原始档案 source 文本快照）、`profile.json`、`pois.json`、`coverage.json` 和有效 `params.json`。trace 按 poi_id 索引，每条链依次包含 terminal、score_components、zone、channel、scenes/selling_points；各环带判断来源与关卡检查。原文锚点包含 source 标识、精确 quote、字符 offset、行号及文件 SHA256，可脱离之后的档案编辑验证。source 中的 `human` 表示用户提供的原文来源，不表示该 run 已人工批准。

两份档案引用 `.work/handbook/dongting-brief.md`。卖点—场景适用映射是明确标来源的项目假设；未从图片猜测包装规格，毛利/价格带/首单政策全部未知。产品事实不等于渠道效果已验证，所有候选仍需走访。禁用词表是档案红线与项目扩展规则，不声称法律穷举。

数据按 `--area` 选择独立快照，不混用坐标系。高德缓存原生 GCJ-02；未指定商圈时使用 OSM WGS84。OSM 来源与抓取时间见 `data/raw/provenance.json`；中心是 `data/site.json` 的落星桥附近示例点，非经销商真实门店，写入 brief 的 assumptions。相同名称且50米内的跨源候选保守去重；一个 POI 按档案候选顺序只归一个业态。无名称保留缺失，不将园区/办公建筑伪造为公司。OSM覆盖不全、网格没有点不等于现实没有商户。

高德预算 `params.budget.amap_calls` 默认80，在每次请求前检查硬上限，包含失败请求。达到预算停止并在 coverage 标 `truncated: true` 和“截断”。不请求电话或 business 字段，响应出现联系方式即拒绝落盘；请求密钥不进日志。分页服务上限、重复页或错误会保存脱敏失败 manifest，不替换现有数据。可选补充采集放 `.work/tmp/amap-<id>/`，正式 run 保存其 manifest 路径与合并数据；GCJ02 转 WGS84 是近似换算。

## 共享 harness

任务 `.work/tasks/` 不修改；报告 `.work/reports/`；临时脚本、备份、缓存、每次独立 pytest 目录放 `.work/tmp/`。历史 `runs/` 全部保留。v2 前代码备份在 `.work/tmp/market-entry-v2-baseline/`，旧数据示例文件不再加载为产品档案。本轮不提交既有未提交工作，不 push，不部署，不抓个人联系方式。

数据 © OpenStreetMap contributors，许可信息见 `data/raw/provenance.json`。

## 高德商圈缓存

```powershell
uv run python -m pipeline fetch-amap --area shishan -o .work/reports/amap-fetch/fetch-shishan.json
uv run python -m pipeline run --area guanqian --offline --auto-approve --input "观前街周边3公里，2个地推，做4周" -o .work/reports/guanqian.json
```

抓取从根目录 `.env` 读取 `AMAP_KEY`，HTTP 客户端 `trust_env=False` 直连；key 不写日志。地理编码候选和 POI 原始响应保存在 `data/raw/amap/<capture-id>/`，仅保留基础字段白名单，丢弃所有联系方式。请求参数中的 key 为 `[REDACTED]`。地理编码优先选择唯一兴趣点；否则同名同级别按 API 返回顺序取首项作为参考中心；不是配送门店或比赛场地精确定位。

每商圈半径3000米，每类型最多8页、每页25条（项目保守采样上限），达到上限即标截断，继续抓下一类型；重复页也标截断。`coverage.json` 保存类型级截断原因、互斥业态计数和抓取时间，不声称商户全量。分类是 rules：海鲜、火锅、综合酒楼按官方类型码映射；快餐映射简餐是项目假设；050118为特色地方风味，烧烤/夜宵由名称规则识别，不能据此验证营业时段。

默认单次预算来自 `params.budget.amap_calls=80`，`fetch-amap --amap-calls N` 可显式覆盖并记录在 manifest。搜索请求按400ms最小间隔串行执行；`data/raw/amap/usage.json` 按北京时间月份累计，每月5000次、本任务累计400次。余额不足本次预算则在请求前拒绝。计数在发请求前持久化，网络失败也计数；地理编码另记请求但不计搜索额度。文件锁阻止并行抓取。该账本只记录本项目调用，无法得知控制台其他应用消耗，不能当作官方剩余额度。演示回放不会消耗搜索额度。

接口和类型码来源：[POI 2.0](https://lbs.amap.com/api/webservice/guide/api-advanced/newpoisearch)、[地理编码](https://lbs.amap.com/api/webservice/guide/api/georegeo)、[官方类型码表](https://a.amap.com/lbs/static/code_resource/Amap_poicode.zip)。

真实店名原文保留在 pois.json；A3 理由引用 POI ID、业态及排名，避免把店名中的宣传词重复传播到理由中。禁用词、评分和关卡阈值均保持不变。

## 分级修正与补抓

比例来自 params.scoring.grade_shares，标注项目排序假设。restaurant_other 在 A1 最高P3，不作为场景匹配证据；仅在已选商圈保留为待核实终端，A3封顶C且动作固定为「首访-了解需求」，封顶后不补位。其他终端 A/B/C 对应带样品和桌牌/了解需求/暂缓。高德type明确子类型优先，再类型码、档案名称规则；映射见 params.amap_classification。

PACK仅安排A+B；每人每天首次拜访不超过 params.first_visits_per_day=10（项目假设），每周5天，并遵守用户给定的更低日容量。未排入的A+B完整保存在backlog，C保存在items。检查层重新计算等级、路线和backlog。

`uv run python -m pipeline supplement-amap -o .work/reports/grading-fix/supplement.json` 仅补三个商圈coverage标截断的类型。原3000米圆外接矩形四等分，调用polygon后过滤回原圆；按ID去重，保留原缓存和脱敏请求ledger。每格每类型最多8页，各格轮流分页。本任务grading-fix累计硬限制150搜索调用（含失败），重启共享usage.json余额，达到上限即停止并保留truncated及具体原因。接口不保证普查全量。补抓前快照存data/raw/amap/grading-*/before.json，历史runs不修改。
