# 临时公开测试入口

此方案使用本机 Python 服务和 Cloudflare Quick Tunnel，适用于黑客松测试。电脑、后端和 tunnel 进程需要保持运行；重建 tunnel 后网址会变化。前端以单个 HTML 从同一服务返回，不需要 Vite 或前端构建。

## 启动

在项目目录准备 `.env`（不提交）：`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`、`AMAP_KEY`。

```powershell
uv run --env-file .env uvicorn server.public:app --host 127.0.0.1 --port 8788 --workers 1
```

另一个终端：

```powershell
cloudflared tunnel --url http://127.0.0.1:8788 --protocol http2
```

分享终端输出的 `https://….trycloudflare.com`。测试者不需要提供模型 key。密钥只由本机 Python 服务读取，不会作为前端配置发送给浏览器。

## 页面怎么用

- 公共示例只固定展示一条真实 Agent 录制，点击“回看执行过程”查看动作、工具返回与产物。
- “快速开始一次实况”直接提交默认产品、商圈和 3 公里覆盖范围；“新建任务”可选择产品、商圈并输入要求。
- 测试者任务与追问只在所属浏览器展示，完成视图另存入该站点 localStorage。公共示例不混入个人缓存。刷新后仍可继续查看。
- 执行产物仍保留在本机后端以支持继续对话，不是完全无服务端存储。清除 cookie、无痕窗口关闭或更换浏览器不能恢复原匿名身份；临时网址变化也不会继承旧域名的浏览器记录。

## 公开环境与本地开发隔离

- 公开运行记录默认存放于 `.work/tmp/public-demo-runtime/`；可用 `PUBLIC_DEMO_ROOT` 指定独立持久目录。
- 测试者通过 HttpOnly、Secure、SameSite=Lax 的随机会话 cookie 识别。任务列表、详情和继续对话均检查所属会话；清除 cookie 或更换浏览器后，原匿名记录无法恢复访问。
- 全局同时执行两条任务；每个会话每分钟最多提交 10 次，全站每分钟最多提交 30 次。这是请求频率限制，不是模型预算配置。
- 不开放本地旧 `/api/runs`、API 文档或任意文件读取接口。仅返回页面、产品/商圈目录和隔离后的任务 API。
- 公开响应不缓存、不索引。跨站 POST 拒绝。匿名会话与请求频率限制用于小规模测试，不构成完整账号系统或针对分布式滥用的防护。

## 验证

`uv run pytest tests/test_public_demo.py tests/test_workbench_polling.py -q` 覆盖两个独立访客之间的任务隔离、跨会话追问拒绝、同会话追问、私有文件与旧 API 不公开、跨站 POST 拒绝及请求频率限制。

Quick Tunnel 不支持 SSE；本工作台使用任务视图轮询，不依赖 SSE。停止 tunnel 即停止公开访问，本机记录保留。

## Brief 与连续上下文

右侧 Brief 页签（或顶部“立项”）展示当前轮原始需求、追加要求、产品背景、范围、假设来源和待确认信息。用户通过当前对话补充需求，服务端沿 parent_id 继承累积请求、已采集商户，以及上一轮 Brief 和阶段结论；上一轮结论作为历史上下文，不作为本轮新采集事实。

所有通用判断调用与找终端工具循环统一接收 task_context：当前 brief、previous_round 和本轮 completed_decisions。阶段检查与重试规则保留。当前执行器仍是分阶段流水线，实况中的找终端使用模型工具循环，其余阶段仍按现有规则模式执行；共享上下文接线不等于已实现全程自主调度或每关都调用模型。
