"""Command line runner for an auditable browser test."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .browser import BrowserGateway, MAX_STEPS
from .controller import Controller
from .providers import DeepSeekProvider, ModelOutputError, OllamaProvider
from .roles import Executor, Planner, Verifier
from .schema import Action, Scenario
from .store import RunStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an evidence-gated browser scenario")
    parser.add_argument("--scenario", required=True, help="Natural-language test scenario")
    parser.add_argument("--url", required=True, help="Starting page URL")
    parser.add_argument("--provider", choices=("ollama", "deepseek"), default="ollama")
    parser.add_argument("--model", help="Provider model (defaults to qwen2.5:3b or DeepSeek flash)")
    parser.add_argument("--output", type=Path, default=Path("runs/demo"))
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    return parser


def _make_run_directory(base: Path) -> Path:
    """Create a unique timestamped child directory for one invocation."""

    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = base / stamp
    suffix = 1
    while candidate.exists():
        candidate = base / f"{stamp}-{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _unknown_report(store: RunStore, *, trace_path: Path, started: float, reason: str) -> Path:
    return store.write_report(
        {
            "steps": 0,
            "elapsed_seconds": max(0.0, time.monotonic() - started),
            "token_usage": {"prompt": 0, "completion": 0},
            "trace_path": str(trace_path),
            "actions": [],
            "observations": [],
            "verdict": {"status": "UNKNOWN", "reason": reason, "evidence": []},
        }
    )


def _launch_chromium(chromium: Any) -> Any:
    """Prefer Playwright's managed Chromium, with installed Chrome fallback."""

    try:
        return chromium.launch(headless=True)
    except PlaywrightError as bundled_error:
        try:
            return chromium.launch(channel="chrome", headless=True)
        except PlaywrightError:
            raise bundled_error


def run(args: argparse.Namespace) -> tuple[int, Path]:
    started = time.monotonic()
    output = _make_run_directory(Path(args.output))
    store = RunStore(output)
    trace_path = output / "trace.zip"
    max_steps = args.max_steps
    if type(max_steps) is not int or not 1 <= max_steps <= MAX_STEPS:
        report = _unknown_report(
            store,
            trace_path=trace_path,
            started=started,
            reason="invalid max_steps",
        )
        print(f"UNKNOWN: invalid max_steps; report={report}", file=sys.stderr)
        return 2, report

    try:
        model = args.model or ("deepseek-v4.1-flash" if args.provider == "deepseek" else "qwen2.5:3b")
        if args.provider == "deepseek":
            provider = DeepSeekProvider(model=model)
        else:
            provider = OllamaProvider(model=model)
    except (ValueError, OSError) as error:
        report = _unknown_report(
            store,
            trace_path=trace_path,
            started=started,
            reason="provider configuration is unavailable",
        )
        message = "DeepSeek requires DEEPSEEK_API_KEY" if args.provider == "deepseek" else str(error)
        print(f"UNKNOWN: {message}; report={report}", file=sys.stderr)
        return 2, report

    scenario = Scenario(args.scenario, args.url, max_steps=max_steps)
    planner = Planner(provider)
    executor = Executor(provider)
    verifier = Verifier(provider)
    browser = None
    context = None
    trace_started = False
    verdict_status = "UNKNOWN"
    report = output / "report.json"
    try:
        with sync_playwright() as playwright:
            browser = _launch_chromium(playwright.chromium)
            context = browser.new_context()
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            trace_started = True
            try:
                page = context.new_page()
                gateway = BrowserGateway(page, max_steps=max_steps)
                gateway.trace_path = trace_path
                controller = Controller(planner, executor, verifier, gateway, store)
                # Start every CLI run on the requested page. This consumes one
                # controlled browser step and avoids asking a small local model
                # to recover from Playwright's initial about:blank page.
                initial_navigation = gateway.act(Action("goto", value=scenario.url))
                store.append_event(
                    {
                        "type": "action",
                        "action": {
                            "kind": "goto",
                            "locator": "",
                            "checkpoint_id": "",
                        },
                        "success": initial_navigation.success,
                        "steps": initial_navigation.steps,
                    }
                )
                if not initial_navigation.success:
                    report = _unknown_report(
                        store,
                        trace_path=trace_path,
                        started=started,
                        reason="initial navigation failed",
                    )
                    verdict_status = "UNKNOWN"
                else:
                    verdict = controller.run(scenario)
                    verdict_status = verdict.status
                    report = output / "report.json"
                if report.exists() and initial_navigation.success:
                    report_data = json.loads(report.read_text(encoding="utf-8"))
                    report_data["actions"].insert(
                        0, {"kind": "goto", "locator": "", "checkpoint_id": ""}
                    )
                    store.write_report(report_data)
            finally:
                if context is not None:
                    try:
                        if trace_started:
                            context.tracing.stop(path=str(trace_path))
                    except Exception as error:
                        print(f"warning: could not save trace ({type(error).__name__})", file=sys.stderr)
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass
    except (PlaywrightError, OSError) as error:
        report = _unknown_report(
            store,
            trace_path=trace_path,
            started=started,
            reason="browser startup failed",
        )
        print(f"UNKNOWN: browser startup failed ({type(error).__name__}); report={report}", file=sys.stderr)
        return 2, report
    except (ModelOutputError, ValueError, TypeError) as error:
        # Controller normally converts role/provider failures to UNKNOWN and
        # writes a report. This fallback covers setup errors before Controller.
        if not report.exists():
            report = _unknown_report(
                store,
                trace_path=trace_path,
                started=started,
                reason="provider returned invalid role output",
            )
        print(f"UNKNOWN: provider output unavailable ({type(error).__name__}); report={report}", file=sys.stderr)
        return 2, report
    # Controller records the trace destination before tracing stops, so the
    # report remains stable even if Playwright writes the archive at shutdown.
    if not report.exists():
        report = _unknown_report(
            store,
            trace_path=trace_path,
            started=started,
            reason="run did not produce a report",
        )
    if verdict_status == "PASS":
        print(f"PASS: report={report}")
        return 0, report
    if verdict_status == "FAIL":
        print(f"FAIL: report={report}")
        return 1, report
    print(
        f"UNKNOWN: {args.provider} unavailable or role output was unsupported; report={report}",
        file=sys.stderr,
    )
    return 2, report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        status, _ = run(args)
        return status
    except Exception as error:
        # Keep a persistent report for unexpected setup errors too.
        output = _make_run_directory(Path(args.output))
        store = RunStore(output)
        report = _unknown_report(
            store,
            trace_path=output / "trace.zip",
            started=time.monotonic(),
            reason=f"runner stopped after {type(error).__name__}",
        )
        print(f"UNKNOWN: runner stopped after {type(error).__name__}; report={report}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
