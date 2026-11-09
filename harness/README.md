# P2 Harness 控制层

依据主仓库 `.work/reports/product-bible.html` v1.0 第 03、04、05、06、10 节。
本目录、`contracts/harness.py` 和 `production/graph.py` 是独立数据合同与纯函数；尚未接入现有运行时。
Valvekr 文件仅作为只读参考，没有复制实现。

## Brief 和版本

`ChannelEntryBrief` 包含第 03 节全部字段，并补充 `project_id`、`brief_id`、`version` 和 `status`。
嵌套数据也使用 frozen / extra=forbid 模型；集合采用 tuple，可通过 Pydantic JSON 接口往返。
`exclusions`、`compliance_constraints`、`assumptions_and_unknowns` 的 `None` 表示未回答，空 tuple 表示明确确认没有。
假设和未知项均要求 `source`。产品档案版本指向外部档案；本控制层不验证档案文件内容。

```python
from harness.clarity import assess, request_input, approve, supersede, require_approved
from production.graph import compose
from harness.state_machine import initialize, start

# brief 是调用方从经理需求建立的 ChannelEntryBrief。
assessment = assess(brief)                  # 缺口和状态；不改变 brief
pending = request_input(brief)             # draft -> needs_user_input
# 调用方补齐 assessment.missing_fields 后再批准。
approved = approve(pending, approved_by="manager-id")
composition = compose(approved)
state = start(initialize(approved, composition))
```

批准必须匹配版本，缺口未补齐不能批准。`supersede(old, replacement)` 要求同项目、同 brief、递增版本，
以及 `replacement.supersedes == old.version`，返回 `(retired_old, replacement)`，旧快照状态为 `superseded`。
调用方应原子保存两份结果；本纯函数模块没有数据库或全局版本注册表。
缓存对象不会被就地修改：检查历史快照时必须把存储中的权威版本传入
`require_approved(snapshot, latest=authoritative)`；`compose`、`initialize` 同样支持 `latest`。
经理身份验证由未来调用方负责，这里验证批准字段与版本约束。

## 场景配置

| delivery_profile | 车间 | 交付 |
| --- | --- | --- |
| restaurant_entry | scope_clarity → scene_channel → territory → competition → terminal → distribution → outreach → delivery | 铺市作战包、市场进入决策书 |
| distributor_recruitment | scope_clarity → territory → distribution → outreach → delivery | 经销商招募清单、联络话术 |
| competition_inspection | scope_clarity → competition → delivery | 竞争态势简报 |

`PRODUCTION_GRAPH` 保存 Bible 的九车间完整目录，包括 `field_feedback`。
三个场景按第 04 节默认不加入可选回填。`SCENARIOS` 显式定义子图，不能静默跑全图。
子图末车间无 handoff；返工引用必须存在。

Bible 没有定义精简场景跳过车间后的返工细节，P2 配置选择如下，并保留在 `configuration_source` 和源码说明中：

- 经销商招募：territory 返工到 scope_clarity，distribution 返工到 territory。
- 竞品巡检：competition 返工到 scope_clarity，delivery 返工到 competition。
- 对应输入契约显式改为已批准 Brief 或所选上游输出，不依赖被跳过车间。

工具名仅是声明的能力白名单，不代表工具已注册。覆盖标准是完整性、证据或假设标注、输出契约检查，
不虚构业务数值阈值；本层不执行这些门禁，实际评审需调用方提供 `GateResult`。

## 状态推进与审计

`initialize` 检查批准 Brief 和场景的项目、版本、交付类型一致性；`start` 执行 ready → running。
`advance(state, outcome, next_node)` 只接受当前车间的结果：

- running + wait(gate)：进入 waiting_gate，交付物 candidate。
- waiting_gate + pass 且 GateResult=pass：普通车间按声明 handoff 交接；人工车间先冻结并进入 waiting_human。
- waiting_human + pass 且 approved_by 非空：批准冻结交付物，按声明交接；不能在同一步跳过人工等待。
- rework（含 GateResult=needs_evidence）：必须有原因，回到声明的 rework_node；旧状态和原因留在 ledger。
- running + wait(data)：保留当前车间；`resume(..., reason=...)` 恢复 running。人工和门禁等待不可用 resume 绕过。
- quarantine：保持 waiting_gate 和 candidate，不冻结、不交接；block：停止并记录原因。
- 末车间通过后 completed；中途 complete、未声明 handoff、错误 gate_id 都会被拒。

每个接受的操作追加不可变 `Transition`。非法操作抛 `TransitionError`，其 `.state` 带拒绝记录；
调用方应保存异常中的状态并从该状态重试，避免丢失失败历史。
GateResult 的默认判断来源为 `rules`；不生成 LLM 结果。此模块不写文件、不发送消息、不执行任务。

## 验证

仅运行新增的 `tests/test_harness_*.py`，当前只有 `tests/test_harness_contracts.py`。
Windows PowerShell 不为外部程序展开通配符，使用：

```powershell
$harnessTests = @(Get-ChildItem tests/test_harness_*.py | ForEach-Object FullName)
uv run pytest @harnessTests
```

本次执行通过环境变量将 uv 缓存、虚拟环境，以及 `--basetemp` 和日志放在主仓库 `.work/tmp/`。
运行时集成、门禁实际判定、版本入库与授权认证属于后续任务。
