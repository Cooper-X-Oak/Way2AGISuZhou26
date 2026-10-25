# 三分钟 Demo：Agent 自主补采

**一条命令：**

```bash
uv run python -m pipeline demo
```

## 演示的是什么

- 产品：**洞庭山·新鲜活泉**（真实产品资料，见 `docs/product-sources/`），目标渠道是茶馆、写字楼、健身房
- 区域：苏州高新区狮山商圈 3 公里（已有高德快照）
- 问题：快照里只有餐馆，这三类渠道都是 **0 家**

| 步骤 | 屏幕上看到什么 |
|---|---|
| ① 规则版对照 | 不调用模型 → 停在 L2，「没有任何目标渠道商户可选」 |
| ② Agent 终端发现 | 实时打印：🤖 模型决定调用什么 → 🔧 工具返回什么；模型**自己选**关键词去高德补采；结论里的计数、商户 id 由代码复算，不对就打回 |
| ③ 结果 | 选中的商圈、A 级首访名单（带「Agent 补采」标记、理由、动作）、每个渠道一条进店话术；报告存到 `.work/reports/demo-<run>.md` |

## 三种模式（都会如实标识在屏幕和报告里）

| 模式 | 条件 | 说明 |
|---|---|---|
| 实况 | 配好下面的 key | 真实模型 + 真实高德；**跑成功后自动录制**到 `docs/demo/recordings/xinxian-shishan.json` |
| 回放 | 有录制文件 | 按录制顺序重放真实模型回复和高德结果，不联网、不消耗额度 |
| 自动（默认） | — | 能实况就实况，否则回放；都不行就告诉你缺什么 |

强制指定：`uv run python -m pipeline demo --mode live` / `--mode replay`

## 实况需要的配置

```bash
export LLM_BASE_URL=https://<网关>/v1     # OpenAI 兼容，必须支持 tools（DeepSeek / Qwen / Kimi / GLM 等）
export LLM_API_KEY=<key>
export LLM_MODEL=<模型名>
echo "AMAP_KEY=<高德 Web 服务 key>" >> .env   # .env 已被 .gitignore 忽略
```

**提交前请实况跑通一次**，让录制文件生成，然后把 `docs/demo/recordings/xinxian-shishan.json` 提交进仓库。之后现场任何时候都能回放。

## 现场讲解（3 分钟）

1. **0:00–0:30**　问题：新品要进茶馆、写字楼、健身房，但手上的商户数据只有餐馆。规则系统在这里就停了（屏幕 ①）。
2. **0:30–1:45**　Agent：它先盘点现状，发现三个渠道都是 0，然后自己决定去搜「写字楼」「健身房」「茶馆」（屏幕 ②）。指出两点：查什么是模型决定的；它报的数字由代码复算，报错会被打回。
3. **1:45–2:40**　结果：选中的商圈、A 级首访名单（标了「Agent 补采」的就是刚查到的）、对应话术（屏幕 ③）。
4. **2:40–3:00**　边界：只有终端发现这一步由模型决策，打分和路线是代码；商户信息需要走访确认；价格、毛利未知，话术不承诺。

可选：工作台页面里看地图。`demo` 会自动导出到 `web/data/xinxian-agent`，然后 `cd web/app && npm ci && npm run dev`，打开 `#/replay/xinxian-agent`。工作台仍显示原来 8 个站点，不单独显示 L1D。

## 出问题时看哪里

- 终端最后会打印运行目录 `runs/<run_id>/`
- `agent/steps.jsonl`：每一步模型和工具调用；`L1D.json`：Agent 结论；`ledger.jsonl`：每关的尝试和检查
- 实况失败时 demo 会说明停在哪一关，可以直接 `--mode replay` 切回放（前提是之前录制过）

## 其他用法

```bash
# 任意档案/商圈开启 Agent 终端发现
uv run python -m pipeline run --area shishan --offline --auto-approve --agent-l1d \
  --profile xinxian-huoquan --input "苏州高新区周边3公里，2个地推，做4周"
uv run pytest tests/test_demo.py tests/test_l1d_agent.py -q   # 假模型 + 假高德的协议测试
```

`profiles/bread-trial.yaml` 是测试用的**示例假设**档案（非真实品牌），只用于协议测试。
