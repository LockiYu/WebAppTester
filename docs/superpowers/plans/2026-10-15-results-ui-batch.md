# 结果 UI 与批量运行实施计划

> **对于代理型工作节点：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐步实施此计划。步骤使用勾选框 (`- [ ]`) 语法进行跟踪。

**目标：** 在本地 Streamlit 页面中查看现有的测试运行记录，下载其证据，并从命令行顺序运行一系列场景列表。

**架构：** `results.py` 是围绕运行工件的只读边界，不导入 Streamlit。`dashboard.py` 渲染其验证后的记录。`batch.py` 针对每个项目调用现有的单场景运行器一次，并写入批量摘要；每个项目保留其自身的报告、截图、事件和 Trace。

**技术栈：** Python 3.11+，Streamlit 1.55+，现有的 Playwright 和 pytest 技术栈。

## 全局约束

- 保留证据门控的 PASS/FAIL/UNKNOWN 语义，以及每个场景最多 35 次浏览器操作的限制。
- 仪表板仅读取配置在本地 `runs/` 根目录下的文件。永远不接受浏览器提供的任意路径作为截图或 Trace 的来源。
- 运行工件在仪表板中为只读。损坏或不完整的运行显示为不可用，而不会静默地视为 PASS。
- 批量执行是顺序的。失败的项目不会阻止后续项目运行；其状态根据其自身的报告保持为 FAIL 或 UNKNOWN。
- 永远不要在报告、操作日志或批量摘要中包含操作输入值、来自环境的本地凭据或环境密钥。URL、页面文本、证据引用、操作定位器、截图、JSON/JSONL 审计下载和 Playwright Trace 可能包含敏感页面内容；仅在用户明确揭示并带有清晰警告后，在回环绑定的本地仪表板中显示页面证据并下载 JSON/JSONL。Playwright Trace 永远不作为仪表板下载，因为它可能包含会话 Cookie；保留其本地路径供开发人员检查。不要声称源自页面的证据已被脱敏。高级别历史记录在揭示前可能仅显示状态、计数和计时信息。
- 保持现有的课程 PPT 和 markdown 文件未跟踪且未暂存。

---

### 任务 1：读取并验证本地运行工件

**文件：** 创建 `src/webapptester/results.py`，`tests/test_results.py`。

**接口：** `RunRecord` 包含 `directory: Path`、`status: str`、`reason: str`、`steps: int`、`elapsed_seconds: float`、`token_usage: dict[str, int]`、`evidence: tuple[dict[str, str], ...]`、`observations: tuple[dict[str, object], ...]`、`actions: tuple[dict[str, str], ...]`、`trace: Path | None`。`list_runs(root: Path) -> tuple[RunRecord, ...]` 仅在 `root` 下扫描 `report.json`，按最新优先顺序。`load_run(root: Path, report_path: Path) -> RunRecord` 验证报告模式并拒绝位于 `root` 之外的路径。`artifact_path(root: Path, run: RunRecord, path_text: str) -> Path | None` 仅解析位于 `run.directory` 和 `root` 内的现有文件。
- [ ] **步骤 1：编写失败的测试。** 在 `tests/test_results.py` 中，创建有效的 PASS 和 UNKNOWN 报告测试环境、一个损坏的报告以及截图路径使用 `../../` 的报告。断言最新优先的历史记录、保留的状态/证据、损坏报告的省略或显式不可用记录，以及用于逃逸路径的 `artifact_path(...) is None`。仅当旧报告的绝对工件路径解析在根目录内部时才测试它们。
- [ ] **步骤 2：验证红色。** 运行 `.venv/bin/python -m pytest tests/test_results.py -q`；期望 `webapptester.results` 导入失败。
- [ ] **步骤 3：实现。** 在读取文件之前使用 `Path.resolve()` 和 `Path.is_relative_to()`。使用 `json.load` 解析 JSON，要求 `verdict.status in {"PASS", "FAIL", "UNKNOWN"}`，要求 `steps` 为非负整数且不超过 35，并要求 `evidence`、`actions`、`observations` 为字典列表。跳过格式错误的报告但永远不要修改它们。对于缺失或逃逸的截图/Trace 路径返回 `None`。当旧报告中的工件路径相对于仓库工作目录时，将其解析为 `Path.cwd()` 和运行目录两者，仅接受配置根目录内的候选项。
- [ ] **步骤 4：验证绿色。** 运行 `.venv/bin/python -m pytest tests/test_results.py -q` 和 `.venv/bin/python -m pytest -q`。
- [ ] **步骤 5：提交。** `git add src/webapptester/results.py tests/test_results.py && git commit -m "feat: load local run history safely"`。

---

### 任务 2：Streamlit 结果仪表板

**文件：** 创建 `src/webapptester/dashboard.py`、`tests/test_dashboard.py`；修改 `pyproject.toml`、`README.md`。

**接口：** `render(root: Path) -> None` 消费任务 1 的 `list_runs` 和 `artifact_path`。使用 `.venv/bin/streamlit run src/webapptester/dashboard.py -- --runs-root runs` 启动。添加可选依赖组 `ui = ["streamlit>=1.55,<2"]`。

- [ ] **步骤 1：编写失败的测试。** 在 `runs/` 根目录的测试环境上使用 `streamlit.testing.v1.AppTest.from_file(...)`。断言出现一个 PASS 和一个 FAIL/UNKNOWN 运行，选定的运行显示状态、原因、步骤、令牌计数和每个检查点的证据，且缺失的截图/Trace 标记为不可用且不抛出异常。检查空根目录状态。不要断言 Streamlit 的内部 HTML。
- [ ] **步骤 2：验证红色。** 运行 `.venv/bin/python -m pytest tests/test_dashboard.py -q`；期望缺少仪表板模块或缺少 UI 依赖项。
- [ ] **步骤 3：实现。** 仅在 `__main__` 入口点解析 `--runs-root`。在 `render` 中，显示历史记录选择器/表格和带有 `st.metric` 的选定运行摘要，证据行包含观察 ID 和引号，操作时间线，以及仅在 `artifact_path` 接受它们时才为安全子集 `report.json` 和 `events.jsonl` 显示下载按钮。使用 `st.image` 显示关联的截图。保持 `trace.zip` 本地并显示其验证路径，附带明确警告称其可能包含会话 Cookie；永远不要暴露 Trace 下载。使用 `st.warning` 处理缺失的工件。保持页面只读并在元数据不可用时标记来源为未知；仅凭零令牌使用绝不能推断脚本化运行。
- [ ] **步骤 4：验证绿色。** 安装 `.venv/bin/pip install -e '.[test,ui]'`，然后运行 `.venv/bin/python -m pytest tests/test_dashboard.py -q` 和完整套件。在本地启动 Streamlit 并验证 PASS 和 FAIL 演示报告渲染无 Python 异常。
- [ ] **步骤 5：文档化和提交。** README 必须给出绑定到 `127.0.0.1`、`http://127.0.0.1:8501` 的安装/启动命令，原始证据揭示警告，并解释 `runs/` 是本地且被 Git 忽略的。仅提交四个命名的文件，使用 `git commit -m "feat: view run evidence in Streamlit"`。

---

### 任务 3：顺序批处理 CLI 和摘要导出

**文件：** 创建 `src/webapptester/batch.py`、`tests/test_batch.py`；修改 `pyproject.toml`、`README.md`。

**接口：** `load_batch(path: Path) -> tuple[dict[str, object], ...]` 接收一个包含确切键 `scenario`、`url`、可选 `provider`、`model`、`max_steps` 的对象 JSON 数组；`scenario`/`url` 为非空字符串，仅接受 `ollama` 或 `deepseek` 作为提供者，以及从 1 到 35 的整数步骤。`run_batch(items, output_root: Path) -> Path` 按顺序执行现有的 `cli.run`，并在一个带时间戳的批处理目录中写入 `batch-summary.json` 和 `batch-summary.csv`。注册 `webapptester-batch = "webapptester.batch:main"`。

- [ ] **步骤 1：编写失败的测试用例。** 模拟 `cli.run`，输出 PASS、FAIL、UNKNOWN 结果；断言按输入顺序调用三次、每项使用唯一输出目录、汇总保留所有状态和报告路径、含逗号的场景正确进行 CSV 转义。全部通过时退出码为 0，任一失败时为 1，没有失败但有 UNKNOWN 时为 2。验证未知键、空数组、无效提供者，以及 `max_steps` 超出 1–35 范围的情况。本单元测试不调用模型或浏览器。
- [ ] **步骤 2：验证红色。** 运行 `.venv/bin/python -m pytest tests/test_batch.py -q`；预期 `webapptester.batch` 导入失败。
- [ ] **步骤 3：实现。** 对批处理复用一次 `cli._make_run_directory`。向 `cli.run` 传递带有项目配置的 `argparse.Namespace`，使用唯一的项目目录作为其 `output` 基础。捕获意外的每项异常到 UNKNOWN 摘要条目中并继续；不要重新转换返回的 FAIL。通过 `csv.DictWriter` 原子写入摘要 JSON 和 CSV；包含计数和每项的 `scenario`、`url`、`provider`、`model`、`status`、`report_path`、`steps`、`reason`，但不包含操作值。尽可能使用批处理根目录下的相对路径。
- [ ] **步骤 4：验证绿色。** 运行 `.venv/bin/python -m pytest tests/test_batch.py -q` 和完整套件。运行一个包含两项的本地演示批处理，使用 Ollama 并检查两份报告及摘要；保留模型诚实的裁决不变。
- [ ] **步骤 5：文档化和提交。** README 包含完整的 `examples/demo-batch.json` 命令并描述退出状态。添加一个小示例文件 `examples/demo-batch.json`，包含两个演示场景，将其与命名文件一起暂存，并使用 `git commit -m "feat: run sequential scenario batches"` 进行提交。

## 自我审查

- 现有设计的 Streamlit、批处理 CLI、历史记录和导出要求对应任务 1–3。单独的 4gaBoards 计划将在本地侦察后涵盖特定目标的环境和示例。
- 仪表板读取报告而不是重新运行场景，因此打开结果永远不会改变目标应用程序的状态。
- 浏览器操作预算仍由 `Scenario`/`BrowserGateway` 为每个批处理项目拥有；批处理层没有浏览器 API。
