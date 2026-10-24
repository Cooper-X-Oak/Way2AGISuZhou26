# 离线演示数据契约 v1.0.0

入口为 `index.json`。所有 JSON 是独立 UTF-8 文件，无需访问 `runs/`，无运行时 `$run_ref`。
大小限制使用十进制字节：单文件 ≤2,000,000，整个 `web/data/`（包括本文）≤15,000,000。
执行：`uv run python -m pipeline export --run runs/<run_id> --out web/data/<scenario>/`。
重复导出更新该场景和 index，并清理该场景不再使用的 trace；不重新计算评分或修改关卡。

## 公共字段与来源

每个 JSON 顶层有 `schema_version: "1.0.0"`、`generated_from`。
场景文件的 `generated_from = {run_id, files}`；`files` 是对应 run 入口文件相对路径 → 原始文件字节的 SHA-256。trace 文件只需 status.json 和 trace.json 两个入口，其余文件列出本轮导出读取的入口。
入口文件可含对象引用，导出时由 `pipeline.io.read()` 校验并展开；摘要指向原始入口文件，而不是导出后的自身哈希。
ledger 是 JSONL，逐行读其事件元数据，引用的检查和失败尝试仍通过 `pipeline.io.read()` 读取。
`index.generated_from.runs` 列出各场景的上述来源对象；各场景条目也带自己的 `generated_from`。
导出不增加生成时间，固定 run 重复导出内容稳定。未知字段省略，缺失产物列在 summary 中；未执行状态不会伪装成成功。

## index.json

`scenarios[] = {id, area_id, demo_replay, status, summary, generated_from}`。
`summary` 为相对 `web/data/` 的路径。场景为 shishan、guanqian、ligongdi、guanqian-compliance、backtrack。
`demo_replay` 为 null / compliance / backtrack。回退演示使用狮山输入。
本次五场景统一采用 README.md 运行示例的演示参数：周边 3 公里、2 个地推、4 周；不是对实际经营资源的确认。其余默认值和回退演示阈值来源保留在 summary.assumptions.params 及 history。

## summary.json

| 字段 | 含义 |
| --- | --- |
| scenario_id / area_id | 展示场景目录标识 / 原 run 商圈标识 |
| area | 原站点中心、坐标系、显示名、地理编码证据及定位限制 |
| product_profile | id、product（名称规格等）、scenes、selling_points、source；保留产品事实出处 |
| status | run 原始状态、run_id、完成关卡、阻断点、违规原因、回退次数与时间 |
| judged_by / demo_replay | 判断来源；离线为 rules，自动演示确认不等于人工验收 |
| conclusions | L0、L1、L2、L2C、L3、L3P、L4、PACK 各关一句话；未运行可为 null |
| checks | 每关五个 `{layer,status}`；见下方状态定义 |
| counts.grades / channels | 终端 A/B/C 数量（含零）/ 按 channel_id 分组数量 |
| counts.zones / selected_zones | 全部 500m 网格数量 / 选中网格数量；不是行政商圈数量 |
| counts.competition_intensity | 选中网格按低/中/高分组数量；缺少竞争检查不计为低 |
| counts.partners / recommended_partners | 市场伙伴点数 / 已计算为优先的伙伴数 |
| counts.recruitment_gaps | PACK 招商缺口终端数；未产出 PACK 为 null |
| counts.review_queue | 已验收各关 check.review_queue 行数之和，含 PACK 自身行，不做去重 |
| counts.terminals / pack_terminals | 最新终端评估数量 / 已验收作战清单数量 |
| terminal_count_source | PACK、已验收的 L3 或未验收的 L3 最新尝试；页面必须连同状态展示 |
| assumptions | brief 假设、原 params（含全部参数来源）；不把演示阈值当自然事实 |
| missing_artifacts | 源 run 缺失文件名；阻断后未运行的关卡也在此列出 |
| route_date_note | 日期缺失说明；禁止以运行日期推断拜访日期 |

L0 源产物没有 decision：导出以 check.passed 生成结论，并在 stages 的 `decision_source` 标注规则。

## stages.json

`stages[]` 按 L0 → L1 → L2 → L2C → L3 → L3P → L4 → PACK 排列。
每关含 `stage, available, accepted, decision, items, check, history` 及原始 judged_by/confidence/evidence/open（存在时）。
`available` 代表曾产生该关产物，`accepted` 只依据最终 status.completed_stages；回退留下的旧产物可能 available=true、accepted=false。
L0 的 `brief` 保存输入解析、位置说明、假设和待澄清问题。其他关 items 的展示字段如下：

| 关卡 | items 字段 |
| --- | --- |
| L1 | channel_id, scene_ids, selling_point_ids, priority, reason, commercial_conditions |
| L2 | zone_id, tier, type, counts, rank, selected |
| L2C | zone_id, counts, intensity, strategy, reason |
| L3 / PACK | poi_id, channel_id, zone_id, score, grade, next_action, reason, score_components |
| L3P | partner_id, type, recommendation, coverage_count, covered_terminal_ids, recommended_zone_ids, reason |
| L4 | target, pitch, table_card, selling_point_ids, compliance |

各条目还保留 evidence、judged_by、confidence；重复假设通过 `open_ref` 指向**本关** `item_notes[open_ref]` 字符串数组。
`L4.target` 是 POI ID 或未覆盖业态 channel_id；`compliance={passed,hits}`。竞争强度是点位代理指标，不是销量或真实市场份额。
`check` 是完整五层报告：`stage,passed,layers,review_queue,violations`；未检查为 null。
五层为 structure / references / business / judgment_quality / human_gate；源状态为 passed / failed / blocked / needs_review。
summary 对尚无检查的关卡额外用 not_run。needs_review 不是自动失败；human_gate.approved_by 的 `auto (demo)` 不等于人工确认。

`history[]` 保存该关每次尝试（包括通过的重试）、演示配置及回退事件，保持 ledger 顺序。
尝试有 revision、attempt、status、violations、check、retry、seeded_for_demo、approved_by、judged_by 和耗时/时间。
失败合规物料另有 `failed_items`，保留原违规话术、命中规则和来源，仅用于回放，不用于投放。
回退记录归在来源关 L3，`backtrack={from,to,reason,zone_ids}`，同时保留 count、status、seeded_for_demo。
`demo_configuration` 保留 parameter/before/after。未出现的可选事件字段不会补造。

## map.json

所有点和网格都在 GCJ-02 坐标系；`site` 是原始参考中心，不保证为配送门店。
`grids[]` 含 zone_id、tier、type、counts、rank、selected、intensity、strategy 和 GeoJSON `geometry`。
geometry 为 Polygon，坐标顺序 **[经度, 纬度]**，闭合五点环。按 pipeline.geo 的 500m 网格逆算，球面局部近似，不是测绘或道路边界。
`terminals[]` 含原 run 的**全部终端来源 POI**，以保证早期失败尝试和回退的 POI 也可定位。
点基本字段：poi_id、name、lat、lon、category、coordinate_system，以及源数据存在的 source/brands。
有最新 L3 评估的点额外含 channel_id、zone_id、score、grade、next_action、reason、score_components。
未评估的点没有等级分数字段；不得当作 C。`suggested_supplier` 为供货建议对象或 null。
`competitors[]` 为品牌关键词竞品点；`availability[]` 为饮品零售可得性代理点；不能混作实测在售品牌。
`partners[]` 为全部候选伙伴点；若 L3P 已执行，追加 partner_id、type、recommendation、coverage_count、recommended_zone_ids；完整覆盖终端列表和判断依据查 stages.L3P。不同点数组可含同一物理 POI。
`coverage,open` 保留市场采样限制及待核实说明。伙伴资质、授权、能力仍待核实。

## terminals.json

`available` 仅当 PACK 在最终完成列表中为 true；否则 `items=[]`，不从失败尝试伪造作战包。
`items[]` 保留 PACK 的终端字段、判断来源、假设和证据，追加 name、route、suggested_supplier、pitch_ref、trace_ref。
`route={week,day,person,order,date,route_distance_m}`：order 从 1 开始；week/day 是计划周次与工作日序号；date 固定 null（源数据无起始日期）。
未排入路线为 null；C 级和超容量 A/B 不虚构日期。`days` 保留原路线及 poi_ids；`backlog_ids` 为超容量 A/B。
`suggested_supplier={partner_id,status,zone_id,distance_m}` 或 null；partner_id=null 表示需新招经销商，不能与“未计算”混同。
`pitch_ref={file:"stages.json",stage:"L4",target}`，通过 target 查找物料；无有效物料为 null。
`trace_ref` 只在 A 级存在，其余为 null。`recruitment_gaps` 含 terminal_count/terminal_ids/zone_ids，未运行为 null。
`planning_note` 保留容量假设、球面距离与路线排序限制。

## trace/<编码后的 poi_id>.json

只导出已验收 PACK 的 A 级终端。Windows 不允许文件名冒号，因此 poi_id 用 URL 百分号编码构成**磁盘文件名**，例如 `amap%3AB...json`。
推荐按 terminals.trace_ref 查找，不自行拼接；通过 HTTP 请求时应逐路径段再次 encodeURIComponent，确保服务器定位字面 `%3A` 文件名。
顶层 `poi_id,trace`；trace 原样保留 terminal、score_components、zone、channel、scenes、selling_points、competition、supply。
每个节点包含 data、judged_by、check；源锚点含 source/file/quote/offset/line/sha256 与 exact-substring 检查。
trace.check.path 是原 run 证据路径，供溯源显示；网页检查详情用 stages 中相同 stage 的 check，不请求 run。
产品原文中的 human 指用户提供资料来源，不表示本轮人工审查。离线判断仍为 rules。

## 隐私与错误处理

导出使用展示字段白名单，并递归去除联系方式、密钥类键；识别出的手机号、邮箱及带标识的密钥文本替换为 `[redacted]`。
不导出原始 tags、环境变量或源文档全文。输入未知坐标系、引用缺失/哈希错误、悬空 POI、大小超限时拒绝导出。
调用者应串行导出不同场景，避免共享 index 的并发写冲突。
