import json
import os
import time
from pathlib import Path

import pytest

from webapptester.results import RunRecord, artifact_path, list_runs, load_run


def _report(
    *,
    status: str = "PASS",
    reason: str = "all required checkpoints have supported SAT evidence",
    steps: int = 2,
    trace_path: str | None = "trace.zip",
    evidence: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "steps": steps,
        "elapsed_seconds": 1.25,
        "token_usage": {"prompt": 10, "completion": 4},
        "trace_path": trace_path,
        "actions": [{"kind": "goto", "locator": "https://example.test", "checkpoint_id": ""}],
        "observations": [
            {
                "id": "o1",
                "url": "https://example.test",
                "text": "visible page",
                "screenshot": "screenshots/observation-0001.png",
                "fingerprint": "abc",
            }
        ],
        "verdict": {
            "status": status,
            "reason": reason,
            "evidence": evidence
            if evidence is not None
            else [
                {
                    "checkpoint_id": "c1",
                    "observation_id": "o1",
                    "quote": "visible page",
                    "status": "SAT",
                }
            ],
        },
    }


def _write_report(run: Path, report: dict[str, object]) -> Path:
    run.mkdir(parents=True, exist_ok=True)
    report_path = run / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def test_list_runs_returns_valid_history_newest_first_and_preserves_fields(tmp_path: Path) -> None:
    older = tmp_path / "older"
    newer = tmp_path / "newer"
    for run in (older, newer):
        (run / "screenshots").mkdir(parents=True)
        (run / "screenshots" / "observation-0001.png").write_bytes(b"png")
        (run / "trace.zip").write_bytes(b"trace")
    _write_report(older, _report(status="UNKNOWN", reason="provider unavailable", steps=0))
    _write_report(newer, _report(status="PASS"))
    now = time.time()
    os.utime(older / "report.json", (now - 10, now - 10))
    os.utime(newer / "report.json", (now, now))

    runs = list_runs(tmp_path)

    assert [run.directory for run in runs] == [newer.resolve(), older.resolve()]
    assert runs[0].status == "PASS"
    assert runs[0].evidence[0]["quote"] == "visible page"
    assert runs[1].status == "UNKNOWN"
    assert runs[1].reason == "provider unavailable"
    assert runs[1].steps == 0
    assert runs[0].trace == (newer / "trace.zip").resolve()


def test_corrupt_and_incomplete_reports_are_omitted(tmp_path: Path) -> None:
    valid = tmp_path / "valid"
    corrupt = tmp_path / "corrupt"
    incomplete = tmp_path / "incomplete"
    _write_report(valid, _report(status="UNKNOWN", reason="missing evidence", evidence=[]))
    corrupt.mkdir()
    (corrupt / "report.json").write_text("{not json", encoding="utf-8")
    _write_report(incomplete, {"steps": 1, "verdict": {"status": "PASS"}})

    runs = list_runs(tmp_path)

    assert [run.directory for run in runs] == [valid.resolve()]
    with pytest.raises(ValueError):
        load_run(tmp_path, corrupt / "report.json")


def test_pass_with_missing_evidence_is_not_loaded_as_pass(tmp_path: Path) -> None:
    report_path = _write_report(tmp_path / "run", _report(status="PASS", evidence=[]))

    with pytest.raises(ValueError):
        load_run(tmp_path, report_path)


def test_artifact_path_rejects_escape_paths_and_accepts_files_inside_run(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "screenshots").mkdir(parents=True)
    screenshot = run_dir / "screenshots" / "observation.png"
    screenshot.write_bytes(b"png")
    report_path = _write_report(run_dir, _report(status="UNKNOWN", trace_path=None, evidence=[]))
    run = load_run(tmp_path, report_path)

    assert artifact_path(tmp_path, run, "screenshots/observation.png") == screenshot.resolve()
    assert artifact_path(tmp_path, run, "../../outside.png") is None
    assert artifact_path(tmp_path, run, str(tmp_path.parent / "outside.png")) is None
    assert artifact_path(tmp_path, run, "missing.png") is None


def test_legacy_absolute_and_working_directory_artifacts_are_allowed_only_inside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_root = tmp_path / "runs"
    run_dir = runs_root / "legacy"
    (run_dir / "screenshots").mkdir(parents=True)
    screenshot = run_dir / "screenshots" / "observation.png"
    screenshot.write_bytes(b"png")
    trace = run_dir / "trace.zip"
    trace.write_bytes(b"trace")
    report_path = _write_report(
        run_dir,
        _report(
            status="UNKNOWN",
            trace_path="runs/legacy/trace.zip",
            evidence=[],
        ),
    )
    monkeypatch.chdir(tmp_path)
    run = load_run(runs_root, report_path)

    assert artifact_path(runs_root, run, str(screenshot)) == screenshot.resolve()
    assert artifact_path(runs_root, run, "runs/legacy/screenshots/observation.png") == screenshot.resolve()
    assert artifact_path(runs_root, run, str(tmp_path / "outside.png")) is None


def test_action_values_are_not_exposed(tmp_path: Path) -> None:
    report = _report(status="UNKNOWN", evidence=[])
    report["actions"] = [{"kind": "fill", "locator": "#password", "value": "secret"}]
    report_path = _write_report(tmp_path / "run", report)

    run = load_run(tmp_path, report_path)

    assert run.actions == ({"kind": "fill", "locator": "#password"},)
    assert all("value" not in action for action in run.actions)


def test_load_run_rejects_report_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    outside = tmp_path / "outside"
    report_path = _write_report(outside, _report(status="UNKNOWN", evidence=[]))

    with pytest.raises(ValueError):
        load_run(root, report_path)

