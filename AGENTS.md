# 配送点拓客流水线

- 本项目是共享 harness；文档分层管理，临时输出只放 `.work/tmp/`，报告放 `.work/reports/`，任务文件 `.work/tasks/` 不得修改。
- 当前开发分支 `feat/pipeline`；改动前检查 Git 状态，保留其他 agent 的工作。
- 只实现数据、Python 流水线与 JSON；不做 UI、不部署、不提交密钥、不抓个人联系方式。
- Valvekr 参考仓库只读，不复制其实现。
- 不编造商户或 LLM 结果；离线判断必须标 rules，所有参数假设保留来源。
- 数学由代码计算，关卡检查不得为通关而放宽；每次失败和重试均保留 ledger。
- Python 3.11+，uv；验证命令 `uv run pytest`。
- 浏览器需要时使用 ego-browser 技能和任务专属 Space；先运行 `ego-browser skill`。
