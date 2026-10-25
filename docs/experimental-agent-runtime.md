# 实验：全程 Agent 循环（分支 `exp/agent-runtime`）

参照拓哥（signalops-runtime-distribution）的结构：模型驱动流程，业务方法写在技能里，状态机和完成口径由代码保证。

## 分工

| 职责 | 由谁承担 | 位置 |
|---|---|---|
| 决定下一步、读哪份技能、给每关的指引、何时回退或提问 | 任务 Agent（模型） | `pipeline/runtime.py`，系统提示来自 `runtime/SOUL.md`、`runtime/RUNTIME.md`、`skills/pushi-runtime` |
| 每关的业务判断方法 | 技能（Markdown） | `skills/<name>/SKILL.md`，按 `stage` 注入该关的模型上下文 |
| 读取资料、查商户、高德补采 | 工具 | 「找终端」「怎么说」的工具循环 |
| 距离、数量、分数、等级、路线 | Python | `pipeline/l*.py` 的计算部分 |
| 商户存在、引用可追溯、计算复算、硬约束 | 校验代码 | 各关 `inspect` / `quality.evaluate` |
| 任务、产物、失败、续跑、前端 | 运行与存储层 | `pipeline/engine.Run`、`server/` |

## 状态机门禁（代码强制）

- 只能运行下一关；重做已完成的关卡必须 `backtrack`（每个 run 最多 3 次）；
- 每关产物过代码检查才进入下游，未通过自动带问题重试，最多 3 次，检查不放宽；
- 「进哪家」门店不足时代码自动回退到「去哪里」并排除网格，阈值不降低；
- 只有九关全过并成功调用 `finish` 才是 completed；Agent 自称完成会被打回。

## 运行

- 本分支的实况任务默认走全程 Agent；设置 `PUSHI_AGENT_RUNTIME=0` 回到固定顺序；
- 回放模式仍用固定顺序（录制里没有任务 Agent 的模型调用）；
- 测试：`uv run pytest tests/test_runtime_agent.py tests/test_l4_agent.py`（脚本模型，不代表真实模型效果）。

## 已知限制（下一步）

1. 「对手」「进哪家」「供货」三关的检查要求模型输出**等于**代码给定的策略或理由（`allowed_strategies`、`allowed_reason`）。这是「等于固定规则答案」式检查，模型实际上没有判断空间。应改为：模型给出依据和不确定性，代码只校验商户存在、引用可追溯、数字正确和硬约束。
2. 各关的输出契约（字段要求）仍写在代码里，这是合理的；但 `decide()` 的提示词片段还有一部分业务描述在代码里，应继续迁到技能。
3. 「找终端」「怎么说」以外的关卡仍是「单次判断」，不是工具循环；需要时再给它们工具。
4. 真实模型下的步数、耗时和稳定性未验证：需要在配置了 key 的机器上跑一次实况。
