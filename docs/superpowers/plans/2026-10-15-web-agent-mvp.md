# Web 应用自主测试 Agent 首阶段实施计划

> **面向执行计划的智能体：** 按任务执行本计划时，使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 技能；步骤使用复选框（`- [ ]`）跟踪。

**目标：** 从自然语言场景启动一次可审计的 Playwright 测试，生成三态结论、逐项证据、截图、Trace 和操作台账，并能切换 Ollama 与 DeepSeek。

**架构：** Python CLI 驱动确定性 Controller。Planner、Executor、Verifier 只能通过统一模型接口交换 JSON 对象；BrowserGateway 独占 Playwright 页面与 35 步预算；RunStore 追加保存证据。先用本地演示网页和脚本模型证明协议，再做一次 Ollama 端到端调用。

**技术栈：** Python 3.14、Playwright Python、pytest、Python 标准库 HTTP/JSON、Ollama HTTP API、DeepSeek Chat Completions API。

## 全局约束

- 浏览器交互只使用 Playwright Python SDK；不调用任意网页 JavaScript 作为动作。
- 每场景最多 35 次浏览器操作；失败尝试也计数，DOM 读取和截图不计数。
- 正式评测模型固定 `deepseek-v4.1-flash`；开发可使用本地 Ollama 模型。
- 必要检查点全部有保存的页面证据才可 PASS；执行异常与缺证据应为 UNKNOWN。
- 报告必须含判定依据、截图、Playwright Trace、操作轨迹、步数、耗时与 Token 统计。
- 密钥、运行产物、4gaBoards 本地配置和课程材料不提交 Git。
- 本计划只覆盖设计文档的首阶段；Streamlit、批量 CLI 和 4gaBoards 正式场景属于下一阶段。

---

## 文件索引

| 文件 | 职责 |
| --- | --- |
| `pyproject.toml` | Python 包元数据、Playwright 与 pytest 依赖、CLI 入口 |
| `src/webapptester/schema.py` | 不可变场景、检查点、动作、观察、证据和结论类型；严格 JSON 校验 |
| `src/webapptester/providers.py` | Ollama 与 DeepSeek HTTP 适配，统一 `complete_json` 接口和 Token 计数 |
| `src/webapptester/browser.py` | 唯一 Playwright 动作入口、预算、失败计数、观察与截图 |
| `src/webapptester/store.py` | 运行目录、追加 JSONL 台账、截图路径和最终报告 |
| `src/webapptester/roles.py` | 三个角色的提示、结构化解析和有限重试 |
| `src/webapptester/controller.py` | 确定性状态机、预算仲裁、证据门控、循环终止 |
| `src/webapptester/cli.py` | 单场景 CLI、配置、退出码与摘要 |
| `demo/app.py` | 本地确定性项目创建网页与两个可核验位置 |
| `tests/` | 协议、预算、三态判定和真实 Playwright 冒烟测试 |

## 任务 1：打包和类型化协议

**文件：** 创建 `pyproject.toml`, `.gitignore`, `src/webapptester/__init__.py`, `src/webapptester/schema.py`, `tests/test_schema.py`。

**接口：** 生成 `Scenario(text: str, url: str, max_steps: int = 35)`、`Checkpoint(id: str, source: str, expected: str, required: bool = True)`、`Action(kind: str, locator: str = "", value: str = "", checkpoint_id: str = "")`、`Observation(id: str, url: str, text: str, screenshot: str, fingerprint: str)`、`Evidence(checkpoint_id: str, observation_id: str, quote: str, status: str)`、`Verdict(status: str, evidence: tuple[Evidence, ...], reason: str)` 以及严格的 `parse_*` 函数。允许的操作类型：`goto`、`click`、`fill`、`select`、`scroll`、`drag`、`stop`。
- [ ] **步骤 1：编写协议测试。** 在 `tests/test_schema.py` 中添加：

```python
import pytest
from webapptester.schema import Action, Scenario, parse_action, parse_checkpoints

def test_scenario_rejects_budget_over_course_limit():
    with pytest.raises(ValueError):
        Scenario("create a project", "http://localhost:3000", 36)

def test_action_rejects_uncontrolled_code():
    with pytest.raises(ValueError):
        parse_action({"kind": "evaluate", "locator": "document.body.remove()"})

def test_checkpoint_requires_source_mapping():
    with pytest.raises(ValueError):
        parse_checkpoints([{"id": "c1", "source": "", "expected": "project exists"}])
```

- [ ] **步骤 2：验证红色。** 运行 `python3 -m pytest tests/test_schema.py -q`；预期 `webapptester` 导入失败。
- [ ] **步骤 3：创建包。**将 `pyproject.toml` 构建系统设置为 setuptools，`requires-python = ">=3.11"`，依赖项为 `playwright>=1.55,<2`，可选测试依赖项为 `pytest>=8,<10`，包目录为 `src`。当 `cli.py` 存在时，将 `webapptester` 控制台脚本注册留待任务 5 处理。创建具有上述确切签名的不可变 `@dataclass(frozen=True, slots=True)` 类型。验证 `1 <= max_steps <= 35`、非空场景文本和 URL、操作种类成员资格以及唯一的非空检查点 ID。`parse_action` 和 `parse_checkpoints` 必须拒绝未知键、错误类型和缺失的必需值；不要将任意值强制转换为字符串。使用此验证模式：

```python
def parse_action(raw: dict[str, object]) -> Action:
    allowed = {"kind", "locator", "value", "checkpoint_id"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("invalid action fields")
    if not isinstance(raw.get("kind"), str):
        raise ValueError("action kind must be a string")
    for key in ("locator", "value", "checkpoint_id"):
        if key in raw and not isinstance(raw[key], str):
            raise ValueError(f"{key} must be a string")
    return Action(**raw)
```

- [ ] **步骤 4：验证绿色。**运行 `python3 -m venv .venv`、`.venv/bin/pip install -e '.[test]'`，然后运行 `.venv/bin/python -m pytest tests/test_schema.py -q`；预期通过 3 项。添加 `.gitignore` 条目 `.venv/`、`__pycache__/`、`.pytest_cache/`、`runs/`、`.env`、`*.egg-info/`。
- [ ] **步骤 5：提交。**`git add pyproject.toml .gitignore src/webapptester/__init__.py src/webapptester/schema.py tests/test_schema.py && git commit -m "feat: define test run protocol"`。

## 任务 2：模型提供商与角色边界

**文件：** 创建 `src/webapptester/providers.py`、`src/webapptester/roles.py`、`tests/test_providers.py`、`tests/test_roles.py`。

**接口：** 消耗 `parse_action`、`parse_checkpoints`、`Checkpoint`、`Observation`、`Evidence`。生成 `ModelProvider.complete_json(system: str, user: str) -> dict[str, object]`、`usage: TokenUsage`、`Planner.plan(scenario: Scenario) -> tuple[Checkpoint, ...]`、`Executor.next_action(scenario: Scenario, checkpoints: tuple[Checkpoint, ...], observation: Observation, history: tuple[Action, ...]) -> Action`、`Verifier.verify(checkpoints: tuple[Checkpoint, ...], observations: tuple[Observation, ...]) -> tuple[Evidence, ...]`。使用累加更新定义 `TokenUsage(prompt: int = 0, completion: int = 0)`。
- [ ] **步骤 1：编写 provider 和 role 测试。**使用 `unittest.mock.patch("urllib.request.urlopen")` 验证 Ollama 发送 `POST /api/chat`（包含 `stream: false`、`format: "json"`），以及 DeepSeek 发送 `POST /chat/completions`（包含 `response_format: {"type": "json_object"}` 和 Bearer key）。返回 `{"checkpoints": [{"id":"c1","source":"create project","expected":"project exists","required":true}]}` 的虚假 provider 必须生成一个解析后的检查点；返回两次 `{"action":{"kind":"evaluate"}}` 的虚假 provider 必须抛出 `ModelOutputError`。在 Python 测试数据中使用 `True`，而非 JSON `true`。
- [ ] **步骤 2：验证失败阶段。**运行 `python3 -m pytest tests/test_providers.py tests/test_roles.py -q`；预期结果为缺少模块导入。
- [ ] **步骤 3：实现提供者传输。**使用 `urllib.request.Request` 和 `urlopen(timeout=120)` 配合 JSON 字节数据。Ollama URL 默认为 `http://127.0.0.1:11434/api/chat`；解析 `message.content` 和 `prompt_eval_count`/`eval_count`。DeepSeek URL 默认为 `https://api.deepseek.com/chat/completions`；解析 `choices[0].message.content` 和 `usage.prompt_tokens`/`completion_tokens`。将模型名称配置为构造函数参数；DeepSeek 的默认值为 `deepseek-v4.1-flash`。拒绝空内容以及格式错误的 JSON，使用 `ModelOutputError` 处理，并在 `__cause__` 中保留底层异常。切勿记录 Authorization 标头。请求形状：

```python
payload = {"model": self.model, "messages": [
    {"role": "system", "content": system},
    {"role": "user", "content": user}],
    "stream": False, "format": "json"}  # Ollama
```

```python
payload = {"model": self.model, "messages": [
    {"role": "system", "content": system},
    {"role": "user", "content": user}],
    "stream": False, "response_format": {"type": "json_object"}}  # DeepSeek
```

- [ ] **步骤 4：实现角色。** Planner 提示词要求 JSON `checkpoints`，其中必须包含来自场景的精确源引用、不同的 ID、预期的可见状态以及 `required` 布尔值。Executor 提示词要求 JSON `action`，其中仅允许一种类型，即由 BrowserGateway 指定的 CSS 或 Playwright 角色定位器语法，且不得包含任意代码。Verifier 提示词要求 JSON `evidence` 条目以检查点和观察 ID 为键，引用自存储的可见文本，以及状态 `SAT`、`VIOLATED` 或 `UNKNOWN`。在 Python 中验证所有 ID、源片段和引用；不接受没有观察结果的模型声明。重试一次格式错误的响应并显式修正模式，然后抛出 `ModelOutputError`。使用辅助工具：

```python
def retry_parse(provider, system, user, parse):
    for attempt in range(2):
        try:
            return parse(provider.complete_json(system, user))
        except (ValueError, KeyError, TypeError) as error:
            if attempt:
                raise ModelOutputError("invalid role output") from error
            user += f"\nReturn corrected JSON only. Error: {error}"
```

- [ ] **步骤 5：验证绿色。**运行 `python3 -m pytest tests/test_providers.py tests/test_roles.py -q`；预期全部通过。
- [ ] **步骤 6：提交。** `git add src/webapptester/providers.py src/webapptester/roles.py tests/test_providers.py tests/test_roles.py && git commit -m "feat: add structured model roles and providers"`。

## 任务 3：Playwright 网关和证据存储库

**文件：** 创建 `src/webapptester/browser.py`、`src/webapptester/store.py`、`tests/test_browser.py`、`tests/test_store.py`。

**接口：** 生成 `BrowserGateway(page: Page, max_steps: int)`、`steps: int`、`act(action: Action) -> ActionResult`、`observe(store: RunStore) -> Observation`；`ActionResult(action: Action, success: bool, error: str, steps: int)`。使用 `append_event(dict)`、`save_observation(url: str, text: str, screenshot: bytes) -> Observation`、`write_report(dict) -> Path` 生成 `RunStore(root: Path)`。`RunStore` 分配唯一的观察 ID，在 `root/screenshots/` 下写入截图，并追加 `events.jsonl`。
- [ ] **步骤 1：编写预算测试。** 在 `tests/test_browser.py` 中，使用一个虚假页面，其 `get_by_role(...).click()` 会引发 `TimeoutError`；断言失败的点击操作将 `steps` 从 0 增加到 1。使用 `max_steps=1` 时，第二个操作会抛出 `BudgetExhausted`，而无需调用页面。`stop` 操作不消耗步骤。在 `tests/test_store.py` 中，保存两个观察结果并断言 ID 和文件不同，早期的 JSONL 行保持完整，同时存储 URL、文本和指纹。
- [ ] **第 2 步：验证红色。**运行 `python3 -m pytest tests/test_browser.py tests/test_store.py -q`；预期出现导入失败。
- [ ] **步骤 3：实现网关。** `act` 在执行前检查预算并增加计数。`goto` 调用 `page.goto`；`click` 和 `fill` 可使用格式为 `role=<role>;name=<name>` 的 `page.get_by_role` 定位器，也可使用 `page.locator` 的 CSS 定位器。`select` 调用 `select_option`，`scroll` 调用 `mouse.wheel`，`drag` 使用由 ` => ` 分隔的源和目标定位器。默认操作超时为 5 秒。将 Playwright 错误记录在 `ActionResult.error`，不能转为应用 FAIL。不支持 `evaluate`。`observe` 读取 `page.url`、`page.locator("body").inner_text()[:20000]` 和 `page.screenshot(full_page=True)`；以 URL 与文本的 SHA-256 哈希值作为状态指纹。
- [ ] **步骤 4：实现存储。** 使用 `mkdir(parents=True, exist_ok=True)` 创建目录。将截图写入 `screenshots/observation-0001.png` 等独立文件，并使用 `json.dumps(..., ensure_ascii=False)` 加换行符向 `events.jsonl` 追加元数据。先写临时文件，再调用 `replace` 原子写入 `report.json`。持久化事件中排除动作的 `value`，避免保存密码。
- [ ] **步骤 5：验证绿色。** 运行 `python3 -m pytest tests/test_browser.py tests/test_store.py -q`；预期全部通过。
- [ ] **步骤 6：提交。** `git add src/webapptester/browser.py src/webapptester/store.py tests/test_browser.py tests/test_store.py && git commit -m "feat: enforce browser budget and save evidence"`。

## 任务 4：确定性控制器和裁决门

**文件：** 创建 `src/webapptester/controller.py`、`tests/test_controller.py`。

**接口：** 使用任务 1–3 定义的类型，提供 `Controller(planner, executor, verifier, browser, store).run(scenario: Scenario) -> Verdict`。CLI 拥有 Playwright 浏览器/上下文的追踪生命周期；Controller 永远不会创建第二个上下文。`verdict_from_evidence(checkpoints, evidence, observations) -> Verdict` 是一个纯函数。

- [ ] **步骤 1：编写判定测试。** 覆盖：两个必需检查点均有实际观察支持的 SAT 时为 PASS；存在 UNKNOWN 时为 UNKNOWN；无法对应实际观察的 SAT 引用为 UNKNOWN；有直接页面引用支持的 VIOLATED 为 FAIL；仅动作超时时为 UNKNOWN；预算耗尽时恰好停在 35 步；同一 `(fingerprint, action)` 重复三次时以 UNKNOWN 停止。使用模拟角色、浏览器和存储，不调用真实模型或浏览器。
- [ ] **步骤 2：验证红色。** 运行 `python3 -m pytest tests/test_controller.py -q`；预期导入失败。
- [ ] **步骤 3：实现判定门控。** 按 ID 索引观察。每个必需检查点都须有匹配的证据，且引用必须是对应观察文本的非空子串。无依据的证据按 UNKNOWN 处理。只有有依据的 `VIOLATED` 才返回 FAIL；所有必需检查点均为有依据的 `SAT` 时才返回 PASS；其余情况返回 UNKNOWN。`Verdict` 包含逐检查点证据和简短理由。
- [ ] **步骤 4：实现状态机。** 规划一次，观察初始页面，向 Executor 请求一个动作，仲裁预算和重复次数，执行，追加结果事件，再次观察，询问 Verifier，并在 PASS/FAIL、`stop`、重复状态/动作三次或预算耗尽时停止。在模型、浏览器或验证异常时，使用累积证据编写 UNKNOWN 报告；记录异常类但不包含密钥。编写 `steps`, `elapsed_seconds`, `token_usage`, `trace_path`, `actions`, `observations`, `verdict` 至 `report.json`。将 `GapRequest` 保留为可选的结构化 Verifier 输出；在下一个提示中将其检查点 ID 和请求的证据返回给 Executor，而不授予 Planner/Verifier 浏览器访问权限。
- [ ] **步骤 5：验证绿色。** 运行 `python3 -m pytest tests/test_controller.py -q`；预期全部通过。
- [ ] **步骤 6：提交。** `git add src/webapptester/controller.py tests/test_controller.py && git commit -m "feat: add evidence-gated controller"`。

## 任务 5：本地演示、CLI 和真实浏览器冒烟测试

**文件：** 创建 `demo/app.py`、`src/webapptester/cli.py`、`tests/test_e2e.py`、`README.md`；修改 `pyproject.toml` 以注册 CLI 入口点。

**接口：** `python3 demo/app.py --port 8765` 提供包含项目名称输入框、创建按钮、仪表板卡片和侧边栏链接的表单；演示过程中的状态是确定性的。`webapptester --scenario "..." --url http://127.0.0.1:8765 --provider ollama --model <installed-model> --output runs/demo` 运行一个场景。`--provider deepseek` 读取 `DEEPSEEK_API_KEY` 并默认为 `deepseek-v4.1-flash`。

- [ ] **步骤 1：编写浏览器冒烟测试。** `tests/test_e2e.py` 在空闲的回环端口上启动 `demo/app.py`，启动 Playwright Chromium，通过 BrowserGateway 导航，填写项目名称，点击创建，并断言截图/追踪文件存在且仪表板和侧边栏均显示该名称。通过 Controller 执行的脚本化 Planner/Executor/Verifier 运行必须返回 PASS 并保存证据。缺少侧边栏引用时必须返回 UNKNOWN。
- [ ] **步骤 2：验证红色。**运行 `python3 -m pytest tests/test_e2e.py -q`；预期结果为缺少演示或 CLI 行为。
- [ ] **步骤 3：实现演示和 CLI。**演示使用 `http.server.ThreadingHTTPServer`，本地内存项目列表、HTML 转义和 POST 重定向。CLI 使用 `argparse`，创建带时间戳的运行目录，启动 Chromium（使用 `sync_playwright()`），启动 `context.tracing`，调用 Controller，在 `finally` 中将跟踪停止到 `trace.zip`，并打印状态/报告路径。一旦 `cli.py` 存在，就在 `pyproject.toml` 中注册 `webapptester = "webapptester.cli:main"`。退出代码为 0 表示 PASS，1 表示 FAIL，2 表示 UNKNOWN。如果缺少 Ollama 服务或缺少 `DEEPSEEK_API_KEY`，打印有用的错误信息并仍然持久化 UNKNOWN 报告。
- [ ] **步骤 4：记录确切的启动过程。** README 命令：`python3 -m venv .venv`, `.venv/bin/pip install -e '.[test]'`, `.venv/bin/python -m playwright install chromium`, `.venv/bin/python demo/app.py --port 8765`, `ollama serve`, `ollama pull qwen2.5:3b`, `.venv/bin/webapptester --scenario 'Create project Alpha and verify it appears on dashboard and sidebar' --url http://127.0.0.1:8765 --provider ollama --model qwen2.5:3b --output runs/demo`。解释 `DEEPSEEK_API_KEY` 并使用 `.venv/bin/playwright show-trace runs/demo/trace.zip` 追踪查看。
- [ ] **步骤 5：验证绿色状态并检查工件。**运行 `python3 -m playwright install chromium`、`python3 -m pytest -q`，然后执行一次脚本化的端到端运行和一次实时 Ollama 运行。检查 `report.json` 以获取 `steps <= 35`，确认非空检查点证据为 PASS，验证截图文件及 `trace.zip`。如果 Ollama 输出无效，记录 UNKNOWN 并识别模型/协议限制，而不是更改裁决规则。
- [ ] **步骤 6：提交并推送。**`git add demo/app.py src/webapptester/cli.py tests/test_e2e.py README.md pyproject.toml && git commit -m "feat: run auditable browser agent from CLI"`；然后在完整测试套件通过后执行 `git push origin main`。不要将课程 PPT/markdown 或位于 `/Users/loki/4gaBoards-test` 下的文件纳入暂存区。

## 自我审查

- **规范覆盖范围：**任务 1–4 实现了三种角色协议、受控的 Playwright 操作、35 步预算、证据账本、跟踪/报告字段以及三态门控机制。任务 5 提供了 CLI、确定性演示、截图、Trace、Ollama 实时测试和可选的 DeepSeek 传输方式。Streamlit、批量运行和目标应用程序明确保留在第二阶段。
- **占位符检查：** 启动命令指定具体的 `qwen2.5:3b` 模型和 `runs/demo` 输出路径。
- **类型一致性：** `Action`、`Observation`、`Evidence`、`Verdict`、`ModelProvider.complete_json`、`BrowserGateway.act/observe` 和 `RunStore.save_observation/write_report` 在任务中各有一个定义和使用路径。

## API 参考

- [Playwright Python 库 ](https://playwright.dev/python/docs/library)
- [Playwright 追踪 ](https://playwright.dev/python/docs/trace-viewer-intro)
- [Ollama 聊天和 JSON 格式 ](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [Ollama qwen2.5:3b 模型 ](https://ollama.com/library/qwen2.5%3A3b)
- [DeepSeek 聊天补全](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)
