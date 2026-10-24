# 数据库底座（#17）

以主仓库 `.work/reports/system-design.html` v1.1 为依据。正文 A–E 实际列出
25 张表，全部实现；标题中的“21 张”是计数差异。`area_poi` 按设计使用
`(area_id, poi_id)` 复合主键，其余实体使用 26 字符 ULID。无登录、HTTP
接口或运行时接线，现有 `pipeline/`、`server/` 不受此变更影响。

## 初始化与重建

在本 worktree 根目录运行：

```powershell
uv run alembic upgrade head
uv run python scripts/import_legacy.py --work-dir '<主仓库>/.work' -o '<主仓库>/.work/reports/db-foundation-import.json'
uv run pytest --basetemp='<主仓库>/.work/tmp/<本次测试唯一目录>'
```

未传 `--work-dir` 时，通过 `git rev-parse --git-common-dir` 定位主仓库。
默认数据库和 blob 位于本 worktree 的 `data/app.db`、`data/blobs/`，均忽略提交。
`APP_DB_URL` 同时影响 Alembic 和应用引擎；导入脚本还支持 `--db-url` 和
`--blobs-dir`。自定义数据库与 blob 路径应配套保存。只改变数据库 URL 不会
改变默认 blob 目录。SQLite 每个连接开启 WAL、外键和 5 秒忙等待。

## 使用仓储

```python
from app.db import make_engine, session_factory
from app.repo import AreaRepository, RunRepository

engine = make_engine()
with session_factory(engine)() as session, session.begin():
    areas = AreaRepository(session).areas()
    pois = AreaRepository(session).pois(areas[0].id, role="terminal", limit=50)
    runs = RunRepository(session).runs(status="completed", area=areas[0].id)
engine.dispose()
```

五个 Repository 分别管理配置、产品、区域、运行、执行领域。调用者拥有
Session 和事务；`add()` flush 但不 commit，便于业务写入与事件在同一事务提交。
运行时事件序号分配属于后续接线工作，本任务仅通过唯一索引阻止重复 seq。

商户、产品、运行使用 ULID 升序游标，传上一页最后一项的 `id`；事件按
`seq > after_seq` 读取。终端支持 `score_desc`（默认）、`score_asc`、`grade`、
`channel`、`cell`、`id`，同值按 ID 升序；游标仍为最后一项 ID，跨页保持
筛选和排序不变。终端投影应在完成运行后保持不变。每页 1–1000 项。
分类查询默认选该商户各角色的最大 `rule_version`，明确传版本可查询历史。
网格查询默认选该区域最大规则版本。分类和尝试记录追加保存，不覆盖旧记录。

## 导入口径与来源

- 两个 YAML 档案整体保存为 source_doc/blob；brief 原始字节单独保存。
  产品信息、卖点、场景、红线、竞品和渠道假设逐条保存 profile_fact。
  `text` 保留来源、假设和 `quote_status`；不把来源元数据塞进未设计的 JSON 列。
- `quote_offset` 是 UTF-8 解码后的 Unicode 字符偏移，CRLF 原样保留。
  只有源自 brief 且逐字查到的内容才写入 quote/offset；否则两者为 NULL。
  报告区分 `not_found`（声明来自 brief 但未查到）与 `not_brief`（外部规则、
  竞品资料等没有 brief 引用）。报告的失败总数包括这两类候选引用；没有引用
  候选的渠道/物料假设仍标 `not_brief`，不计作回查失败。
- legacy v1 档案标 `created_by=human` 表示导入用户提供的既有资料，产品保持
  draft；它不表示人工审批或 LLM 新抽取。没有来源的 category_noun 保留 NULL。
- 参数集数据库版本为 1；`content.version` 保留原 YAML 的版本，所有来源与
  假设原样保留。合规规则包含现有 `pipeline.profiles.load()` 添加的两条竞品
  规则；项目保守混合禁词组归 efficacy，原 terms/source 不拆改。不虚构 brand 规则。
- 原始和失败抓取全部保留；多区域/多类型 manifest 按区域及抓取 kind 拆为
  capture。`calls` 是该分组的实际次数；budget 是原 manifest 的共享预算，
  不能把这些行的预算相加。完整 manifest、响应、ledger 各自进入 blob；
  `raw_blob_sha` 指向包含文件名→sha256 的索引 blob，可逐个校验还原。
- POI 按 `(source, source_id)` 去重；关联区域各自保留距离和 cell_key。
  首次抓取从原始响应时间回查，最新字段取最新观测；同一 POI 可有多个角色。
  `partner_type=其他` 的候选保留原分类，不表示可供货。
- 业态与伙伴类型复用现有确定性函数，judged_by=rules；不调用网络或 LLM。
  网格为 500m，中心计算沿用现有 EARTH_M 与网格定义；counts 为区域半径内
  按 POI 去重的类别/渠道数量，availability 为品牌/零售代理点位去重计数。
  5km 伙伴候选仍保留 area_poi，超出区域半径的点不进入网格特征计数。
- 同样输入重复导入不增加行或 blob。v1 档案、brief、参数与合规词表改变时
  拒绝静默改写，需显式建立新版本；此脚本是 legacy 迁移入口，不替代增量采集。
  所有数据库导入在一个事务中完成；失败可留下内容寻址 blob，重试安全复用。
  不迁移旧运行目录。

## 类型与迁移

JSON 仅用于设计明确标注的字段；筛选、排序和关联使用正式列及索引。
SQLite 读出的时间恢复 UTC，写入拒绝无时区 datetime。初始迁移是独立的
固定 DDL，不从运行时模型动态建表；PostgreSQL 使用标准类型，并延后创建
product/current_version 的循环外键。测试包含 PostgreSQL 离线 DDL 编译；
未以此声称已通过真实 PostgreSQL 服务验证。
