# Agent 决定与组装运行时

`production/line.yaml` 对每个车间声明 `decision_schema`、`assembler`、完整输出契约、五层检查、技能、工具、步数和 token 预算。单次会话使用同一个 Agent。

## 决定范围

| 车间 | 模型提交 | 程序负责 |
|---|---|---|
| L0 | brief 字段、必要追问 | 容量、原始需求、来源、默认假设 |
| L1 | ≤10 个渠道的优先级与理由 | 档案场景与卖点映射、商业条件缺项 |
| L2 | ≤12 个候选中的类型、层级、选择与理由 | 全网格计数和排名、未复核行 rules 来源 |
| L2C | 选中商圈的强度、切入策略与理由 | 完整竞争代理指标清单和计数原文 |
| L3 | A 级前15家和名称提示需复核的其他餐饮 | 全终端重算分级、标准理由、原始 POI 保留 |
| L3P | 每商圈 ≤3 个伙伴与理由 | 分类政策、几何覆盖、完整候选清单 |
| L4 | 业态×策略的 ≤8 份话术模板 | 展开到终端、合规扫描 |
| PACK | ≤10 条带引用的决策书要点 | 路线、全部上游、溯源、供货建议、缺口 |

现行验收政策仍要求按排名选商圈、按竞争配置选择策略、按贪心覆盖推荐伙伴；模型不能任意越过这些约束。工具用 `required_*` 明示这些约束，决定与它们不符仍打回。

工具先 `load_skill`、`decision_data`，有 `next_cursor` 时用 `read_result(cursor=...)` 翻页；返回的 records 包含原数据 path，长字符串保留 string_offset。所有原始结果保存在当前车间的本地引用中。`save_decisions(items=...)` 可保存跨页判断（同编号替换），最后 `submit_artifact(payload={"use_saved":true})`；也可直接提交完整的小规模决定。L0直接提交fields/questions，PACK直接提交items。

## 出处与检查

- `seen_references` 只登记实际展示过的编号，不把被截断的后台完整数据视为已读。显式 refs 及 POI、cell、渠道、卖点、模板和profile编号均检查；未展示即 `unseen_reference`。
- 组装器补 evidence、来源和 open；未由模型复核的行保持 rules，来源核验依据内部组装记录，不接受模型自报来源。
- L3 复核保存在 `terminal_reviews`，计算使用它重算渠道与分级，不改原始 POI，也不回写 L2 的原始统计。复核理由进入来源说明；排除项保留 C 和暂缓动作。
- 五层检查仍检查完整组装物；每次决定、完整物、检查和 source_map 都保存在 attempts。反馈使用 decision_path 定位模板或决定条目；原始违规未删除。
- L2/L3保留人工关口。真实自动验证显式设置auto_approve，记录为auto (demo)，不代表人工业务批准。
- 每轮工具完成都保存快照；会话恢复保留编号登记、草稿、来源映射、累计用量和Agent身份。回退清除失效决定及当前产物，历史attempts/ledger/events保留。

## 上限与运行

工具呈现 ≤1500 UTF-8 字节；请求 messages+tools 的保守上界加协议预留 ≤8000。字节上界比通常的分词 token 数严格，报告明确区别于供应商实际 usage。车间内压缩保留真实契约、最新可见事实、游标、已保存决定编号和反馈，避免悬空 tool 消息。所有网络重试共享预算；供应商未给usage时按请求/输出预留扣账。

整次默认300k；车间预算依次为20k/35k/35k/25k/65k/25k/65k/30k，总计300k。L3/L4最多64步，其余32步；每车间3次提交机会。检查失败不会换成rules结果。

通过进程环境提供LLM_BASE_URL、LLM_API_KEY、LLM_MODEL，不把凭据写入证据。CLI：

```powershell
uv run python -m pipeline run --runtime agent --area guanqian --profile jiangnan-gongquan --input "观前街周边3公里，2个地推，做4周" --root <主仓库.work/tmp/任务运行目录> --auto-approve
uv run pytest
```

未传auto-approve时关口返回gate_pending，使用Session.restore目录、resolve_gate与run继续。服务API与SSE沿用原接口。真实验证报告和运行证据位于主仓库 `.work/reports/` 与 `.work/tmp/`。
# 单车间验证

`uv run python -m harness stage-test --stage L1 --from-run <run_dir> --model deepseek-v4-flash --root <output_root> --token-budget 30000`

从原运行的 `inputs.json` 与通过检查的冻结上游创建独立运行，只执行指定车间；原运行保持不变。L1 默认上限 30k，L2 默认 40k；L1 通过后，L2 的 `--from-run` 指向新生成的 L1 运行目录。现有人工关口与显式演示自动批准设置继续生效。缺少冻结上游时拒绝运行。

逐步输出工具名、参数摘要、结果 token 的 UTF-8 字节保守上界和累计模型 token；`llm.usage` 保留供应商实测用量或明确标记的预算估算。失败、重试、检查和去重事件均落盘。车间内同名同参数调用返回首次结果摘要与原步骤编号，不再次执行；缓存随会话检查点保存，进入新车间时更新作用域，避免复用过期引用。
