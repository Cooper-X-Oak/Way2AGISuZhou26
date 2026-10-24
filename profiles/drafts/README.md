# 产品档案草稿（Issue #15）

此目录中的 `fictional-tea`、`fictional-beer` 是测试用虚构产品，由明确的 mock
响应生成。`judged_by: llm` 表示所测试的抽取契约，`draft_metadata.mock: true`
明确表示没有实际模型判断，不得作为真实产品资料使用。

## 抽取与证据

```powershell
uv run python -m pipeline profile-draft --text-file <原文文件> --id <新id> --name <原文中的名称> -o <检查报告.json>
```

`--name` 和 `-o` 可省略。默认报告目录是主仓库 `.work/reports/profile-wizard/`。
每次调用保存独立 ledger；检查通过才保存 YAML 及同名原文 `.txt`。已有 id 不覆盖。
引用按规范化空白进行子串检查，`user_text#N` 的 N 为原文从零开始的 Unicode
字符偏移。抽取条目保留 `quote`、`source`、`judged_by: llm`；复用规则库的
通用合规规则保留真实规则来源并标记 `rules`，不会伪称来自用户原文。

当前默认调用明确返回 `gateway_contract_unavailable`：公开 `Gateway.decide`
只支持既有 L0–L4 契约，没有 PROFILE 契约。没有调用私有 `_complete`、修改
注册表、另建 HTTP 客户端或读取真实密钥。需要网关后续公开支持自定义契约
或增加 PROFILE；预算与传输重试仍应由同一 Gateway 实例管理。

## 离线复现

在任务 worktree 根目录运行：

```powershell
uv run python tests/fixtures/replay_profile_drafts.py
```

该测试入口生成两份虚构草稿（存在时复用），再逐份调用 CLI 的
`run --offline --area shishan --profile drafts/<id> --auto-approve`。
所有运行数据写入主仓库 `.work/tmp/profile-wizard-replay/`；报告在
`.work/reports/profile-wizard/`。没有替换任何关卡或降低阈值。

现有 L0 检查硬编码 `profiles/<id>.yaml`，因此入口显式将其档案目录配置为
`profiles/drafts`。任意草稿可用以下等价方式执行：

```powershell
uv run python -c "from functools import partial; from pipeline import l0; import pipeline.__main__ as cli; from pipeline.profiles import PROFILE_DIR; from pipeline.profile_builder import report_directory; from pipeline.engine import execute; l0.PROFILE_DIR=PROFILE_DIR/'drafts'; cli.execute=partial(execute, root=report_directory().parents[1]/'tmp'/'profile-wizard-runs'); raise SystemExit(cli.main())" run --offline --area shishan --profile drafts/fictional-tea --input '半径3公里，2个地推，做4周' --auto-approve
```

`load('drafts/<id>')` 在内存中将逻辑来源映射为同名原文文件路径，原来的
`user_text#N` 保存在 `input_source`，磁盘 YAML 不改变。读取时验证原文摘要。
这是现有引擎按文件读取证据所需的适配。未来服务应将档案目录作为显式参数
传递，避免在并发请求中修改 L0 模块变量。

档案结构兼容不表示现有业务措辞适配所有快消品：L4 仍有固定的“用水”措辞，
PACK 的引用元数据仍固定标记 `human`。本任务未获准修改这些关卡。

## 建议的服务契约（尚未实现）

`POST /api/profiles/draft` 输入 `{text, id, product_name?}`；成功返回
`{status: "passed", profile, profile_path, ledger_path, attempts}`。
校验三次失败返回 422 和逐次 violations；同 id 已存在返回 409；
网关契约缺失返回 503（`gateway_contract_unavailable`）。返回路径在服务实现时
应转换成受限的资源 id。文本中未出现的竞品和商业参数不能补造。
