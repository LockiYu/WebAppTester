# Locator Recovery and Grounded Ref Actions

**Goal:** Let the agent distinguish repeated controls in a real web app and recover from a failed locator within the 35-action budget, without hardcoding 4gaBoards selectors or relaxing evidence-based verdicts.

**Observed failure:** The 4gaBoards dashboard has two visible `Add Project` buttons. Qwen proposed `role=button;name=Add Project`; Playwright's strict locator failed after step 2. The controller then returned UNKNOWN, and the project was not created.

**Design:** On each observation, `BrowserGateway` captures a Playwright AI ARIA snapshot with temporary element refs. It keeps the snapshot in memory for the Executor and retains the existing visible `Observation.text` as the only source for Verifier quotes. The Executor may return `ref=eN`; the gateway accepts only refs present in the latest snapshot. The snapshot and action input values must not appear in reports, events, or dashboard exports. Snapshot input values are redacted before they are sent to a model. No page cookies or storage state enter model prompts.

An unqualified locator matching multiple elements remains an error. Such a failure consumes a browser step, records a safe error class, and may be retried at most twice using a fresh observation and a short failure hint. Other failures retain the current UNKNOWN behavior. A recovered locator error cannot itself satisfy a checkpoint; PASS still requires the Verifier's grounded evidence from subsequent observations. Repeated state/action detection and the 35-step ceiling remain authoritative.

The minimum Playwright version becomes 1.59 because AI ARIA snapshots and `get_by_ref` were added then. Official API reference: [Playwright Python Locator](https://playwright.dev/python/docs/api/class-locator).

## Tasks

1. Add snapshot capture, input-value redaction, latest-ref validation and `ref=eN` dispatch to `BrowserGateway`. Test ephemeral refs, malformed/invented refs, duplicate role failure, and nonleakage.
2. Pass the temporary snapshot and safe failure hint to `Executor`; update its prompt to prefer refs when a role/name is repeated. Preserve compatibility with test doubles and role signatures.
3. Let `Controller` retry only classified ambiguous-locator failures, accounting for each action and enforcing a small retry bound. Test recovery to grounded PASS and exhaustion to UNKNOWN, including saved action events.
4. Require separate, auditable evidence for independent final assertions. In particular, dashboard and sidebar visibility each need their own checkpoint and scoped observation support. Reject a whole-page quote as proof of both locations, and persist checkpoint definitions in the report. Regression tests must show that one-location or missing-project observations cannot PASS.
5. Run focused and full tests, then first probe the model with a unique local project name. Rerun the single known 4gaBoards `Course Test` example on the isolated fixture only after checking that no `Course Test` project exists. Keep the model verdict honest and save screenshots/Trace locally. Review the changes independently before pushing.

**Risk boundary:** ARIA snapshots can contain page text and input values. Only a sanitized snapshot reaches the Executor; raw snapshots remain transient. Authenticated Trace remains local and is never a dashboard download because it may contain session cookies.

## Verification status

- The isolated 4gaBoards fixture and local Ollama completed the full natural-language creation scenario with a fresh project name after the evidence audit. The report at `runs/4gaboards-local/runtime-acceptance-after-audit/20261016T102519799424Z/report.json` is `PASS`: five browser actions, five saved screenshots, a local Trace, and independent `dashboard`/`sidebar` SAT evidence from observation `o5` on the scenario start URL. The target text appears in distinct nonoverlapping `main` and `aside` regions. This report is local and ignored by Git.
- Earlier `UNKNOWN` model probes remain in the ignored run history. They are not recast as application failures or successful tests.
- Managed Playwright Chromium is required for stable local acceptance on this machine. The installed system Chrome channel crashed during long-running probes; the CLI now tries managed full Chromium first, then the managed default and installed Chrome.
- Scoped positive-presence evidence is accepted only from the scenario start URL and the newest observation. Semantic as well as spatial regions are checked for geometric separation. Form recovery only infers a value for an active field inside a dialog and clicks the observed dialog button by its snapshot ref.
- The full test suite passed `165 passed, 1 skipped` after the Verifier fallback change. The skipped test is the opt-in isolated 4gaBoards smoke test; the real model acceptance above exercised the fixture separately.
- The result dashboard was restarted with an absolute runs root and displayed the prior real PASS with its two checkpoint rows, screenshots, and local Trace path. The new report can be selected after refreshing the page.
- A diagnostic run created `Course Test` and captured it in both locations, but its report remained `UNKNOWN` because Ollama returned malformed Verifier JSON. The fixture therefore must not be used for another `Course Test` creation attempt. The Verifier now has a narrow fallback that extracts exact target evidence from the latest correctly scoped observation when model JSON is invalid; this fallback has targeted unit coverage but has not yet been exercised by a new full Course Test run on a fresh fixture.
- A later fresh-name probe after the fallback stopped at step 1 with `UNKNOWN` because Ollama's Executor JSON was malformed before any creation. This is model variability; the earlier end-to-end PASS and the focused fallback tests remain the evidence for those respective claims.
- DeepSeek API spend remains ¥0; no API key was available in the environment.
