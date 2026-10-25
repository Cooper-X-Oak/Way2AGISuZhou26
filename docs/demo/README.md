# Demo：Agent 自主补采（L1D 终端发现）

## 演示要讲的一件事

换一个品类（面包），三个商圈的缓存里**一家面包店、便利店都没有**。
规则版流水线到 L2 就停了；打开 Agent 后，模型**自己决定**查什么关键词、调用高德补采，
补到的商户经过代码核对后进入后续关卡，最后产出作战包。

```
L0 立项 → L1 卖给谁 → L1D 终端发现（Agent）→ L2 去哪里 → … → PACK
                         │
                         ├─ area_inventory    盘点现有商户
                         ├─ search_merchants  高德补采（模型选关键词/类型码）
                         └─ list_merchants    查看候选
                         结论里的计数、引用由代码复算；不对就打回给模型重写
```

## 准备（一次）

```bash
uv sync
# 模型：任意支持 tools 的 OpenAI 兼容接口（DeepSeek / Qwen / Kimi / GLM …）
export LLM_BASE_URL=https://<网关>/v1
export LLM_API_KEY=<key>
export LLM_MODEL=<模型名>
# 高德：仓库根目录 .env 写一行 AMAP_KEY=<key>（已被 .gitignore 忽略）
```

## 演示 1：对照——规则版跑不下去（约 10 秒）

```bash
uv run python -m pipeline run --area shishan --offline --auto-approve \
  --profile bread-trial --input "苏州高新区周边3公里，2个地推，做4周"
```

预期：`"status": "blocked", "stopped_at": "L2"`。L1D 显示两个渠道 `not_searched_offline`、候选 0。

## 演示 2：Agent 实时补采（约 30–90 秒，取决于模型）

```bash
uv run python -m pipeline run --area shishan --offline --auto-approve --agent-l1d \
  --profile bread-trial --input "苏州高新区周边3公里，2个地推，做4周" \
  -o .work/reports/bread-agent.json
```

终端会实时打印 Agent 的每一步：

```
🤖 模型 → 调用 area_inventory
🔧 area_inventory({}) → 渠道计数 {'bakery': 0, 'convenience': 0}
🤖 模型 → 调用 search_merchants
🔧 search_merchants({"keywords": "面包|烘焙"}) → 新增 xx 家，返回 xx 条
…
🤖 模型 → 给出结论
```

预期：`"status": "completed"`，`completed_stages` 里有 `L1D`。

讲解时可以打开的文件（都在 `runs/<run_id>/` 下）：

| 文件 | 看什么 |
|---|---|
| `discovery.json` | Agent 结论、新增商户、`agent.searches`（查了什么、几页） |
| `agent/steps.jsonl` | 每一步模型调用和工具调用的完整记录 |
| `agent/amap/*.json` | 高德原始响应（基础字段白名单，无联系方式） |
| `terminals.csv` | 最终终端清单，出现面包烘焙店和便利店 |
| `ledger.jsonl` | `pois_merged` 事件：L1D 通过后才合并 |

### 放进工作台（地图上看到补采的面包店、便利店）

```bash
uv run python -m pipeline export --run runs/<run_id> --out web/data/bread-agent
cd web/app && npm ci && npm run build:single     # 生成 dist/铺市流水线.html，离线可开
# 或开发模式：npm run dev，打开 http://localhost:5173/#/replay/bread-agent
```

工作台目前仍显示原来的 8 个站点，**不单独显示 L1D**；补采结果体现在地图和终端清单里。讲 Agent 过程时用终端输出或 `agent/steps.jsonl`。

## 演示 3：现场没网或怕翻车——回放模式

先用演示 2 真实跑一次，把高德结果录下来：

```bash
uv run python scripts/make_replay.py runs/<上一步的 run_id> -o docs/demo/amap-replay-shishan.json
```

现场：

```bash
uv run python -m pipeline run --area shishan --offline --auto-approve --agent-l1d \
  --amap-replay docs/demo/amap-replay-shishan.json \
  --profile bread-trial --input "苏州高新区周边3公里，2个地推，做4周"
```

- 模型仍然实时调用、自己决定查什么；只有高德请求从录制数据回放，不消耗额度
- 模型如果换了一个录制里没有的查询，工具会告诉它「录制中可用的查询」，由它决定下一步（这一点会如实记录在 steps 里，讲解时可以说明）
- 加 `--amap-live` 时，录制里没有的查询会实时调用高德

## 需要如实说明的边界

- `bread-trial` 是**示例假设档案**（`docs/demo/bread-trial-brief.md`），不是真实品牌资料
- 只有 L1D 用了模型；其余关卡仍是规则（`judged_by: rules`），打分、分级、路线由代码计算
- 补采结果只进本次 run，不回写 `data/areas/` 共享快照
- L2C 竞品、L3P 供货方仍沿用饮用水的市场数据，对面包任务没有参考价值
- 高德商户名称和类型不代表实际经营品类或进货意愿，需要走访确认

## 测试

```bash
uv run pytest tests/test_l1d_agent.py -q   # 脚本化假模型 + 假高德，验证循环与关卡逻辑
```

这组测试不证明真实模型或真实高德可用；演示 2 就是那次真实集成。
