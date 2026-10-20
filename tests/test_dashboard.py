import base64
import json
import os
import time
from pathlib import Path

import pytest

from streamlit.testing.v1 import AppTest
from webapptester.dashboard import _events_export, _report_export
from webapptester.results import list_runs


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def _write_run(
    root: Path,
    name: str,
    *,
    status: str,
    reason: str,
    screenshot: str = "screenshots/observation-0001.png",
    trace: str | None = "trace.zip",
    steps: int = 3,
    tokens: tuple[int, int] = (12, 8),
) -> Path:
    run = root / name
    run.mkdir(parents=True)
    report = {
        "steps": steps,
        "elapsed_seconds": 1.25,
        "token_usage": {"prompt": tokens[0], "completion": tokens[1]},
        "trace_path": trace,
        "secret": "top-level secret",
        "password": "top-level password",
        "api_key": "top-level api key",
        "actions": [
            {"kind": "goto", "locator": "https://example.test", "checkpoint_id": ""},
            {"kind": "click", "locator": "role=button;name=Create", "checkpoint_id": "c1", "value": "secret"},
        ],
        "observations": [
            {
                "id": "o1",
                "url": "https://example.test",
                "text": "The project dashboard is visible.",
                "screenshot": screenshot,
                "fingerprint": "abc",
            }
        ],
        "verdict": {
            "status": status,
            "reason": reason,
            "evidence": [
                {
                    "checkpoint_id": "c1",
                    "observation_id": "o1",
                    "quote": "project dashboard",
                    "status": "SAT" if status == "PASS" else "VIOLATED",
                }
            ],
        },
    }
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    if screenshot:
        screenshot_path = run / screenshot
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        screenshot_path.write_bytes(_PNG)
    if trace:
        (run / trace).write_bytes(b"trace")
    (run / "events.jsonl").write_text(
        json.dumps(
            {
                "type": "action",
                "action": {
                    "kind": "fill",
                    "locator": "#password",
                    "value": "secret",
                    "input": "another secret",
                    "arbitrary": "discard this",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return run


def _dashboard_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.chdir(tmp_path)
    return AppTest.from_file(str(Path(__file__).parents[1] / "src/webapptester/dashboard.py"))


def test_dashboard_history_and_selected_run_show_evidence_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    _write_run(
        runs,
        "pass-run",
        status="PASS",
        reason="all required checkpoints have supported SAT evidence",
        screenshot="screenshots/missing.png",
        trace=None,
        tokens=(0, 0),
    )
    fail = _write_run(
        runs,
        "fail-run",
        status="FAIL",
        reason="a required checkpoint is directly shown to be VIOLATED",
    )
    now = time.time()
    os.utime(runs / "pass-run" / "report.json", (now - 10, now - 10))
    os.utime(runs / "fail-run" / "report.json", (now, now))
    (runs / "pass-run" / "screenshots" / "missing.png").unlink()

    at = _dashboard_app(tmp_path, monkeypatch).run(timeout=20)

    assert at.exception == []
    history_text = str(at.dataframe[0].value)
    assert "PASS" in history_text
    assert "FAIL" in history_text
    assert all("来源未知" in source for source in at.dataframe[0].value["来源"].tolist())

    at.selectbox[0].set_value(1).run()

    at.checkbox[0].set_value(True).run()

    text = "\n".join(
        [item.value for item in at.markdown]
        + [item.value for item in at.caption]
        + [item.value for item in at.warning]
        + [item.value for item in at.info]
        + [f"{item.label}: {item.value}" for item in at.metric]
    )
    assert at.exception == []
    assert "PASS" in text
    assert "all required checkpoints have supported SAT evidence" in text
    assert "操作步数" in text
    assert "输入 Token" in text
    assert "输出 Token" in text
    assert "o1" in text
    assert "project dashboard" in text
    assert "可能包含敏感内容" in text
    assert "不可用" in text
    assert any(button.label == "下载 report.json" for button in at.download_button)
    assert any(button.label == "下载 events.jsonl" for button in at.download_button)
    selected = next(run for run in list_runs(runs) if run.status == "PASS")
    report_export = _report_export(runs, selected) or b""
    events_export = _events_export(runs, selected) or b""
    for secret in (b"secret", b"password", b"api_key", b"discard this", b"another secret"):
        assert secret not in report_export
        assert secret not in events_export

    at.selectbox[0].set_value(0).run()
    at.checkbox[0].set_value(True).run()
    assert not any(button.label == "下载 trace.zip" for button in at.download_button)
    assert any("会话凭据" in item.value for item in at.info)

    trace_run = next(run for run in list_runs(runs) if run.status == "FAIL")
    outside_trace = tmp_path / "outside.trace.zip"
    outside_trace.write_bytes(b"outside")
    assert trace_run.trace is not None
    trace_run.trace.unlink()
    trace_run.trace.symlink_to(outside_trace)
    at.selectbox[0].set_value(1).run()
    at.checkbox[0].set_value(True).run()
    assert any("trace.zip 不可用" in item.value for item in at.warning)


def test_dashboard_empty_root_is_readable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "runs").mkdir()

    at = _dashboard_app(tmp_path, monkeypatch).run(timeout=20)

    assert at.exception == []
    assert any("没有找到可查看的运行记录" in item.value for item in at.info)


def test_dashboard_hides_page_derived_fields_and_downloads_until_reveal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    _write_run(runs, "run", status="PASS", reason="sensitive page reason")

    at = _dashboard_app(tmp_path, monkeypatch).run(timeout=20)

    text = "\n".join(
        [item.value for item in at.markdown]
        + [item.value for item in at.caption]
        + [item.value for item in at.warning]
        + [item.value for item in at.info]
    )
    assert "sensitive page reason" not in text
    assert "project dashboard" not in text
    assert at.download_button == []


def test_dashboard_marks_unhashable_observation_unavailable_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    run = _write_run(runs, "malformed", status="UNKNOWN", reason="unknown", trace=None)
    report_path = run / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["observations"][0]["id"] = []
    report_path.write_text(json.dumps(report), encoding="utf-8")

    at = _dashboard_app(tmp_path, monkeypatch).run(timeout=20)

    assert at.exception == []
    assert "UNAVAILABLE" in str(at.dataframe[0].value["状态"].tolist())
    assert at.checkbox == []


def test_events_export_preserves_original_order_and_safe_action_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    run = _write_run(runs, "run", status="UNKNOWN", reason="unknown", trace=None)
    (run / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "action", "action": {"kind": "click", "locator": "#save", "checkpoint_id": "c1"}, "success": False, "steps": 2, "error_class": "TimeoutError"}),
                json.dumps({"type": "observation", "id": "o1", "url": "https://example.test", "text": "saved", "screenshot": "screenshots/o1.png", "fingerprint": "f"}),
                json.dumps({"type": "action", "action": {"kind": "fill", "locator": "#password", "value": "secret"}, "success": True, "steps": 3}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    selected = list_runs(runs)[0]

    exported = (_events_export(runs, selected) or b"").decode("utf-8").splitlines()
    events = [json.loads(line) for line in exported]

    assert [event["type"] for event in events] == ["action", "observation", "action"]
    assert events[0]["success"] is False
    assert events[0]["steps"] == 2
    assert events[0]["error_class"] == "TimeoutError"
    assert events[2]["success"] is True
    assert "value" not in events[2]["action"]
    assert "password" not in exported[2]


def test_dashboard_exports_drop_cookie_fields_and_never_offer_trace_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = tmp_path / "runs"
    run = _write_run(runs, "cookie-run", status="PASS", reason="ok")
    report_path = run / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["cookie"] = {"name": "accessToken", "value": "cookie-secret"}
    report_path.write_text(json.dumps(report), encoding="utf-8")
    events_path = run / "events.jsonl"
    events_path.write_text(
        json.dumps(
            {
                "type": "observation",
                "id": "o1",
                "url": "https://example.test",
                "text": "dashboard",
                "cookie": {"name": "accessToken", "value": "cookie-secret"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    selected = list_runs(runs)[0]
    assert b"cookie-secret" not in (_report_export(runs, selected) or b"")
    assert b"cookie-secret" not in (_events_export(runs, selected) or b"")

    at = _dashboard_app(tmp_path, monkeypatch).run(timeout=20)
    at.checkbox[0].set_value(True).run()
    assert not any(button.label == "下载 trace.zip" for button in at.download_button)


def test_report_export_preserves_scoped_contract_metadata(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    run_dir = _write_run(runs, "scoped", status="PASS", reason="ok", trace=None)
    report_path = run_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["checkpoints"] = [
        {
            "id": "dashboard",
            "source": "appears on dashboard",
            "expected": "Course Test",
            "required": True,
            "scope": "dashboard",
            "target": "Course Test",
        }
    ]
    report["observations"][0]["regions"] = [
        {"id": "dashboard", "text": "Course Test", "locator": "main"}
    ]
    report["observations"][0]["text"] = "Course Test"
    report["verdict"]["evidence"] = [
        {
            "checkpoint_id": "dashboard",
            "observation_id": "o1",
            "quote": "Course Test",
            "status": "SAT",
            "region_id": "dashboard",
        }
    ]
    report_path.write_text(json.dumps(report), encoding="utf-8")

    selected = list_runs(runs)[0]
    exported = json.loads((_report_export(runs, selected) or b"{}").decode("utf-8"))

    assert exported["checkpoints"][0]["scope"] == "dashboard"
    assert exported["checkpoints"][0]["target"] == "Course Test"
    assert exported["observations"][0]["regions"][0]["id"] == "dashboard"
    assert exported["verdict"]["evidence"][0]["region_id"] == "dashboard"
