from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import Playwright, sync_playwright

from webapptester.browser import BrowserGateway
from webapptester.cli import _launch_chromium
from webapptester.schema import Action
from webapptester.store import RunStore


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = ROOT / "examples" / "4gaboards" / "scenarios.json"
LOCAL_URL = "http://127.0.0.1:3001/"
STORAGE_STATE = Path("runs/4gaboards-local/auth.json")


def _launch_browser(playwright: Playwright):
    return _launch_chromium(playwright.chromium)


def _act(gateway: BrowserGateway, store: RunStore, action: Action) -> None:
    result = gateway.act(action)
    store.append_event(
        {
            "type": "action",
            "action": {
                "kind": action.kind,
                "locator": action.locator,
                "checkpoint_id": action.checkpoint_id,
            },
            "success": result.success,
            "steps": result.steps,
        }
    )
    assert result.success, f"browser action failed: {action.kind}"


def test_4gaboards_scenarios_manifest_is_local_and_bounded() -> None:
    scenarios = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))

    assert isinstance(scenarios, list)
    assert len(scenarios) == 3
    course_examples = [item for item in scenarios if "Course Test" in item["scenario"]]
    assert len(course_examples) == 1
    assert "In the dashboard click Add project, enter the project name Course Test and confirm, then check the new project appears on both dashboard and sidebar." in course_examples[0]["scenario"]

    read_only = [item for item in scenarios if item["scenario"].startswith("Local read-only smoke:")]
    assert len(read_only) == 2
    for item in scenarios:
        assert item["url"] == LOCAL_URL
        assert item["storage_state"] == str(STORAGE_STATE)
        assert item["max_steps"] <= 35


def test_4gaboards_deterministic_creation_smoke() -> None:
    """Run only when explicitly enabled against the isolated local fixture."""

    if os.environ.get("WEBAPPTESTER_4GA_SMOKE") != "1":
        pytest.skip("set WEBAPPTESTER_4GA_SMOKE=1 to run the local browser smoke")

    storage_state = Path(os.environ.get("WEBAPPTESTER_4GA_STORAGE_STATE", str(STORAGE_STATE)))
    if not storage_state.is_file():
        pytest.skip("local 4gaBoards storage state is unavailable")

    output_root = Path(
        os.environ.get(
            "WEBAPPTESTER_4GA_SMOKE_OUTPUT",
            "runs/4gaboards-local/deterministic-smoke",
        )
    )
    store = RunStore(output_root)
    trace_path = output_root / "trace.zip"
    started = time.monotonic()
    actions: list[dict[str, str]] = []
    observations = []
    evidence: list[dict[str, str]] = []
    verdict_status = "UNKNOWN"
    verdict_reason = "smoke did not complete"
    context = None
    browser = None

    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            context = browser.new_context(storage_state=str(storage_state))
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            gateway = BrowserGateway(page, max_steps=12)

            goto = Action("goto", value=LOCAL_URL)
            _act(gateway, store, goto)
            actions.append({"kind": goto.kind, "locator": "", "checkpoint_id": ""})
            initial = gateway.observe(store)
            observations.append(initial)
            assert page.locator('button[title="Add Project"]:visible').count() >= 1

            project_name = "Local Smoke " + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            click_add = Action(
                "click",
                'div[class*="Projects_headerButtonGroup"] button[title="Add Project"]',
            )
            _act(gateway, store, click_add)
            actions.append({"kind": click_add.kind, "locator": click_add.locator, "checkpoint_id": ""})

            fill_name = Action("fill", 'input[name="name"]', project_name, "project-created")
            _act(gateway, store, fill_name)
            actions.append({"kind": fill_name.kind, "locator": fill_name.locator, "checkpoint_id": fill_name.checkpoint_id})

            confirm = Action("click", 'div[role="dialog"] button[type="submit"]', checkpoint_id="project-created")
            _act(gateway, store, confirm)
            actions.append({"kind": confirm.kind, "locator": confirm.locator, "checkpoint_id": confirm.checkpoint_id})

            navigate_dashboard = Action("goto", value=LOCAL_URL)
            _act(gateway, store, navigate_dashboard)
            actions.append({"kind": navigate_dashboard.kind, "locator": "", "checkpoint_id": ""})

            dashboard_project = page.locator(
                'div[class*="Projects_projectsWrapper"] a[href^="/projects/"]'
            ).filter(has_text=project_name)
            sidebar_project = page.locator(
                'div[class*="Sidebar_sidebarItemProject"] a[href^="/projects/"]'
            ).filter(has_text=project_name)
            dashboard_project.wait_for(state="visible", timeout=5_000)
            sidebar_project.wait_for(state="visible", timeout=5_000)
            assert dashboard_project.count() == 1
            assert sidebar_project.count() == 1

            final = gateway.observe(store)
            observations.append(final)
            assert project_name in final.text
            evidence = [
                {
                    "checkpoint_id": "dashboard-project",
                    "observation_id": final.id,
                    "quote": project_name,
                    "status": "SAT",
                },
                {
                    "checkpoint_id": "sidebar-project",
                    "observation_id": final.id,
                    "quote": project_name,
                    "status": "SAT",
                },
            ]
            assert gateway.steps <= 35
            verdict_status = "PASS"
            verdict_reason = "unique local project was verified on dashboard and sidebar"
            # Stop tracing while the sync Playwright event loop is still live.
            context.tracing.stop(path=str(trace_path))
            context.close()
            browser.close()
            context = None
            browser = None
    except AssertionError:
        verdict_status = "FAIL"
        verdict_reason = "deterministic browser assertion failed"
        raise
    except Exception as error:
        verdict_reason = f"deterministic browser execution unavailable: {type(error).__name__}"
        raise
    finally:
        if context is not None:
            try:
                context.tracing.stop(path=str(trace_path))
            except Exception:
                pass
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        if observations:
            store.write_report(
                {
                    "steps": gateway.steps if "gateway" in locals() else 0,
                    "elapsed_seconds": max(0.0, time.monotonic() - started),
                    "token_usage": {"prompt": 0, "completion": 0},
                    "trace_path": str(trace_path),
                    "actions": actions,
                    "observations": [asdict(item) for item in observations],
                    "verdict": {
                        "status": verdict_status,
                        "reason": verdict_reason,
                        "evidence": evidence,
                    },
                }
            )
