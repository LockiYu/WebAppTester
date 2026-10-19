#!/usr/bin/env python3
"""Prepare a private fixture and run the local three-application ablation pilot."""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from webapptester.ablation import VARIANT_ORDER, load_scenario_manifest
from webapptester.ablation_cli import run_manifest
from webapptester.cli import _launch_chromium


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "runs"
APP_URLS = {
    "4gaboards": "http://127.0.0.1:3001/",
    "admidio": "http://127.0.0.1:3102/",
    "discourse": "http://127.0.0.1:4203/",
}
APP_VERSIONS = {
    "4gaboards": "3.2.16",
    "admidio": "5.0.2",
    "discourse": "v2025.12.0-latest",
}


def _auth_path(app: str) -> Path:
    path = RUNS / f"{app}-local" / "auth.json"
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f"{app} browser state is not a regular file")
    return path


def _prepare_4gaboards_project(name: str) -> None:
    """Create one fresh project, then verify it independently in two regions."""

    with sync_playwright() as playwright:
        browser = _launch_chromium(playwright.chromium)
        try:
            context = browser.new_context(storage_state=str(_auth_path("4gaboards")))
            page = context.new_page()
            page.goto(APP_URLS["4gaboards"], wait_until="domcontentloaded")
            page.locator(
                'div[class*="Projects_headerButtonGroup"] button[title="Add Project"]'
            ).click(timeout=10_000)
            page.locator('input[name="name"]').fill(name)
            page.locator('div[role="dialog"] button[type="submit"]').click()
            page.goto(APP_URLS["4gaboards"], wait_until="domcontentloaded")
            dashboard = page.locator(
                'div[class*="Projects_projectsWrapper"] a[href^="/projects/"]'
            ).filter(has_text=name)
            sidebar = page.locator(
                'div[class*="Sidebar_sidebarItemProject"] a[href^="/projects/"]'
            ).filter(has_text=name)
            dashboard.wait_for(state="visible", timeout=10_000)
            sidebar.wait_for(state="visible", timeout=10_000)
            if dashboard.count() != 1 or sidebar.count() != 1:
                raise RuntimeError("pilot project is not unique in both 4gaBoards regions")
        finally:
            browser.close()


def _manifest_cases(name: str, manifest_dir: Path) -> list[dict[str, object]]:
    """Use the same read-only scenario for each variant within this pilot."""

    def state(app: str) -> str:
        return os.path.relpath(_auth_path(app), manifest_dir)

    return [
        {
            "id": "4gaboards-existing-project",
            "scenario": (
                f"Open the dashboard and verify project named {name}, "
                "then confirm it appears on both dashboard and sidebar."
            ),
            "url": APP_URLS["4gaboards"],
            "storage_state": state("4gaboards"),
            "max_steps": 5,
        },
        {
            "id": "admidio-preferences-heading",
            "scenario": (
                "Open Preferences from the Admidio Administration menu and "
                "verify the Preferences heading is visible."
            ),
            "url": APP_URLS["admidio"],
            "storage_state": state("admidio"),
            "max_steps": 5,
        },
        {
            "id": "discourse-welcome-heading",
            "scenario": (
                "Open Welcome to Discourse! and verify the "
                "Welcome to Discourse! heading is visible."
            ),
            "url": APP_URLS["discourse"],
            "storage_state": state("discourse"),
            "max_steps": 5,
        },
    ]


def _write_private_json(path: Path, value: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".pilot-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen3.5:9b", help="local Ollama model")
    parser.add_argument("--variant", nargs="+", choices=VARIANT_ORDER, default=VARIANT_ORDER)
    args = parser.parse_args(argv)

    for app in APP_URLS:
        _auth_path(app)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = RUNS / "cross-app-pilot" / stamp
    run_dir.mkdir(parents=True, mode=0o700)
    os.chmod(run_dir, 0o700)
    project_name = f"Cross App Pilot {stamp}"
    _prepare_4gaboards_project(project_name)

    manifest = run_dir / "manifest.json"
    _write_private_json(manifest, _manifest_cases(project_name, run_dir))
    load_scenario_manifest(manifest)
    _write_private_json(run_dir / "setup.json", {
        "created_at_utc": stamp,
        "model": args.model,
        "variants": args.variant,
        "application_versions": APP_VERSIONS,
        "project_name": project_name,
    })
    summary = run_manifest(
        manifest, run_dir / "results", model=args.model, variants=tuple(args.variant)
    )
    print(f"Local cross-app pilot summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
