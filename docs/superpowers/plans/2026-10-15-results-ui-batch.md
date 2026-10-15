# Results UI and Batch Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** View existing test runs in a local Streamlit page, download their evidence, and run a list of scenarios sequentially from the CLI.

**Architecture:** `results.py` is a read-only boundary around run artifacts and does not import Streamlit. `dashboard.py` renders its validated records. `batch.py` calls the existing single-scenario runner once per item and writes a batch summary; each item retains its own report, screenshots, events, and Trace.

**Tech Stack:** Python 3.11+, Streamlit 1.55+, existing Playwright and pytest stack.

## Global Constraints

- Preserve the evidence-gated PASS/FAIL/UNKNOWN semantics and the maximum of 35 browser operations **per scenario**.
- The dashboard reads only files under the configured local `runs/` root. Never accept a browser-supplied arbitrary path for a screenshot or Trace.
- Run artifacts are read-only in the dashboard. A corrupt or incomplete run is shown as unavailable, not silently treated as PASS.
- Batch execution is sequential. A failed item does not prevent later items from running; its status remains FAIL or UNKNOWN according to its own report.
- Never include action input values, local credentials, or environment secrets in UI exports or batch summaries.
- Keep the existing course PPT and markdown files untracked and unstaged.

---

### Task 1: Read and validate local run artifacts

**Files:** Create `src/webapptester/results.py`, `tests/test_results.py`.

**Interfaces:** `RunRecord` holds `directory: Path`, `status: str`, `reason: str`, `steps: int`, `elapsed_seconds: float`, `token_usage: dict[str, int]`, `evidence: tuple[dict[str, str], ...]`, `observations: tuple[dict[str, object], ...]`, `actions: tuple[dict[str, str], ...]`, `trace: Path | None`. `list_runs(root: Path) -> tuple[RunRecord, ...]` scans only `report.json` under `root`, newest first. `load_run(root: Path, report_path: Path) -> RunRecord` validates the report schema and rejects paths outside `root`. `artifact_path(root: Path, run: RunRecord, path_text: str) -> Path | None` resolves only existing files inside `run.directory` and `root`.

- [ ] **Step 1: Write failing tests.** In `tests/test_results.py`, create valid PASS and UNKNOWN report fixtures, a corrupt report, and a report whose screenshot path uses `../../`. Assert newest-first history, preserved statuses/evidence, corrupt report omission or explicit unavailable record, and `artifact_path(...) is None` for escape paths. Test absolute artifact paths from older reports only when they resolve inside the root.
- [ ] **Step 2: Verify red.** Run `.venv/bin/python -m pytest tests/test_results.py -q`; expect import failure for `webapptester.results`.
- [ ] **Step 3: Implement.** Use `Path.resolve()` and `Path.is_relative_to()` before reading files. Parse JSON with `json.load`, require `verdict.status in {"PASS", "FAIL", "UNKNOWN"}`, require `steps` to be a nonnegative integer at most 35, and require `evidence`, `actions`, `observations` to be lists of dictionaries. Skip malformed reports but never mutate them. Return `None` for missing or escaping screenshot/Trace paths. When an artifact path in an older report is relative to the repository working directory, resolve it against both `Path.cwd()` and the run directory, accepting only a candidate inside the configured root.
- [ ] **Step 4: Verify green.** Run `.venv/bin/python -m pytest tests/test_results.py -q` and `.venv/bin/python -m pytest -q`.
- [ ] **Step 5: Commit.** `git add src/webapptester/results.py tests/test_results.py && git commit -m "feat: load local run history safely"`.

### Task 2: Streamlit results dashboard

**Files:** Create `src/webapptester/dashboard.py`, `tests/test_dashboard.py`; modify `pyproject.toml`, `README.md`.

**Interfaces:** `render(root: Path) -> None` consumes Task 1's `list_runs` and `artifact_path`. Launch with `.venv/bin/streamlit run src/webapptester/dashboard.py -- --runs-root runs`. Add optional dependency group `ui = ["streamlit>=1.55,<2"]`.

- [ ] **Step 1: Write failing tests.** Use `streamlit.testing.v1.AppTest.from_file(...)` on a fixture `runs/` root. Assert one PASS and one FAIL/UNKNOWN run appear, selected run shows status, reason, steps, token counts and per-checkpoint evidence, and a missing screenshot/Trace is labelled unavailable without an exception. Check the empty-root state. Do not assert Streamlit's internal HTML.
- [ ] **Step 2: Verify red.** Run `.venv/bin/python -m pytest tests/test_dashboard.py -q`; expect missing dashboard module or missing UI dependency.
- [ ] **Step 3: Implement.** Parse `--runs-root` only in the `__main__` entrypoint. In `render`, show a history selector/table and selected run summary with `st.metric`, evidence rows with observation IDs and quotes, action timeline, associated screenshots with `st.image`, and download buttons for `report.json`, `events.jsonl`, and `trace.zip` only when `artifact_path` accepts them. Use `st.warning` for missing artifacts. Keep the page read-only and label scripted runs distinctly when metadata is unavailable; never claim every PASS came from an LLM.
- [ ] **Step 4: Verify green.** Install `.venv/bin/pip install -e '.[test,ui]'`, then run `.venv/bin/python -m pytest tests/test_dashboard.py -q` and the full suite. Start Streamlit locally and verify the PASS and FAIL demo reports render without Python exceptions.
- [ ] **Step 5: Document and commit.** README must give the install/start command, `http://127.0.0.1:8501`, and explain that `runs/` is local and ignored by Git. Commit only the four named files with `git commit -m "feat: view run evidence in Streamlit"`.

### Task 3: Sequential batch CLI and summary export

**Files:** Create `src/webapptester/batch.py`, `tests/test_batch.py`; modify `pyproject.toml`, `README.md`.

**Interfaces:** `load_batch(path: Path) -> tuple[dict[str, object], ...]` accepts a JSON array of objects with exact keys `scenario`, `url`, optional `provider`, `model`, `max_steps`; nonempty strings for `scenario`/`url`, provider only `ollama` or `deepseek`, and integer steps from 1 through 35. `run_batch(items, output_root: Path) -> Path` executes the existing `cli.run` sequentially and writes `batch-summary.json` plus `batch-summary.csv` under one timestamped batch directory. Register `webapptester-batch = "webapptester.batch:main"`.

- [ ] **Step 1: Write failing tests.** Mock `cli.run` with PASS, FAIL, UNKNOWN outcomes; assert three calls in input order, unique per-item output bases, all statuses and report paths in the summary, CSV escaping for a scenario containing a comma, and exit code 0 only when all items PASS, 1 when any FAIL, else 2 when any UNKNOWN. Validate unknown keys, empty array, invalid provider, and `max_steps` outside 1–35. Keep model calls and browsers out of this unit test.
- [ ] **Step 2: Verify red.** Run `.venv/bin/python -m pytest tests/test_batch.py -q`; expect import failure for `webapptester.batch`.
- [ ] **Step 3: Implement.** Reuse `cli._make_run_directory` once for the batch. Pass an `argparse.Namespace` with item configuration into `cli.run`, using a unique item directory as its `output` base. Catch an unexpected per-item exception into an UNKNOWN summary entry and continue; do not recast a returned FAIL. Write summary JSON atomically and CSV via `csv.DictWriter`; include counts and per-item `scenario`, `url`, `provider`, `model`, `status`, `report_path`, `steps`, `reason` but no action values. Use relative paths beneath batch root where possible.
- [ ] **Step 4: Verify green.** Run `.venv/bin/python -m pytest tests/test_batch.py -q` and full suite. Run a two-item local demo batch with Ollama and inspect both reports and the summary; leave the model's honest verdicts unchanged.
- [ ] **Step 5: Document and commit.** README includes a complete `examples/demo-batch.json` command and describes exit status. Add a small example file `examples/demo-batch.json` with two demo scenarios, stage it alongside the named files, and commit with `git commit -m "feat: run sequential scenario batches"`.

## Self-review

- Existing design's Streamlit, batch CLI, history and export requirements map to Tasks 1–3. A separate 4gaBoards plan will cover target-specific environment and examples after local reconnaissance.
- The dashboard reads reports rather than rerunning scenarios, so opening results never changes target application state.
- Browser action budgets remain owned by `Scenario`/`BrowserGateway` for each batch item; the batch layer has no browser API.
