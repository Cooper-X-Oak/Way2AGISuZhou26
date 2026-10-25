# 独立打磨话术

话术现在按「商户资料整理 → 撰写 → 检查」执行，原流水线 L4 调用同一个工作流。
尚未接入网页搜索。实况任务的「怎么说」由写作 Agent 完成（`agent_l4`），回放与离线仍用模板基线。

## 改哪里

| 要打磨的内容 | 文件（相对项目根目录） |
| --- | --- |
| 资料整理、开场/切入点/需求问题/下一步写法、自查与完成标准（唯一业务技能） | `skills/pitch-writing/SKILL.md` |
| 实况任务的写作 Agent（逐条自查、全部达标才交稿） | `pipeline/l4_agent.py` |
| 资料装配与后续搜索接入位置 | `pipeline/workflows/pitch/research.py` |
| 模型调用与规则模板基线 | `pipeline/workflows/pitch/writing.py` |
| 程序检查（规则不因技能文档变化而自动改变） | `pipeline/workflows/pitch/review.py` |
| 调度与中间产物保存 | `pipeline/workflows/pitch/__init__.py` |

技能文件每次运行读取并保存当次快照。实况任务由写作 Agent 按技能逐条写稿并用正式检查自查，全部达标才交稿；离线模式使用原有模板，不执行模型指令。
资料整理和程序检查是确定性 Python 步骤。

输入复用同一任务的 brief、任务文档、产品档案、POI、业态、网格竞争策略及终端名单。
商户事实与上游判断分开保留；只有业态资料时标记 `channel_fallback`，不能冒充商户调研。

## 只重做一家店

在项目根目录运行（替换原任务目录与目标 ID）：

```powershell
uv run python -m pipeline.workflows.pitch --run <原任务目录> --target <poi_id> --output .work/tmp/pitch-check-01 --offline
```

真实模型运行使用同一命令，把 `--offline` 换成 `--live`，并通过 `uv run --env-file <本机配置文件>` 加载配置。
不提供任何模式会报错，避免把模板当模型。`--target` 可重复，不传则生成全部应有目标。
只接受上游选定的 A/B 终端或未覆盖业态。输出目录必须不存在，原任务保持不变。
模型或检查失败会保留失败记录；此独立入口单次执行、不自动重试，修改后换一个输出目录再跑。
整条流水线沿用原有重试和 ledger。

## 每次留下什么

```text
输出目录/
  source.json          原任务与选择范围（独立入口）
  materials.json       话术产物，是否合格以 review 为准
  review.json          五层检查报告
  pitch/<执行时间>/
    context.json       共享背景、每家商户资料、来源、未知项
    skills.json        当次技能原文
    draft.json         当次话术，包括被检查拒绝的草稿
    review.json        检查结果与失败原因
    status.json        通过/失败、rules/llm 来源；调用异常仅保存异常类型
```

正常流水线也会在原 run 下生成 `pitch/<执行时间>/`。模型调用失败时没有草稿，保留 context、skills 和失败 status。
这些中间产物目前保存在后端文件中，尚未新增前端展示或「单店重写」按钮。

## 怎么评价业务效果

先检查文案是否解释为何拜访这家店、是否提出与它相关的问题、是否给出可执行的下一步；再看产品事实与商业条件有没有被编造。
程序通过只能证明满足现有检查，不能证明个性化质量或成交效果。离线模板只用作回归基线。
后续网页搜索应接在资料整理步骤，产出商户匹配依据、URL、采集时间与事实摘要。搜索无结果时保留未知项并继续使用已有事实。

## 本次验证样例

使用已有真实任务 `cc2b5f8c2f1e4be994431cb9a40b102d` 的目标 `amap:B02000IVEO`（金狮大厦，滨河路）独立复跑。
本地输出位于 `.work/tmp/pitch-extraction-smoke-01/`，模式为 rules，只有一个目标。
结构、引用、商业规则检查通过；置信度检查保留 `needs_review`，不能视为业务效果通过。
实际草稿仍是业态模板，本次没有执行真实模型或 Web Search。自动化测试覆盖模型输入传递，未验证在线模型文案质量。
样例产物来自本机历史任务，不提交其中的私人会话；命令可以用于任意已保存的完整任务。

本次全量验证：`uv run pytest -q`，291 passed（482.18 秒）；1 条 Starlette/httpx 弃用提示。三个 SKILL.md 均通过 skill-creator 格式校验。
