# Isolated 4gaBoards Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run WebAppTester against a local, isolated 4gaBoards 3.2.16 instance without exposing login credentials to the model or changing the user's existing instance.

**Architecture:** A committed Compose template starts a second 4gaBoards/PostgreSQL pair on loopback port 3001 with separate Docker volumes. A local setup command generates first-run administrator and database secrets under ignored `runs/4gaboards-local/`. A Playwright bootstrap command logs in and writes a local storage-state file; the single-run CLI and batch CLI load that state into each new browser context. Officially supplied scenario text remains external input, never hardcoded into role logic.

**Tech Stack:** Docker Compose/Colima, 4gaBoards 3.2.16, PostgreSQL 16 Alpine, Python 3.11+, Playwright, pytest.

## Global Constraints

- Preserve the existing `127.0.0.1:3000` 4gaBoards instance and its database. Use a separate Compose project name, volume names and `127.0.0.1:3001` port binding.
- Generate random first-run credentials locally. Store them with mode `0600` under ignored `runs/4gaboards-local/`; never commit or print secret values, cookies or storage state.
- Do not send credentials, cookies or storage state to Ollama or DeepSeek prompts, reports, events or dashboard exports.
- All application interactions during a scenario still pass through `BrowserGateway`; setup authentication happens before the scenario in a separate browser context.
- Keep the 35-operation maximum per scenario, including the initial navigation. Do not mislabel model failures as application failures.
- The exact five English course examples are not available locally. Use the one explicit `Course Test` example for integration and identify any new scenarios as locally authored smoke tests; add official examples when the user supplies them.

---

### Task 1: Reproducible isolated 4gaBoards instance

**Files:** Create `examples/4gaboards/compose.yaml`, `scripts/setup_4gaboards_local.py`, `tests/test_4gaboards_setup.py`; modify `README.md`.

**Interfaces:** `.venv/bin/python scripts/setup_4gaboards_local.py` creates `runs/4gaboards-local/instance.env` (0600) if absent and prints only the next Docker command. The Compose template reads `POSTGRES_PASSWORD`, `SECRET_KEY`, `DEFAULT_ADMIN_USERNAME`, `DEFAULT_ADMIN_EMAIL`, `DEFAULT_ADMIN_NAME`, and `DEFAULT_ADMIN_PASSWORD` from that env file. The 4gaBoards image is `ghcr.io/rargames/4gaboards:3.2.16`; Postgres is `public.ecr.aws/docker/library/postgres:16-alpine`.

- [ ] **Step 1: Write tests.** Assert setup creates mode-0600 env file, preserves it on repeat invocation, includes required variable names with nonempty random values, and does not print any value. Assert Compose template binds only `127.0.0.1:3001:1337` and uses distinct named volumes.
- [ ] **Step 2: Verify red.** Run `.venv/bin/python -m pytest tests/test_4gaboards_setup.py -q`; expect missing setup script/template.
- [ ] **Step 3: Implement.** Use `secrets.token_urlsafe` to generate database, session and admin secrets. Use exclusive file creation and `os.chmod(path, 0o600)`. Compose declares `db` and `app`, health check, `BASE_URL=http://127.0.0.1:3001`, and named volumes; do not include literal passwords. Do not read or modify `/Users/loki/4gaBoards-test`.
- [ ] **Step 4: Verify green and start.** Run focused/full tests; run setup and `docker compose --env-file runs/4gaboards-local/instance.env -p webapptester-4ga -f examples/4gaboards/compose.yaml up -d`; verify both containers healthy and `/login` responds on port 3001. Do not print env contents.
- [ ] **Step 5: Commit.** Commit only named source/test/docs files with `git commit -m "feat: provision isolated 4gaBoards test instance"`.

### Task 2: Private browser authentication state

**Files:** Create `scripts/bootstrap_4gaboards_auth.py`, `tests/test_4gaboards_auth.py`; modify `src/webapptester/cli.py`, `src/webapptester/batch.py`, `tests/test_e2e.py`, `README.md`.

**Interfaces:** Single-run CLI adds `--storage-state PATH` (optional). `run()` passes `storage_state=str(path)` to `browser.new_context()` only after confirming an existing regular file; missing/invalid state yields UNKNOWN report. Batch JSON items may include optional `storage_state` and pass it through to single-run CLI. Bootstrap command reads `runs/4gaboards-local/instance.env` locally, logs into `http://127.0.0.1:3001/login` with Playwright selectors, and saves `runs/4gaboards-local/auth.json` with mode 0600 after confirming an authenticated dashboard. Do not output state contents.

- [ ] **Step 1: Write tests.** Mock `browser.new_context` to assert the storage-state path is passed and absent/invalid paths become UNKNOWN without a provider call. Assert no storage-state data or path contents appear in report/events. Verify batch loader accepts the optional path and forwards it. Test bootstrap parsing/permission behavior without writing a real credential to tracked files.
- [ ] **Step 2: Verify red.** Run the covering CLI, batch and auth tests; expect unsupported `--storage-state` and missing bootstrap.
- [ ] **Step 3: Implement.** Validate storage-state file before Playwright startup. In bootstrap, read env with a parser that treats values as local secrets, use Playwright `get_by_role`/locators to fill login form, wait for dashboard rather than assuming a redirect alone proves authentication, save state with `context.storage_state(path=...)`, and chmod 0600. Keep the login context separate from scenario runs.
- [ ] **Step 4: Verify green.** Run focused/full tests. Execute bootstrap against the isolated instance; verify only that `auth.json` exists, mode 0600, and a fresh context using it opens the dashboard without seeing `/login`.
- [ ] **Step 5: Commit.** Commit only named files with `git commit -m "feat: load private browser auth state"`.

### Task 3: 4gaBoards integration smoke and evidence

**Files:** Create `examples/4gaboards/scenarios.json`, `tests/test_4gaboards_smoke.py`; modify `README.md`.

**Interfaces:** Scenario JSON includes the explicit course example “In the dashboard click Add project, enter the project name Course Test and confirm, then check the new project appears on both dashboard and sidebar.” plus clearly labelled local read-only smoke scenarios. Each item points to `http://127.0.0.1:3001/` and uses the ignored `runs/4gaboards-local/auth.json` storage state.

- [ ] **Step 1: Write a browser smoke test.** With the isolated service and auth state, open the authenticated dashboard via `BrowserGateway`, assert the Add project control is available, and take a real screenshot/Trace. Keep this test opt-in when Docker or auth state is unavailable so the standard unit suite remains deterministic.
- [ ] **Step 2: Verify red.** Run the opt-in smoke command; expect missing scenario/auth plumbing before Task 2 completion.
- [ ] **Step 3: Run scripted and model scenarios.** Perform one deterministic Playwright creation/verification flow with a unique local project name and two separate dashboard/sidebar assertions; save evidence in `runs/4gaboards-local/`. Run the explicit `Course Test` scenario once with `qwen2.5:3b`, preserving its honest PASS/FAIL/UNKNOWN report. Do not pre-seed the target answer or alter verdict rules. Avoid repeated `Course Test` creations; use a fresh instance or unique local names for subsequent smoke runs.
- [ ] **Step 4: Verify artifacts.** Confirm report step count ≤35, screenshots and Trace exist, and evidence quotes map to saved observations. If the small model fails, distinguish model execution failure from 4gaBoards failure in the report/README. Run the full test suite.
- [ ] **Step 5: Commit.** Commit the scenario manifest, opt-in smoke test and README changes only; do not commit generated reports, credentials, Docker volumes or course files.

## Self-review

- The design's 4gaBoards integration requirement maps to Tasks 1–3. Exact five official examples remain an external input; the available single example is implemented without claiming the others exist.
- Login is a local fixture; the model never receives a password. Browser context creation receives only the storage-state file path.
- The existing instance and its data remain untouched because Compose uses a separate project and port.
