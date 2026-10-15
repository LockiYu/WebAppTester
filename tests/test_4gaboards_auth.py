from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import Error as PlaywrightError

from webapptester import batch, cli


ROOT = Path(__file__).resolve().parents[1]
_BOOTSTRAP_SPEC = importlib.util.spec_from_file_location(
    "bootstrap_4gaboards_auth", ROOT / "scripts" / "bootstrap_4gaboards_auth.py"
)
assert _BOOTSTRAP_SPEC is not None and _BOOTSTRAP_SPEC.loader is not None
bootstrap = importlib.util.module_from_spec(_BOOTSTRAP_SPEC)
_BOOTSTRAP_SPEC.loader.exec_module(bootstrap)


class _FailingPage:
    def set_default_timeout(self, _timeout: int) -> None:
        pass

    def set_default_navigation_timeout(self, _timeout: int) -> None:
        pass

    def goto(self, _url: str) -> None:
        raise PlaywrightError("navigation failed")


class _Tracing:
    def start(self, **_kwargs: object) -> None:
        pass

    def stop(self, **_kwargs: object) -> None:
        pass


class _Context:
    def __init__(self) -> None:
        self.tracing = _Tracing()

    def new_page(self) -> _FailingPage:
        return _FailingPage()

    def close(self) -> None:
        pass


class _Browser:
    def __init__(self) -> None:
        self.context = _Context()
        self.new_context_options: dict[str, object] | None = None

    def new_context(self, **kwargs: object) -> _Context:
        self.new_context_options = kwargs
        return self.context

    def close(self) -> None:
        pass


class _Playwright:
    chromium = object()


class _PlaywrightContextManager:
    def __init__(self, playwright: _Playwright) -> None:
        self.playwright = playwright

    def __enter__(self) -> _Playwright:
        return self.playwright

    def __exit__(self, *_args: object) -> None:
        pass


def _args(tmp_path: Path, storage_state: Path | None) -> argparse.Namespace:
    return argparse.Namespace(
        scenario="dashboard",
        url="http://127.0.0.1:3001/",
        provider="ollama",
        model="test-model",
        output=tmp_path / "run",
        max_steps=3,
        storage_state=storage_state,
    )


def test_cli_passes_storage_state_to_new_context_and_does_not_persist_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "auth.json"
    state_secret = "state-cookie-must-not-appear"
    state_path.write_text(json.dumps({"cookies": [{"name": state_secret}]}), encoding="utf-8")
    browser = _Browser()
    monkeypatch.setattr(cli, "sync_playwright", lambda: _PlaywrightContextManager(_Playwright()))
    monkeypatch.setattr(cli, "_launch_chromium", lambda _chromium: browser)
    monkeypatch.setattr(cli, "OllamaProvider", lambda model: SimpleNamespace(usage={}))

    status, report_path = cli.run(_args(tmp_path, state_path))

    assert status == 2
    assert browser.new_context_options == {"storage_state": str(state_path)}
    report_text = report_path.read_text(encoding="utf-8")
    events_text = (report_path.parent / "events.jsonl").read_text(encoding="utf-8")
    assert state_secret not in report_text
    assert state_secret not in events_text


def test_cli_rejects_missing_storage_state_before_provider_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider_called = False

    def provider(_model: str) -> object:
        nonlocal provider_called
        provider_called = True
        raise AssertionError("provider must not be initialized")

    monkeypatch.setattr(cli, "OllamaProvider", provider)
    status, report_path = cli.run(_args(tmp_path, tmp_path / "missing-auth.json"))

    assert status == 2
    assert not provider_called
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["verdict"]["status"] == "UNKNOWN"
    assert report["verdict"]["reason"] == "storage state path is missing or invalid"


def test_batch_accepts_and_forwards_optional_storage_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "batch.json"
    state_path = tmp_path / "auth.json"
    config.write_text(
        json.dumps([{"scenario": "dashboard", "url": "http://127.0.0.1:3001/", "storage_state": str(state_path)}]),
        encoding="utf-8",
    )
    items = batch.load_batch(config)
    assert items[0]["storage_state"] == str(state_path)

    seen: list[object] = []

    def fake_run(args: argparse.Namespace) -> tuple[int, Path]:
        seen.append(args.storage_state)
        report = args.output / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "steps": 0,
                    "elapsed_seconds": 0,
                    "token_usage": {"prompt": 0, "completion": 0},
                    "actions": [],
                    "observations": [],
                    "verdict": {"status": "UNKNOWN", "reason": "state unavailable", "evidence": []},
                }
            ),
            encoding="utf-8",
        )
        return 2, report

    monkeypatch.setattr(batch.cli, "run", fake_run)
    root = batch.run_batch(items, tmp_path / "runs")

    assert seen == [str(state_path)]
    summary = json.loads((root / "batch-summary.json").read_text(encoding="utf-8"))
    assert "storage_state" not in summary["items"][0]


@pytest.mark.parametrize("contents", ["", "DEFAULT_ADMIN_USERNAME=x\n"])
def test_bootstrap_env_parser_rejects_missing_credentials(tmp_path: Path, contents: str) -> None:
    env_path = tmp_path / "instance.env"
    env_path.write_text(contents, encoding="utf-8")
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.read_local_env(env_path)


def test_bootstrap_storage_state_is_written_mode_0600(tmp_path: Path) -> None:
    destination = tmp_path / "auth.json"

    class Context:
        def storage_state(self, *, path: str) -> None:
            Path(path).write_text('{"cookies": []}', encoding="utf-8")

    bootstrap._write_storage_state(Context(), destination)

    assert destination.read_text(encoding="utf-8") == '{"cookies": []}'
    assert os.stat(destination).st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".auth.json.*.tmp"))
