# B0--B3 ablation harness

The formal evaluation scenarios are supplied separately by the evaluator. The
repository intentionally does not contain the 36 scenario texts, expected
answers, or a claimed score. Put the released manifest in a private path with
one object per scenario:

```json
[
  {
    "id": "scenario-01",
    "scenario": "the natural-language scenario supplied by the evaluator",
    "url": "http://127.0.0.1:3000/",
    "storage_state": "auth.json",
    "max_steps": 35
  }
]
```

`storage_state` is optional and resolves relative to the private manifest.
Create it separately for each authenticated test application. Keep both the
manifest and browser state outside Git; the state contains login cookies.

Then run the local Ollama comparison with an output directory outside the
repository's ignored demo runs:

```sh
.venv/bin/webapptester-ablation \
  /private/path/formal-scenarios.json \
  --output /private/path/webapptester-ablation \
  --model qwen2.5:3b
```

The command executes the same manifest under each condition and writes one
report per scenario plus `ablation-summary.json`. It does not invoke a paid
provider. The API is also available through `AblationHarness` and
`AblationConfig` in `webapptester.ablation` when an evaluator already has a
runner or fixture manager.

## Conditions

| Condition | Evidence contract | Gap feedback | Budget arbitration |
| --- | --- | --- | --- |
| B0 | off | off | off |
| B1 | on | off | off |
| B2 | on | on | off |
| B3 | on | on | on |

B0 keeps ordinary Planner, Executor, and Verifier roles and uses the
Verifier's structured checkpoint statuses without the B1 provenance checks.
B1 requires quotes and checkpoint claims to be grounded in captured
observations and their declared regions. B2 passes a structured missing-evidence
request from Verifier to Executor. B3 applies the existing hard action cap and
declines a gap request when its declared estimated cost exceeds the remaining
budget. That is an explainable heuristic, not a claim that future reward can
be predicted exactly.

The summary reports PASS, FAIL, UNKNOWN, evidence coverage, browser steps,
token counts, and elapsed time per condition. Interpret UNKNOWN as an
inconclusive execution outcome. Report per-scenario failures and the actual
model, fixture, and version alongside any course result; do not infer an
application defect from an action or provider failure.

## Local cross-application pilot

On 2026-10-19, one read-only scenario per isolated application was run once
under each condition with local Ollama `qwen3.5:9b` and a five-action limit.
The applications were 4gaBoards 3.2.16, Admidio 5.0.2, and Discourse
`v2025.12.0-latest`. The 4gaBoards scenario checked an existing project in
both dashboard and sidebar; Admidio and Discourse checked a destination page
heading. Private inputs and evidence are under `runs/cross-app-pilot/`.

| Condition | PASS | UNKNOWN | FAIL |
| --- | ---: | ---: | ---: |
| B0 | 3 | 0 | 0 |
| B1 | 2 | 1 | 0 |
| B2 | 2 | 1 | 0 |
| B3 | 3 | 0 | 0 |

The B1 Discourse and B2 Admidio runs reached the intended pages, but the
Verifier did not supply complete supported evidence and the following browser
action failed. Both remain UNKNOWN. The result viewer retains these partial
reports. This is a pipeline smoke test with one run per cell, not a formal
36-scenario evaluation or an estimate of relative method quality.
