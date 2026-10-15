from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import Playwright, sync_playwright

from webapptester.browser import BrowserGateway
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
        if "Alpha" not in current.text:
            return ()
        return tuple(
            Evidence(checkpoint.id, current.id, "Alpha", "SAT") for checkpoint in checkpoints
        )


class _MissingSidebarVerifier(_ScriptedVerifier):
    def verify(self, checkpoints, observations):
        current = observations[-1]
        if "Alpha" not in current.text:
            return ()
        return (Evidence("dashboard", current.id, "Alpha", "SAT"),)


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
                sidebar_links = page.get_by_role("link", name="Alpha").count()
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
        assert "Alpha" in body
        assert sidebar_links == 1
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
