# WebAppTester

WebAppTester runs a bounded Playwright browser scenario through Planner,
Executor, and Verifier roles. Every observation includes visible text and a
screenshot; the final report is PASS, FAIL, or UNKNOWN and keeps evidence
grounded in those observations.

## Local demo

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m playwright install chromium
.venv/bin/python demo/app.py --port 8765
```

In another terminal, start Ollama and fetch the concrete local model:

```sh
ollama serve
ollama pull qwen2.5:3b
.venv/bin/webapptester --scenario 'Create project Alpha and verify it appears on dashboard and sidebar' --url http://127.0.0.1:8765 --provider ollama --model qwen2.5:3b --output runs/demo
```

The CLI creates a unique UTC timestamped child under the supplied output base
(`runs/demo/<timestamp>/`) and writes `report.json`, append-only `events.jsonl`,
PNG observations under `screenshots/`, and `trace.zip`. It exits 0 for PASS, 1 for
FAIL, and 2 for UNKNOWN. If the managed Playwright browser is unavailable,
the runner falls back to an installed Google Chrome channel.

To run two scenarios sequentially and export one summary, use the included
batch configuration:

```sh
.venv/bin/webapptester-batch examples/demo-batch.json --output runs/batch
```

Each item keeps its own run artifacts under one timestamped batch directory.
The directory contains `batch-summary.json` and `batch-summary.csv`; the
structured summaries include scenario metadata, status, report path, step
count, and reason without action input values. The batch command exits 0 only
when every item passes, 1 when at least one item fails, and 2 when no item
fails but at least one item is unknown.

DeepSeek is selectable with `--provider deepseek`; it uses
`deepseek-v4.1-flash` by default and requires `DEEPSEEK_API_KEY` in the
environment. No DeepSeek request is made without that key.

To inspect the newest trace:

```sh
.venv/bin/playwright show-trace "$(ls -td runs/demo/*/trace.zip | head -1)"
```

## Isolated 4gaBoards fixture

The integration fixture runs 4gaBoards 3.2.16 and PostgreSQL on
`127.0.0.1:3001`. It uses a separate Compose project and named volumes, so the
existing instance on port 3000 and its data are left unchanged.

Create the private first-run credentials once:

```sh
.venv/bin/python scripts/setup_4gaboards_local.py
```

The command creates `runs/4gaboards-local/instance.env` with mode `0600` and
prints the next Docker command without printing any credential values. Start
the fixture with the printed command, or run:

```sh
docker compose --env-file runs/4gaboards-local/instance.env \
  -p webapptester-4ga -f examples/4gaboards/compose.yaml up -d
```

The generated env file and Docker volumes are local-only. Keep the env file
private and do not commit it.

After the fixture is healthy, create a private browser login state in a
separate Playwright context:

```sh
.venv/bin/python scripts/bootstrap_4gaboards_auth.py
```

The command reads the local first-run credentials without printing them,
confirms the authenticated dashboard, and writes
`runs/4gaboards-local/auth.json` with mode `0600`. Use that state for a single
scenario with `--storage-state`:

```sh
.venv/bin/webapptester \
  --scenario "Open the dashboard" \
  --url http://127.0.0.1:3001/ \
  --storage-state runs/4gaboards-local/auth.json \
  --output runs/4gaboards-local/scenarios
```

Batch items may also include a local `storage_state` path. The state is loaded
into each new browser context and is never copied into reports, events, or
batch summaries:

```json
[
  {
    "scenario": "Open the dashboard",
    "url": "http://127.0.0.1:3001/",
    "storage_state": "runs/4gaboards-local/auth.json"
  }
]
```

Run the tests with:

```sh
.venv/bin/python -m pytest -q
```

## Results dashboard

Install the optional Streamlit UI and start the read-only dashboard with:

```sh
.venv/bin/pip install -e '.[test,ui]'
.venv/bin/streamlit run src/webapptester/dashboard.py --server.address 127.0.0.1 -- --runs-root runs
```

Open [http://127.0.0.1:8501](http://127.0.0.1:8501) to inspect the run history,
checkpoint evidence, actions, screenshots, and available report artifacts. The
`runs/` directory is local demo output and is ignored by Git; configure another
local root with `--runs-root` when needed. Screenshots and Playwright Trace are
raw page evidence and may contain sensitive page content; the dashboard keeps
them hidden until you explicitly select the raw-evidence reveal control.
