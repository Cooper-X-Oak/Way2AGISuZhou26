# 通用 Agent runtime：公开上下文与复现

## 第一阶段目标（2026-10-24 用户澄清）

给市场拓展经理生成基于现有信息的市场开发策略。三个商圈的已有商户数据是可复用的区域底座，可以用于水、酒、其他饮料、面包等任务；不是只服务卖水，也不是每换产品就必须重采一套区域数据。

通用 runtime 应执行：理解目标 → 读取已有数据 → 判断影响决策的信息缺口 → 自主搜索/读取/计算或调用专业 Skill → 根据证据修订策略 → 检查并交付。缺能力则编排工作流和技能。不要把可由 Agent 完成的研究转成等待用户处理的清单。

竞品、供货信息或价格资料不齐，不应机械阻断策略。Agent 可以搜索补充；搜不到则为空或标未知，仍基于已有信息作有解释的决策。价格、库存等事实不能编造，但缺少这些事实不妨碍写出适合当前阶段的话术、访谈提纲与切入策略。

第一阶段不要求完整四周任务派发、业务员日程、复访管理或人工消化数百条复核任务。它们不是 runtime 通关的前提。用户未声明资金或模型用量不足，不得把“缺少预算”列为阻塞。

## 当前代码真实差距

基线：`f2dac2109709783e40ab3841881a5b1aacccd49f`（`feat/pipeline`）。外部旧审查基线为 `aae002c`，不能混用测量值。

- `pipeline/engine.py` 固定8关顺序，`Judge.decide()` 一次提交JSON请求；当前没有模型工具调用循环、网页研究工具、技能发现/调用与结果回填闭环。
- `pipeline/l4.py` 包含饮水话术和固定切入角度；`profiles.py` 限制竞品类别为水；`contracts.py` 使用餐饮网格枚举。将文件搬到skills目录但保持这些行为不变，不足以成为通用runtime。
- `l2c.py` 向模型提供 `allowed_strategies`、`allowed_reasons` 并要求相等；模型不能根据新增证据自主修订策略。检查应验证事实引用与数学，不把代码预设的唯一商业意见当真值。
- 产品原文依赖被Git忽略的 `.work/handbook/`。本目录公开真实产品文本与复现工具，外部开发无需读取原作者本机。
- 现有tests包含端到端测试，之前“完全没有端到端测试”的说法不成立；缺口是可移植环境、动态补全、跨品类和实际工具调用的验收。

## 下载与复现现有基线

从包含本目录的公开分支或PR checkout。Python 3.11+、uv。首次安装依赖需要联网；下述rules业务运行只读缓存，不请求高德或模型。

```powershell
uv sync --locked
# 先报告数据、来源与当前能力，不写工作资料
uv run python docs/runtime-review/reproduce.py
# 显式将公开真实资料放到旧引擎要求的位置，保留既有不同内容不覆盖
uv run python docs/runtime-review/reproduce.py --prepare-sources
# 运行当前基线；结果写 .work/reports/public-runtime-repro/
uv run python docs/runtime-review/reproduce.py --run --area shishan --profile jiangnan-gongquan
```

`--prepare-sources` 是复现适配，不是最终源码修复；正式实现应把资料来源与运行上下文接线，摆脱隐藏目录依赖。`dongting-brief.md` 保留本地源文件字节和原文，`handbook.md` 仅保留赛道章节，不含现场网络凭据；图片/视频没有在这个文本包中重发，runtime不读取这些媒体。

原始区域快照已在 `data/areas/{shishan,guanqian,ligongdi}/` 版本控制中。脚本打印两份已有档案在三个区域的渠道计数；零计数只说明当前分类映射没有匹配，不证明区域无法用于另一品类，更不证明现实没有商户。数据采样截断和来源以coverage为准。

## 已观测结果与新的验收

之前本地rules实跑：1199个POI，4网格，229终端（A46/B26/C157），73条材料实际8种文本，954条复核记录。技术检查13项通过，243项测试通过。见 [evidence.json](evidence.json)。这些是旧流水线的结果，不是通用Agent已经实现的证据。

赛百味话术出现“家庭常备”说明模板适配差，应由写作/渠道技能根据目标重写；不能推出“缺少价格所以无法写话术”。供货候选没有资质、竞品信息为空，允许明确未知并继续策略；不能伪造事实，也不必全部收齐才交付。

新的验收以 [scenarios.json](scenarios.json) 的水/酒/饮料/面包跨任务矩阵为基础。它们是测试任务定义，不是真实商户调查结果或虚构产品档案。相同三个区域底座被复用，Agent按产品和任务动态选择候选及相关工具；结果不要求相同或保证成交。若任务确实要求缓存外的数据，应自主获取或解释搜索结果后给出可行替代路径，不以预设渠道零值立即结束。

## 工程参考与结论边界

- [Anthropic: Building effective agents](https://www.anthropic.com/research/building-effective-agents)：区别固定代码路径与模型自主选择过程/工具。用于说明当前实现与目标的差距，不强制采用特定框架。
- [Anthropic: Effective context engineering](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents)：可用按需检索和上下文管理支撑长任务。模型上下文容量是技术参数，不是用户缺钱；不能把任意40,000字符阈值当作所有模型的限制。

计算和结构验收继续由代码负责；产品事实、领域知识、话术及业务策略放入输入、可配置的专业Skill与证据中。流程要能搜索、调整和重试，失败及工具过程记录可追溯。仅在真正不可执行、权限不足或关键约束冲突时报告明确阻塞，不把普通未知项变成统一关卡失败。

## 公开资料范围

用户授权公开项目复现上下文。品牌原文来源为公开黑客松brief（见sources/manifest.json），权利归原来源，未另行宣称其采用某个开源许可证。本包不发布API key、个人联系方式、现场Wi-Fi凭据、完整活动手册或本机路径；不需要ChatGPT会话和私有.work链接。
