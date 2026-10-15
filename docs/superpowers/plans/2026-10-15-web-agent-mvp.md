# Web 应用自主测试 Agent 首阶段 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从自然语言场景启动一次可审计的 Playwright 测试，生成三态结论、逐项证据、截图、Trace 和操作台账，并能切换 Ollama 与 DeepSeek。

**Architecture:** Python CLI 驱动确定性 Controller。Planner、Executor、Verifier 只能通过统一模型接口交换 JSON 对象；BrowserGateway 独占 Playwright 页面与 35 步预算；RunStore 追加保存证据。先用本地演示网页和脚本模型证明协议，再做一次 Ollama 端到端调用。

**Tech Stack:** Python 3.14、Playwright Python、pytest、Python 标准库 HTTP/JSON、Ollama HTTP API、DeepSeek Chat Completions API。

## Global Constraints

- 浏览器交互只使用 Playwright Python SDK；不调用任意网页 JavaScript 作为动作。
- 每场景最多 35 次浏览器操作；失败尝试也计数，DOM 读取和截图不计数。
- 正式评测模型固定 `deepseek-v4.1-flash`；开发可使用本地 Ollama 模型。
- 必要检查点全部有保存的页面证据才可 PASS；执行异常与缺证据应为 UNKNOWN。
- 报告必须含判定依据、截图、Playwright Trace、操作轨迹、步数、耗时与 Token 统计。
- 密钥、运行产物、4gaBoards 本地配置和课程材料不提交 Git。
- 本计划只覆盖设计文档的首阶段；Streamlit、批量 CLI 和 4gaBoards 正式场景属于下一阶段。

---

## File map

| File | Responsibility |
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

## Task 1: Package and typed protocol

**Files:** Create `pyproject.toml`, `.gitignore`, `src/webapptester/__init__.py`, `src/webapptester/schema.py`, `tests/test_schema.py`.

**Interfaces:** Produce `Scenario(text: str, url: str, max_steps: int = 35)`, `Checkpoint(id: str, source: str, expected: str, required: bool = True)`, `Action(kind: str, locator: str = "", value: str = "", checkpoint_id: str = "")`, `Observation(id: str, url: str, text: str, screenshot: str, fingerprint: str)`, `Evidence(checkpoint_id: str, observation_id: str, quote: str, status: str)`, `Verdict(status: str, evidence: tuple[Evidence, ...], reason: str)`, and strict `parse_*` functions. Allowed action kinds: `goto`, `click`, `fill`, `select`, `scroll`, `drag`, `stop`.

- [ ] **Step 1: Write protocol tests.** In `tests/test_schema.py`, add:

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

- [ ] **Step 2: Verify red.** Run `python3 -m pytest tests/test_schema.py -q`; expect import failure for `webapptester`.
- [ ] **Step 3: Create the package.** Set `pyproject.toml` build system to setuptools, `requires-python = ">=3.11"`, dependencies `playwright>=1.55,<2`, optional test dependency `pytest>=8,<10`, package directory `src`, and script `webapptester = "webapptester.cli:main"`. Create immutable `@dataclass(frozen=True, slots=True)` types with the exact signatures above. Validate `1 <= max_steps <= 35`, nonempty scenario text and URL, action kind membership, and unique nonempty checkpoint IDs. `parse_action` and `parse_checkpoints` must reject unknown keys, wrong types, and missing required values; do not coerce arbitrary values to strings. Use this validation pattern:

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

- [ ] **Step 4: Verify green.** Run `python3 -m venv .venv`, `.venv/bin/pip install -e '.[test]'`, then `.venv/bin/python -m pytest tests/test_schema.py -q`; expect 3 passed. Add `.gitignore` entries `.venv/`, `__pycache__/`, `.pytest_cache/`, `runs/`, `.env`, `*.egg-info/`.
- [ ] **Step 5: Commit.** `git add pyproject.toml .gitignore src/webapptester/__init__.py src/webapptester/schema.py tests/test_schema.py && git commit -m "feat: define test run protocol"`.

## Task 2: Model providers and role boundary

**Files:** Create `src/webapptester/providers.py`, `src/webapptester/roles.py`, `tests/test_providers.py`, `tests/test_roles.py`.

**Interfaces:** Consume `parse_action`, `parse_checkpoints`, `Checkpoint`, `Observation`, `Evidence`. Produce `ModelProvider.complete_json(system: str, user: str) -> dict[str, object]`, `usage: TokenUsage`, `Planner.plan(scenario: Scenario) -> tuple[Checkpoint, ...]`, `Executor.next_action(scenario: Scenario, checkpoints: tuple[Checkpoint, ...], observation: Observation, history: tuple[Action, ...]) -> Action`, `Verifier.verify(checkpoints: tuple[Checkpoint, ...], observations: tuple[Observation, ...]) -> tuple[Evidence, ...]`. Define `TokenUsage(prompt: int = 0, completion: int = 0)` with additive update.

- [ ] **Step 1: Write provider and role tests.** Use `unittest.mock.patch("urllib.request.urlopen")` to verify Ollama sends `POST /api/chat` with `stream: false`, `format: "json"`, and DeepSeek sends `POST /chat/completions` with `response_format: {"type": "json_object"}` and Bearer key. A fake provider returning `{"checkpoints": [{"id":"c1","source":"create project","expected":"project exists","required":true}]}` must produce one parsed checkpoint; a fake provider returning `{"action":{"kind":"evaluate"}}` twice must raise `ModelOutputError`. In Python test data use `True`, not JSON `true`.
- [ ] **Step 2: Verify red.** Run `python3 -m pytest tests/test_providers.py tests/test_roles.py -q`; expect missing module imports.
- [ ] **Step 3: Implement provider transport.** Use `urllib.request.Request` and `urlopen(timeout=120)` with JSON bytes. Ollama URL defaults to `http://127.0.0.1:11434/api/chat`; parse `message.content` and `prompt_eval_count`/`eval_count`. DeepSeek URL defaults to `https://api.deepseek.com/chat/completions`; parse `choices[0].message.content` and `usage.prompt_tokens`/`completion_tokens`. Configure model names as constructor arguments; DeepSeek default is `deepseek-v4.1-flash`. Reject empty content and malformed JSON with `ModelOutputError`, preserving the underlying exception in `__cause__`. Never log the Authorization header. Request shape:

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

- [ ] **Step 4: Implement roles.** Planner prompt demands JSON `checkpoints` with exact source quote from the scenario, distinct IDs, expected visible state and `required` boolean. Executor prompt demands JSON `action` with one allowed kind, CSS or Playwright role locator syntax as specified by BrowserGateway, and no arbitrary code. Verifier prompt demands JSON `evidence` entries keyed by checkpoint and observation IDs, quote copied from stored visible text, and status `SAT`, `VIOLATED` or `UNKNOWN`. Validate all IDs, source fragments and quotes in Python; do not accept model claims without an observation. Retry malformed responses once with explicit schema correction, then raise `ModelOutputError`. Use the helper:

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

- [ ] **Step 5: Verify green.** Run `python3 -m pytest tests/test_providers.py tests/test_roles.py -q`; expect all pass.
- [ ] **Step 6: Commit.** `git add src/webapptester/providers.py src/webapptester/roles.py tests/test_providers.py tests/test_roles.py && git commit -m "feat: add structured model roles and providers"`.

## Task 3: Playwright gateway and evidence store

**Files:** Create `src/webapptester/browser.py`, `src/webapptester/store.py`, `tests/test_browser.py`, `tests/test_store.py`.

**Interfaces:** Produce `BrowserGateway(page: Page, max_steps: int)`, `steps: int`, `act(action: Action) -> ActionResult`, `observe(store: RunStore) -> Observation`; `ActionResult(action: Action, success: bool, error: str, steps: int)`. Produce `RunStore(root: Path)` with `append_event(dict)`, `save_observation(url: str, text: str, screenshot: bytes) -> Observation`, `write_report(dict) -> Path`. `RunStore` allocates unique observation IDs, writes screenshots under `root/screenshots/`, and appends `events.jsonl`.

- [ ] **Step 1: Write budget tests.** In `tests/test_browser.py`, use a fake page whose `get_by_role(...).click()` raises `TimeoutError`; assert failed click increases `steps` from 0 to 1. With `max_steps=1`, a second action raises `BudgetExhausted` without calling the page. A `stop` action does not consume a step. In `tests/test_store.py`, save two observations and assert IDs and files differ, earlier JSONL line remains intact, and URL/text/fingerprint are stored.
- [ ] **Step 2: Verify red.** Run `python3 -m pytest tests/test_browser.py tests/test_store.py -q`; expect import failures.
- [ ] **Step 3: Implement gateway.** `act` checks budget and increments count before dispatch. Support `goto` via `page.goto`, `click` and `fill` via `page.get_by_role` for locators formatted `role=<role>;name=<name>` or via `page.locator` for CSS, `select` via `select_option`, `scroll` via `mouse.wheel`, `drag` via source and destination locators separated by ` => `. Use 5-second default action timeout. Catch Playwright errors into `ActionResult.error`, never convert them to a product FAIL. Do not support `evaluate`. `observe` reads `page.url`, `page.locator("body").inner_text()[:20000]`, and `page.screenshot(full_page=True)`; store SHA-256 of URL plus text as fingerprint.
- [ ] **Step 4: Implement store.** Create directories with `mkdir(parents=True, exist_ok=True)`. Write each screenshot to `screenshots/observation-0001.png` and append metadata to `events.jsonl` using `json.dumps(..., ensure_ascii=False)` plus newline. Write `report.json` atomically via temporary file followed by `replace`. Exclude action `value` from persisted events to avoid saving passwords.
- [ ] **Step 5: Verify green.** Run `python3 -m pytest tests/test_browser.py tests/test_store.py -q`; expect all pass.
- [ ] **Step 6: Commit.** `git add src/webapptester/browser.py src/webapptester/store.py tests/test_browser.py tests/test_store.py && git commit -m "feat: enforce browser budget and save evidence"`.

## Task 4: Deterministic controller and verdict gate

**Files:** Create `src/webapptester/controller.py`, `tests/test_controller.py`.

**Interfaces:** Consume Task 1-3 types. Produce `Controller(planner, executor, verifier, browser, store).run(scenario: Scenario) -> Verdict`. The CLI owns Playwright browser/context and trace lifecycle; Controller never creates a second context. `verdict_from_evidence(checkpoints, evidence, observations) -> Verdict` is a pure function.

- [ ] **Step 1: Write verdict tests.** Cover: two required SAT items referencing existing observations yield PASS; any UNKNOWN yields UNKNOWN; an unreferenced SAT quote yields UNKNOWN; a VIOLATED item with direct quoted observation yields FAIL; action timeout alone yields UNKNOWN; budget exhaustion terminates at exactly 35 steps; repeated `(fingerprint, action)` three times stops UNKNOWN. Use fake roles/browser/store so tests do not call a model or browser.
- [ ] **Step 2: Verify red.** Run `python3 -m pytest tests/test_controller.py -q`; expect import failure.
- [ ] **Step 3: Implement verdict gate.** Index observations by ID. For each required checkpoint require one matching evidence entry; its quote must be a nonempty substring of that observation's text. Ignore unsupported evidence as UNKNOWN. Return FAIL only for supported `VIOLATED`; return PASS only when all required items are supported `SAT`; otherwise UNKNOWN. Include per-checkpoint evidence and a short reason in `Verdict`.
- [ ] **Step 4: Implement state machine.** Plan once, observe initial page, ask Executor for one action, arbitrate budget and repeat count, execute, append result event, observe again, ask Verifier, and stop on PASS/FAIL, `stop`, repeated state/action three times, or budget exhaustion. On model, browser, or validation exceptions write an UNKNOWN report with accumulated evidence; record exception class without secrets. Write `steps`, `elapsed_seconds`, `token_usage`, `trace_path`, `actions`, `observations`, `verdict` to `report.json`. Keep `GapRequest` as optional structured Verifier output; pass its checkpoint ID and requested evidence back to Executor in the next prompt, without granting browser access to Planner/Verifier.
- [ ] **Step 5: Verify green.** Run `python3 -m pytest tests/test_controller.py -q`; expect all pass.
- [ ] **Step 6: Commit.** `git add src/webapptester/controller.py tests/test_controller.py && git commit -m "feat: add evidence-gated controller"`.

## Task 5: Local demo, CLI and real browser smoke test

**Files:** Create `demo/app.py`, `src/webapptester/cli.py`, `tests/test_e2e.py`, `README.md`; modify `pyproject.toml` CLI entry as declared in Task 1 if needed.

**Interfaces:** `python3 demo/app.py --port 8765` serves a form with project-name input, Create button, dashboard card and sidebar link; state is deterministic within the demo process. `webapptester --scenario "..." --url http://127.0.0.1:8765 --provider ollama --model <installed-model> --output runs/demo` runs one scenario. `--provider deepseek` reads `DEEPSEEK_API_KEY` and defaults to `deepseek-v4.1-flash`.

- [ ] **Step 1: Write browser smoke test.** `tests/test_e2e.py` starts `demo/app.py` on a free loopback port, launches Playwright Chromium, navigates through BrowserGateway, fills project name, clicks Create, and asserts screenshot/Trace files exist and both dashboard and sidebar show the name. A scripted Planner/Executor/Verifier run through Controller must return PASS with saved evidence. A missing sidebar reference must return UNKNOWN.
- [ ] **Step 2: Verify red.** Run `python3 -m pytest tests/test_e2e.py -q`; expect missing demo or CLI behavior.
- [ ] **Step 3: Implement demo and CLI.** Demo uses `http.server.ThreadingHTTPServer`, local in-memory project list, HTML escaping, and POST redirects. CLI uses `argparse`, creates a timestamped run directory, starts Chromium with `sync_playwright()`, starts `context.tracing`, calls Controller, stops trace to `trace.zip` in `finally`, and prints status/report path. Exit code is 0 for PASS, 1 for FAIL, 2 for UNKNOWN. On missing Ollama service or missing `DEEPSEEK_API_KEY`, print a useful error and still persist an UNKNOWN report.
- [ ] **Step 4: Document exact startup.** README commands: `python3 -m venv .venv`, `.venv/bin/pip install -e '.[test]'`, `.venv/bin/python -m playwright install chromium`, `.venv/bin/python demo/app.py --port 8765`, `ollama serve`, `ollama pull qwen2.5:3b`, `.venv/bin/webapptester --scenario 'Create project Alpha and verify it appears on dashboard and sidebar' --url http://127.0.0.1:8765 --provider ollama --model qwen2.5:3b --output runs/demo`. Explain `DEEPSEEK_API_KEY` and trace viewing with `.venv/bin/playwright show-trace runs/demo/trace.zip`.
- [ ] **Step 5: Verify green and inspect artifact.** Run `python3 -m playwright install chromium`, `python3 -m pytest -q`, then one scripted end-to-end run and one live Ollama run. Inspect `report.json` for `steps <= 35`, nonempty checkpoint evidence for PASS, valid screenshot files and `trace.zip`. If Ollama output is invalid, record UNKNOWN and identify model/protocol limitation rather than changing verdict rules.
- [ ] **Step 6: Commit and push.** `git add demo/app.py src/webapptester/cli.py tests/test_e2e.py README.md pyproject.toml && git commit -m "feat: run auditable browser agent from CLI"`; then `git push origin main` after full test suite passes. Do not stage the course PPT/markdown or files under `/Users/loki/4gaBoards-test`.

## Self-review

- **Spec coverage:** Tasks 1–4 implement the three role protocol, controlled Playwright actions, 35-step budget, evidence ledger, trace/report fields and three-state gate. Task 5 supplies the CLI, deterministic demo, screenshots, Trace, Ollama live test and DeepSeek selectable transport. Streamlit, batch runs and target applications remain explicitly in phase two.
- **Placeholder scan:** Startup commands name the concrete `qwen2.5:3b` model and `runs/demo` output path.
- **Type consistency:** `Action`, `Observation`, `Evidence`, `Verdict`, `ModelProvider.complete_json`, `BrowserGateway.act/observe`, and `RunStore.save_observation/write_report` have one definition and one consumer path across tasks.

## API references

- [Playwright Python library](https://playwright.dev/python/docs/library)
- [Playwright tracing](https://playwright.dev/python/docs/trace-viewer-intro)
- [Ollama chat and JSON format](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [Ollama qwen2.5:3b model](https://ollama.com/library/qwen2.5%3A3b)
- [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek JSON output](https://api-docs.deepseek.com/guides/json_mode/)
