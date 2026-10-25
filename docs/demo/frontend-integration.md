# 单文件工作台合入与验证

从用户提供的 `frontend.bundle` 引入 `7056361` 前端重写（本地提交 `1f24ff7`），合入 `feat/agent-task-workspace`。只引入前端重写；bundle 中删除旧数据库基础和旧数据的两个清理提交未在本次引入。合入前的四个未提交前端文件保存在 delivery checkout 的命名 stash `Preserve concurrent frontend edits before supplied 7056361 integration`，未丢弃。

## 使用

- 离线：直接打开 `web/data/agent-workbench.html`，不需构建或外部资源。内嵌 Agent 版和规则版两条真实任务；离线消息明确显示未执行。
- 在线后端：`uv run --env-file .env python -m uvicorn server.app:app --host 127.0.0.1 --port 8787`。注意入口是 `server.app:app`，不是原交付说明中的 `server.tasks:app`。
- 在线前端：进入 `web/app`，安装依赖后执行 `npm run dev -- --port 5174`。打开 `http://127.0.0.1:5174/agent-workbench.html`。Vite 只提供文件和 API 代理，工作台 HTML 本身无需构建。

## 合入修正

- 保留人数、周期可选：欢迎语、输入提示、关卡摘要、完成提示与快捷回答均不再要求先填人员排期。
- L0 支持「推荐 3 公里内的供货方」「3 公里」等半径表达；沿用最近一次明确约束，不将普通步行距离当作搜索半径。
- 停止原因去重；规则结论与模型结论分开标注。继续保留行动、工具参数、返回数据和模型最终决策，不展示内部推理原文。
- 修复原包在小于 1180 px 时将页面拉长的响应式规则；窄屏通过对话/产物切换，输入框固定在可见范围。
- 修复任务结束与视图读取并发时，已完成状态错误附到早期进度视图、导致前端不再获取最后产物的问题；读取产物前捕获状态，确保至少获取一次完整终态。

## 本机验证

- 完整 `uv run pytest -q`：278 passed，1 条 Starlette 测试依赖弃用提示，297.92 秒。
- 新增半径表达后定向 `uv run pytest tests/test_conversation_regressions.py tests/test_workbench_view.py -q`：9 passed；其中包含新增的 3 个半径表达用例。
- `node --test tests/workbench-conversation.test.cjs`：3 passed，覆盖任务合并、追问 parent_id 和新任务产品/商圈。
- 真实浏览器离线打开：外部 HTTP 资源请求为 0；发送消息明确显示未执行。
- 浏览器几何检查：968×533 视口页面高度 533，输入区底部约 517；390×844 视口页面宽高 390×844，输入区底部约 827。修复前窄桌面页面高度为 1277。
- 在线加载已有无人员排期任务：显示「3 公里 · 人员排期待安排」及「策略已生成 · 排期待安排」。
- 新页面实际提交「推荐 3 公里内的供货方」：run `f22a0c8012874a88aadb45cb611d24e7` 完成，识别 3000 米，无人数/周期追问。移动宽度切换到地图可见 1371 个圆形图元（包含缓存、补采及标记，不作为商户去重计数）。
- 结束时的并发读取新增 `tests/test_workbench_polling.py`；与半径、视图定向回归共 10 项通过。完整 278 项测试之后新增的 3 个半径用例和 1 个并发用例均已定向执行。

当前可视化是已有拓客流水线的交互入口；任意问答、话术重写和供货意图路由仍受当前后端各关能力限制，不能把立项识别通过等同于任意意图均已实现。
