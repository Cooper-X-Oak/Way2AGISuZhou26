# Agent 运行时

`production/line.yaml` 声明车间、契约、技能目录、工具白名单、五层检查与人工关口。
`Session` 在整条产线中持有一个 `Agent`：一个身份、一个通用系统提示、一份工作记忆。
每个车间追加环境消息并切换 tools；技能全文仅通过 `load_skill` 读取。
离开车间时保留 brief、决定摘要和关键编号，原始工具结果归档在运行证据中。

## 运行

通过环境变量提供 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，程序不保存这些配置。

```powershell
uv run python -m pipeline llm-check --runtime agent
uv run python -m pipeline run --runtime agent --area guanqian --profile jiangnan-gongquan --input "观前街周边3公里，2个地推，做4周" --root <运行产物根目录>
```

默认保留 L2/L3 人工关口；显式 `--auto-approve` 仅标记为 `auto (demo)`，不代表人工批准。
旧路径默认 `--runtime rules --mode rules`；旧逐关 LLM 路径使用 `--mode llm`。
服务 `POST /api/runs` 接受 `runtime: "agent"`，现有 gates 接口完成经理确认。
Agent SSE 推送 `events.jsonl` 的完整信封，以 `seq` 作为断线续传 ID；旧运行继续推 ledger。

无回调的 CLI 会在关口以 `gate_pending` 返回；快照保存消息、状态、工具引用及累计 token。
可在配置好环境变量的新进程中恢复，不需重复模型调用或重新生成待批准产物：

```python
from runtime.session import Session
session = Session.restore("<run_directory>")
session.resolve_gate(True, "经理确认")  # False 保留拒绝记录并停止
directory, state = session.run()      # 继续同一 agent_id；下一关口仍会挂起
```

## 检查与审计边界

- 工具只读取快照或包装现有确定性计算，不调用 `stage.run`，不调用旧逐关 LLM。
- 模型通过 `submit_artifact` 提交完整契约；`$ref/$merge/$each/$concat` 只做明确指定的 JSON 复制和拼装。
- 每次提交及检查保存在 `attempts/`、`checks/` 和 `ledger.jsonl`；失败与关口复检分别保存。
- 通过检查才发布交付物。回退使下游当前交付物失效，但保留每次尝试与事件证据。
- 保留现有检查政策，包括评分、排名、竞争策略约束、伙伴分类校验、理由原文要求与 PACK 顶层 `judged_by=rules`；不为模型放宽检查。
- `intent` 是模型提供的一句公开行动意图，写入 `agent.plan/agent.think`；不保存提供商的隐藏推理字段。
- `llm.usage` 使用提供商 usage；缺失时按请求 UTF-8 字节、协议预留和输出上限保守扣账。上下文压缩 token 数同样标为字节估计，不冒充实测。
- 单车间默认 16 次模型调用、3 次交付物提交、150k token 预算；客户端默认整次运行 200k。网络重试共享预算，不支持 tools 时明确停止。
- 运行目录使用内容寻址存储；读取产物和快照应使用 `pipeline.io.read`，以展开并验证引用。

## 离线验证

`uv run pytest tests/test_agent_runtime.py` 覆盖连续车间、真实门禁反馈、工具权限、压缩、持久化恢复、回退、预算、网络协议错误及服务 SSE。脚本化回复使用标注的测试夹具；真实集成不使用这些回复。
