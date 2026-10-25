# 本地技术债跟踪

检查基线为 `d39099b`。本轮使用统一隔离分支 `fix/demo-debt-hardening`；集成分支保留 `feat/pipeline`。本文件描述范围及后续设计，测试结果另存 `.work/reports/`，不将待办描述为已交付。

| Issue | 范围 |
| --- | --- |
| [#32](https://github.com/Cooper-X-Oak/Way2AGISuZhou26/issues/32) | 工具操作的对话关联在最终模型回复前保存 |
| [#33](https://github.com/Cooper-X-Oak/Way2AGISuZhou26/issues/33) | JSON 同目录临时写入后原子替换，失败保留旧值 |
| [#34](https://github.com/Cooper-X-Oak/Way2AGISuZhou26/issues/34) | 当前演示的茶、办公、健身渠道话术适配 |
| [#35](https://github.com/Cooper-X-Oak/Way2AGISuZhou26/issues/35) | 隔离分支回归与证据记录 |
| [#36](https://github.com/Cooper-X-Oak/Way2AGISuZhou26/issues/36) | 文档入口及历史基线标识 |
| [#37](https://github.com/Cooper-X-Oak/Way2AGISuZhou26/issues/37) | 后续：长对话上下文与约束摘要 |
| [#38](https://github.com/Cooper-X-Oak/Way2AGISuZhou26/issues/38) | 后续：存储职责与是否迁移的决策 |
| [#39](https://github.com/Cooper-X-Oak/Way2AGISuZhou26/issues/39) | 工作区登记；清理需先确认归属 |

## 长对话：保留原始证据，再设计摘要

当前每轮执行把历史请求与新要求拼接，超过 20,000 字符拒绝继续；对话模型接收最近 20 条消息和当前任务文档。该行为仍保留。后续需要独立保存当前有效约束及其来源消息，显式记录被替代的旧约束；原始消息与每轮执行快照保留。摘要不能新增商户、价格或执行事实，也不能以简单截断替代参数继承。先验证长对话、多轮范围修改和冲突约束，再决定上下文压缩方式，不在本轮引入模型摘要调用。

## 存储：运行文件继续承担当前事实记录

| 数据 | 当前读写位置与职责 |
| --- | --- |
| 运行输入、产物、检查、失败与重试 | `runs/<id>/`，通过 `pipeline.io.read` 展开并验证内容引用 |
| 对话、任务文档与修改记录 | 运行根目录下 `conversations/` |
| 公共匿名归属 | 公共运行根目录下 `session-owners.json` |
| 运行并发与等待状态 | 单进程 `RunManager`；重启恢复历史并将未完成任务标为中断 |
| 数据库模型及导入资料 | `app/`、Alembic、`data/app.db` 与 blob；尚未接入上述任务执行链路 |

当前文件与数据库并非双写同步系统。后续先确定数据库实际需要承担的查询或事务需求，再选择迁移范围、版本标识和回滚路径。迁移必须保留原始来源、内容哈希及 ledger；不要仅因数据库底座存在就要求 Demo 全面迁移。

## 工作区：登记后保留

本轮登记分支、HEAD、未提交改动数量与临时目录名称，报告位于主工作区 `.work/reports/debt-workspace-inventory-20261024.json`。登记时间的主目录为 `d39099b`；独立交付 checkout 为 `feat/agent-task-workspace`、`3bec082`。交付 checkout 的 HEAD 不证明在线进程正在加载该版本，确认线上版本还需单独读取进程及启动目录。

其他 agent 的未提交工作、历史运行、测试目录和交付 checkout 全部保留。后续按用途、归属、可恢复证据、是否有进程使用逐项决定归档，不按目录名或年龄自动删除。
