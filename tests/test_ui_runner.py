import json
from pathlib import Path

import pytest

from webapptester import ui_runner, ui_worker


class _Process:
    def __init__(self) -> None:
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


def test_dashboard_launch_keeps_scenario_out_of_process_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    process = _Process()

    def fake_popen(command: list[str], **kwargs: object) -> _Process:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(ui_runner.subprocess, "Popen", fake_popen)
    job = ui_runner.start_ui_job(
        scenario="Verify private project Alpha", url="http://127.0.0.1:3001/",
        provider="ollama", model="qwen3.5:9b", max_steps=5,
        storage_state=None, root=tmp_path / "runs", variant="B3",
    )

    assert job.process is process
    command = captured["command"]
    assert isinstance(command, list)
    assert "Verify private project Alpha" not in " ".join(command)
    assert command[1:3] == ["-m", "webapptester.ui_worker"]
    request_path = job.directory / "request.json"
    assert request_path.stat().st_mode & 0o777 == 0o600
    assert (job.directory / "runner.log").stat().st_mode & 0o777 == 0o600
    assert json.loads(request_path.read_text(encoding="utf-8"))["scenario"] == "Verify private project Alpha"


def test_dashboard_progress_reads_action_counts_without_page_text(tmp_path: Path) -> None:
    process = _Process()
    directory = tmp_path / "job"
    run = directory / "attempt"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text(
        "\n".join((
            json.dumps({"type": "action", "action": {"kind": "goto"}, "success": True, "steps": 1}),
            json.dumps({"type": "observation", "text": "secret page text"}),
            json.dumps({"type": "action", "action": {"kind": "click"}, "success": False, "steps": 2}),
            json.dumps({"type": "gap_replanned", "checkpoint_id": "c1", "candidate_count": 2}),
            json.dumps({"type": "budget_arbitration", "decision": "select_gap"}),
        )) + "\n", encoding="utf-8",
    )
    job = ui_runner.UiJob(process, directory, 5)
    progress = ui_runner.read_ui_progress(job)
    assert (progress.status, progress.steps, progress.last_action, progress.errors) == (
        "运行中", 2, "click", 1
    )
    assert progress.phase == "预算仲裁"
    assert "secret" not in repr(progress)

    (run / "report.json").write_text(json.dumps({
        "steps": 2, "elapsed_seconds": 1.0,
        "token_usage": {"prompt": 0, "completion": 0},
        "actions": [], "observations": [],
        "verdict": {"status": "UNKNOWN", "reason": "browser action failed", "evidence": []},
    }), encoding="utf-8")
    process.exit_code = 2
    finished = ui_runner.read_ui_progress(job)
    assert finished.status == "UNKNOWN"
    assert finished.phase == "已结束"
    assert finished.report_path == run / "report.json"


def test_dashboard_request_rejects_external_auth_and_credential_url(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    external_auth = tmp_path / "auth.json"
    external_auth.write_text("{}", encoding="utf-8")
    request = dict(
        scenario="Verify heading", url="http://127.0.0.1:3001/",
        provider="ollama", model="qwen3.5:9b", max_steps=5,
        storage_state=external_auth, root=root, variant="B3",
    )
    with pytest.raises(ValueError, match="登录状态文件"):
        ui_runner.validate_request(**request)
    (root / "report.json").write_text("{}", encoding="utf-8")
    request["storage_state"] = root / "report.json"
    with pytest.raises(ValueError, match="登录状态文件"):
        ui_runner.validate_request(**request)
    request["storage_state"] = None
    request["url"] = "http://admin:secret@127.0.0.1:3001/"
    with pytest.raises(ValueError, match="账号密码"):
        ui_runner.validate_request(**request)


def test_dashboard_import_and_worker_pass_only_selected_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    imported = ui_runner.parse_scenario_upload(
        "cases.json", json.dumps([
            {"scenario": "Check A", "url": "http://a.test", "storage_state": "ignored.json"},
            {"scenario": "Check B", "url": "http://b.test"},
        ]).encode(),
    )
    assert imported == (("Check A", "http://a.test"), ("Check B", "http://b.test"))

    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({
        "scenario": "Check A", "url": "http://a.test", "provider": "ollama",
        "model": "qwen3.5:9b", "max_steps": 5, "variant": "B3",
        "storage_state": None,
    }), encoding="utf-8")
    captured = {}

    def fake_run(args: object) -> tuple[int, Path]:
        captured["args"] = args
        return 0, tmp_path / "report.json"

    monkeypatch.setattr(ui_worker.cli, "run", fake_run)
    assert ui_worker.main([str(request_path)]) == 0
    assert captured["args"].scenario == "Check A"
    assert captured["args"].ablation_variant == "B3"
