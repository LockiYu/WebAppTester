from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import Playwright, sync_playwright

import webapptester.cli as cli
from webapptester.browser import BrowserGateway
from webapptester.cli import _make_run_directory
from webapptester.controller import Controller
from webapptester.schema import Action, Checkpoint, Evidence, Observation, Scenario
from webapptester.store import RunStore


ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _launch_browser(playwright: Playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except Exception:
        # CI may have installed Google Chrome but not Playwright's bundle.
        return playwright.chromium.launch(channel="chrome", headless=True)


class _ScriptedPlanner:
    def plan(self, scenario: Scenario) -> tuple[Checkpoint, ...]:
        return (
            Checkpoint("dashboard", "dashboard", "Alpha is visible on the dashboard"),
            Checkpoint("sidebar", "sidebar", "Alpha is visible in the sidebar"),
        )


class _ScriptedExecutor:
    def __init__(self) -> None:
        self.actions = [
            Action("goto", value="http://127.0.0.1:0"),
            Action("fill", "#project-name", "Alpha", "dashboard"),
            Action("click", "role=button;name=Create", checkpoint_id="dashboard"),
        ]

    def next_action(self, scenario, checkpoints, observation, history, gap_request=None):
        if not history:
            return Action("goto", value=scenario.url)
        if len(history) == 1:
            return Action("fill", "#project-name", "Alpha", "dashboard")
        if len(history) == 2:
            return Action("click", "role=button;name=Create", checkpoint_id="dashboard")
        return Action("stop")


class _ScriptedVerifier:
    def verify(self, checkpoints, observations):
        current = observations[-1]
        dashboard_quote = "Dashboard project: Alpha"
        sidebar_quote = "Sidebar project: Alpha"
        if dashboard_quote not in current.text or sidebar_quote not in current.text:
            return ()
        return (
            Evidence("dashboard", current.id, dashboard_quote, "SAT"),
            Evidence("sidebar", current.id, sidebar_quote, "SAT"),
        )


class _MissingSidebarVerifier(_ScriptedVerifier):
    def verify(self, checkpoints, observations):
        current = observations[-1]
        if "Dashboard project: Alpha" not in current.text:
            return ()
        return (Evidence("dashboard", current.id, "Dashboard project: Alpha", "SAT"),)


def test_cli_output_creates_unique_timestamped_run_directories(tmp_path: Path) -> None:
    base = tmp_path / "runs" / "demo"
    first = _make_run_directory(base)
    second = _make_run_directory(base)
    assert first.parent == base
    assert second.parent == base
    assert first != second
    assert first.name.endswith("Z")
    assert second.name.endswith("Z")


def test_cli_unexpected_exception_fallback_uses_timestamped_run_directory(
    tmp_path: Path, monkeypatch
) -> None:
    base = tmp_path / "runs" / "demo"

    def explode(args):
        raise RuntimeError("forced test failure")

    monkeypatch.setattr(cli, "run", explode)
    assert cli.main(["--scenario", "x", "--url", "http://127.0.0.1", "--output", str(base)]) == 2
    reports = list(base.glob("*/report.json"))
    assert len(reports) == 1
    assert reports[0].parent.parent == base


def _start_demo(port: int) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "demo" / "app.py"), "--port", str(port)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/", timeout=0.2):
                return process
        except Exception:
            if process.poll() is not None:
                output = process.stderr.read() if process.stderr else ""
                raise RuntimeError(f"demo exited early: {output}")
            time.sleep(0.05)
    process.kill()
    raise RuntimeError("demo did not start")


def test_scripted_controller_real_browser_persists_pass_evidence(tmp_path: Path) -> None:
    port = _free_port()
    demo = _start_demo(port)
    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            context = browser.new_context()
            trace_path = tmp_path / "trace.zip"
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            try:
                page = context.new_page()
                gateway = BrowserGateway(page, max_steps=10)
                gateway.trace_path = trace_path
                scenario = Scenario(
                    "Create project Alpha and verify it appears on dashboard and sidebar",
                    f"http://127.0.0.1:{port}/",
                    max_steps=10,
                )
                store = RunStore(tmp_path / "run")
                verdict = Controller(
                    _ScriptedPlanner(), _ScriptedExecutor(), _ScriptedVerifier(), gateway, store
                ).run(scenario)
                body = page.locator("body").inner_text()
                dashboard_cards = page.get_by_test_id("dashboard-card").count()
                sidebar_links = page.get_by_role("link", name="Sidebar project: Alpha").count()
            finally:
                context.tracing.stop(path=str(trace_path))
                context.close()
                browser.close()

        assert verdict.status == "PASS"
        report = json.loads((tmp_path / "run" / "report.json").read_text())
        assert report["steps"] <= 35
        assert report["verdict"]["evidence"]
        assert trace_path.is_file() and trace_path.stat().st_size > 0
        screenshots = list((tmp_path / "run" / "screenshots").glob("*.png"))
        assert screenshots and all(path.stat().st_size > 0 for path in screenshots)
        assert "Dashboard project: Alpha" in body
        assert dashboard_cards == 1
        assert sidebar_links == 1
        assert "Sidebar project: Alpha" in body
    finally:
        demo.terminate()
        demo.wait(timeout=5)


def test_missing_sidebar_reference_is_unknown(tmp_path: Path) -> None:
    port = _free_port()
    demo = _start_demo(port)
    try:
        with sync_playwright() as playwright:
            browser = _launch_browser(playwright)
            context = browser.new_context()
            try:
                page = context.new_page()
                gateway = BrowserGateway(page, max_steps=10)
                scenario = Scenario(
                    "Create project Alpha and verify it appears on dashboard and sidebar",
                    f"http://127.0.0.1:{port}/",
                    max_steps=10,
                )
                store = RunStore(tmp_path / "run")
                verdict = Controller(
                    _ScriptedPlanner(),
                    _ScriptedExecutor(),
                    _MissingSidebarVerifier(),
                    gateway,
                    store,
                ).run(scenario)
            finally:
                context.close()
                browser.close()
        assert verdict.status == "UNKNOWN"
        report = json.loads((tmp_path / "run" / "report.json").read_text())
        assert report["verdict"]["status"] == "UNKNOWN"
    finally:
        demo.terminate()
        demo.wait(timeout=5)
