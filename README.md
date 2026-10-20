# WebAppTester

WebAppTester 通过 Planner、Executor 和 Verifier 三个角色执行有步数上限的 Playwright 浏览器测试。每次页面观察都保存可见文本和截图；最终报告给出 PASS、FAIL 或 UNKNOWN，并引用实际观察作为证据。

## 本地演示

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m playwright install chromium
.venv/bin/python demo/app.py --port 8765
```

在另一个终端中，启动 Ollama 并获取具体的本地模型：

```sh
ollama serve
ollama pull qwen2.5:3b
.venv/bin/webapptester --scenario 'Create project Alpha and verify it appears on dashboard and sidebar' --url http://127.0.0.1:8765 --provider ollama --model qwen2.5:3b --output runs/demo
```

CLI 会在指定输出目录下创建带唯一 UTC 时间戳的子目录（`runs/demo/<timestamp>/`），并写入 `report.json`、追加记录的 `events.jsonl`、`screenshots/` 中的 PNG 截图以及 `trace.zip`。PASS、FAIL、UNKNOWN 的退出码分别为 0、1、2。运行器依次尝试 Playwright 安装的完整版 Chromium、默认浏览器，以及已安装的 Google Chrome。运行本地验收测试前，请先安装 Playwright 浏览器。

若要顺序运行两个场景并导出一个摘要，请使用包含的批处理配置：

```sh
.venv/bin/webapptester-batch examples/demo-batch.json --output runs/batch
```

每个场景都在带时间戳的批量运行目录中保留自己的产物。该目录包含 `batch-summary.json` 和 `batch-summary.csv`；汇总记录场景元数据、状态、报告路径、步数和原因，不包含操作输入值。全部场景通过时退出码为 0，任一场景失败时为 1，没有失败但至少一个场景无法判定时为 2。

可通过 `--provider deepseek` 选择 DeepSeek；默认模型为 `deepseek-flash`，代码将其用于 DeepSeek-V4.1-Flash。结构化 JSON 请求会关闭思考模式，单次回复最多 4096 Token。运行前须在本地环境中配置 `DEEPSEEK_API_KEY`；没有密钥就不会发送 DeepSeek 请求。CLI 和批量运行器共用被 Git 忽略的 `runs/deepseek-budget.json` 预算台账。每次调用 `deepseek-flash` 前预留人民币 10 元；预留后可能超过人民币 20 元上限时停止。收到 Token 用量后，以保守估算替换预留额；缺少用量或请求异常中断时，保留全部预留额。

付费评测前应重新核对 [DeepSeek 模型与价格文档](https://api-docs.deepseek.com/quick_start/pricing/)；服务价格可能变化，仓库也不包含 API 密钥。

要在开发期间本地检查最新的追踪：

```sh
.venv/bin/playwright show-trace "$(ls -td runs/demo/*/trace.zip | head -1)"
```

Playwright Trace 是本地开发产物，可能包含会话 Cookie 等敏感信息。结果看板不提供 Trace 下载；如需检查，请使用所选报告中的本地路径。

## 隔离的 4gaBoards 测试环境

集成测试环境在 `127.0.0.1:3001` 上运行 4gaBoards 3.2.16 和 PostgreSQL。它使用单独的 Compose 项目和命名卷，因此端口 3000 上的现有实例及其数据保持不变。

一次性创建私有首次运行凭据：

```sh
.venv/bin/python scripts/setup_4gaboards_local.py
```

该命令会创建 `runs/4gaboards-local/instance.env`（模式为 `0600`），并打印下一个 Docker 命令，但不打印任何凭据值。使用打印的命令启动测试环境，或运行：

```sh
docker compose --env-file runs/4gaboards-local/instance.env \
  -p webapptester-4ga -f examples/4gaboards/compose.yaml up -d
```

生成的环境文件和 Docker 卷仅供本地使用。请勿提交环境文件。

测试环境健康后，在单独的 Playwright 上下文中创建私有浏览器登录状态：

```sh
.venv/bin/python scripts/bootstrap_4gaboards_auth.py
```

该命令会读取本地首次运行凭据但不打印它们，确认已认证的仪表板，并写入 `runs/4gaboards-local/auth.json`（模式为 `0600`）。使用此状态配合 `--storage-state` 运行单个场景：

```sh
.venv/bin/webapptester \
  --scenario "Open the dashboard" \
  --url http://127.0.0.1:3001/ \
  --storage-state runs/4gaboards-local/auth.json \
  --output runs/4gaboards-local/scenarios
```

批次项目还可以包含本地 `storage_state` 路径。状态被加载到每个新的浏览器上下文中，且永远不会被复制到报告、事件或批次摘要中：

```json
[
  {
    "scenario": "Open the dashboard",
    "url": "http://127.0.0.1:3001/",
    "storage_state": "runs/4gaboards-local/auth.json"
  }
]
```

4gaBoards 场景清单位于 `examples/4gaboards/scenarios.json`，包含一个名为 `Course Test` 的课程示例，以及两个明确标记的本地只读冒烟场景。每个场景都连接隔离端口，并引用被 Git 忽略的 `runs/4gaboards-local/auth.json`。课程示例只运行一次；后续创建检查请使用全新的测试环境或唯一项目名。

确定性创建检查是可选参与的，因为它会创建一个唯一命名的本地项目并记录原始证据：

```sh
WEBAPPTESTER_4GA_SMOKE=1 \
  .venv/bin/python -m pytest tests/test_4gaboards_smoke.py -q
```

它会在
`runs/4gaboards-local/deterministic-smoke/screenshots/` 下写入报告、PNG 观察结果以及 Playwright
`trace.zip`（均位于该被忽略的目录中）。该检查最多使用 12 次浏览器操作，并在仪表板列表和侧边栏中独立断言新项目。

2026-10-16 在本地 Ollama 端到端验收运行中也根据自然语言场景创建了一个新项目，并在五次浏览器操作中返回了 `PASS`。其两个 SAT 检查点项目引用相同的最终观察结果，但在起始页面上引用不同的、不重叠的仪表板和侧边栏区域。被忽略的本地报告位于
`runs/4gaboards-local/runtime-acceptance-after-audit/20261016T102519799424Z/`。
这只是单场景验收结果，不代表课程完整场景集的成绩。

在确定性检查之后运行一次提供的模型场景时，请保持私有存储状态为本地，并检查持久化的报告路径：

```sh
PYTHONPATH=src .venv/bin/webapptester \
  --scenario "Official example: In the dashboard click Add project, enter the project name Course Test and confirm, then check the new project appears on both dashboard and sidebar." \
  --url http://127.0.0.1:3001/ \
  --provider ollama --model qwen2.5:3b \
  --storage-state runs/4gaboards-local/auth.json \
  --output runs/4gaboards-local/course-test
```

保留模型报告的 PASS、FAIL 或 UNKNOWN 结果。模型规划或提供者失败属于 UNKNOWN 执行结果，并不构成 4gaBoards 失败。不要针对同一测试环境重复 `Course Test` 创建。当前本地测试环境已包含来自诊断运行的 `Course Test`，其报告在畸形 Ollama 验证器输出后保持为 `UNKNOWN`。重新运行该确切创建场景前，请使用新的隔离测试环境；上述成功的唯一名称验收拥有自己的 PASS 报告。

使用以下命令运行测试：

```sh
.venv/bin/python -m pytest -q
```

## B0–B3 消融实验

正式的 36 个场景集未包含在此仓库中。发布时请将其作为私有清单提供，并运行 [docs/ablation.md](docs/ablation.md) 中描述的仅限本地的比较。该框架保留四个条件，并在实际执行场景之前不声称评分，而是写入每个场景的结果和指标。

## 结果看板

安装可选的 Streamlit UI 并使用以下命令启动测试与结果看板：

```sh
.venv/bin/pip install -e '.[test,ui]'
.venv/bin/streamlit run src/webapptester/dashboard.py --server.address 127.0.0.1 -- --runs-root runs
```

打开 [http://127.0.0.1:8501](http://127.0.0.1:8501)。页面顶部可手动输入自然语言场景，或导入 UTF-8 `.txt` / `.json` 场景文件并选择一条案例；填写应用 URL、模型、步数上限、实验配置及已有的登录状态后点击「开始测试」。同一页面每两秒刷新当前阶段、操作步数、最近操作类型和异常次数。结束后，报告自动加入下方运行历史。DeepSeek 仅在本机有 `DEEPSEEK_API_KEY` 时可选，且沿用人民币 20 元预算台账。每次网页启动只执行一条所选案例；批量 B0–B3 比较仍使用命令行工具。

历史区可以选择一组实验，查看每个案例由运行时检查点整理出的中文测试目标及 B0–B3 对照结果；再选择案例阅读完整的原始测试指令，比较结论、步数、耗时、Token 和状态说明。若触发 Planner 补查和预算仲裁，运行详情会显示安全筛选后的候选数量、剩余步数、选路或停止原因。原始判定原因仍在下方逐次运行详情的证据揭示控件后显示，连同检查点证据、操作、执行前后截图及报告文件一起查看。对于未随结果保存场景原文的旧记录，页面只展示已有检查点，不猜测测试任务。实验对照属于本地试跑，不是课程正式成绩。
`runs/` 目录为本地演示输出，Git 会忽略它；需要时请配置另一个带有 `--runs-root` 的本地根目录。页面衍生的证据以及报告和事件下载在显式选择原始证据揭示控件之前保持隐藏。截图和 Playwright Trace 是原始页面证据，可能包含敏感页面内容。特别是，Trace 可能包含会话 Cookie，因此它保留在磁盘上以供本地检查，且永远不会作为仪表板下载。

## Admidio 测试环境准备

课程目标版本 Admidio 5.0.2 的独立 Compose 模板位于 `examples/admidio/compose.yaml`。它仅绑定 `127.0.0.1:3102`，数据库只在 Compose 网络内开放，并使用独立的命名卷。使用以下命令创建被 Git 忽略、权限为 `0600` 的数据库凭据：

```sh
.venv/bin/python scripts/setup_admidio_local.py
docker compose --env-file runs/admidio-local/instance.env \
  -p webapptester-admidio -f examples/admidio/compose.yaml up -d
```

应用镜像固定为 `admidio/admidio:v5.0.2`，平台为 `linux/amd64`。配置已通过 `docker compose config` 验证。由于这台机器访问 Docker Hub 超时，实际使用官方 ZIP 和 Homebrew PHP 作为替代方案安装了独立的本地测试环境。管理员登录已在 `http://127.0.0.1:3102/` 验证；它与现有 4gaBoards 测试环境及其数据卷相互独立。

要在具有本地可访问 MySQL 服务器的 Mac 上重现该回退方案：

```sh
brew install php@8.3
mkdir -p runs/admidio-local/app
curl -L -C - --retry 3 \
  -o runs/admidio-local/admidio-5.0.2.zip \
  'https://sourceforge.net/projects/admidio/files/Admidio/5.0.x/admidio-5.0.2.zip/download'
shasum -a 256 runs/admidio-local/admidio-5.0.2.zip
unzip -q -n runs/admidio-local/admidio-5.0.2.zip -d runs/admidio-local/app
.venv/bin/python scripts/setup_admidio_native.py
/opt/homebrew/opt/php@8.3/bin/php -S 127.0.0.1:3102 -t runs/admidio-local/app
```

已验证的 ZIP SHA-256 为 `0d39f410c2a0747a1a68928e31261f3d65513a6674639a7d4b7cff5d63a88d7e`。
`setup_admidio_native.py` 仅创建专用的 `webapptester_admidio` 数据库和 `wat_admidio` 本地用户；它期望基于套接字的 MySQL 管理访问权限，并拒绝不一致的现有状态。在端口 3102 上完成官方浏览器安装程序，使用被忽略的 `runs/admidio-local/instance.env` 中的数据库密码和被忽略的 `runs/admidio-local/admin.env` 中的测试管理员账户。数据库主机为 `127.0.0.1`，组织为一次性本地测试组织，且所有私有文件必须保留在 Git 之外。然后保存浏览器登录状态：

```sh
.venv/bin/python scripts/bootstrap_admidio_auth.py
```

当前的本地测试环境具有 49 个应用程序表和一个已验证的管理员会话。本地运行的 Ollama `qwen3.5:9b` 打开了偏好设置，并通过两次浏览器操作验证了其可见的 `heading` 区域。私有报告、截图和 Trace 位于 `runs/admidio-local/semantic-heading-acceptance/20261019T082127941731Z/` 下。
早期的本地 Admidio 运行在遇到不支持的模型输出或不正确的导航后返回 `UNKNOWN`。来自陈旧侧边栏文本的初步 `PASS` 已被当前页面证据检查无效化，因此不是验收证据。

## 本地 Discourse 测试环境

独立的 Discourse `v2025.12.0-latest` 测试环境在 macOS 上运行，使用本地 PostgreSQL、Redis、Rails 和 Ember。环境设置、端口、私有登录状态和 Ollama 验收报告见 [Discourse 本地环境说明](docs/discourse-local.md)。B0–B3 条件、外部场景清单格式及三应用试跑结果见 [消融实验说明](docs/ablation.md)。
