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

DeepSeek is selectable with `--provider deepseek`; it uses
`deepseek-v4.1-flash` by default and requires `DEEPSEEK_API_KEY` in the
environment. No DeepSeek request is made without that key.

To inspect the newest trace:

```sh
.venv/bin/playwright show-trace "$(ls -td runs/demo/*/trace.zip | head -1)"
```

Run the tests with:

```sh
.venv/bin/python -m pytest -q
```
