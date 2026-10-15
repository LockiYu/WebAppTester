import argparse
import csv
import json
import os
from pathlib import Path

import pytest

from webapptester import batch


def test_load_batch_validates_shape_and_values(tmp_path: Path) -> None:
    path = tmp_path / "batch.json"
    path.write_text(
        json.dumps(
            [
                {"scenario": "A", "url": "http://a", "provider": "deepseek", "model": "m", "max_steps": 3},
                {"scenario": "B", "url": "http://b"},
            ]
        ),
        encoding="utf-8",
    )

    items = batch.load_batch(path)

    assert items == (
        {"scenario": "A", "url": "http://a", "provider": "deepseek", "model": "m", "max_steps": 3},
        {"scenario": "B", "url": "http://b"},
    )


@pytest.mark.parametrize(
    "payload",
    [
        [{"scenario": "A", "url": "http://a", "extra": True}],
        [],
        [{"scenario": "A", "url": "http://a", "provider": "other"}],
        [{"scenario": "A", "url": "http://a", "max_steps": 0}],
        [{"scenario": "A", "url": "http://a", "max_steps": 36}],
        [{"scenario": "A", "url": "http://a", "max_steps": True}],
        [{"scenario": "", "url": "http://a"}],
    ],
)
def test_load_batch_rejects_invalid_input(tmp_path: Path, payload: object) -> None:
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        batch.load_batch(path)


def test_run_batch_is_sequential_and_exports_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[argparse.Namespace] = []
    outcomes = [(0, "first/report.json"), (1, "second/report.json"), (2, "third/report.json")]

    def fake_run(args: argparse.Namespace) -> tuple[int, Path]:
        calls.append(args)
        index = len(calls)
        report = args.output / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        status = ("PASS", "FAIL", "UNKNOWN")[index - 1]
        observations = []
        evidence = []
        if status in {"PASS", "FAIL"}:
            observations = [{"id": "o1", "url": "http://example", "text": "visible", "screenshot": "", "fingerprint": "f"}]
            evidence = [{"checkpoint_id": "c1", "observation_id": "o1", "quote": "visible", "status": "SAT" if status == "PASS" else "VIOLATED"}]
        report.write_text(
            json.dumps(
                {
                    "steps": index,
                    "elapsed_seconds": 0.1,
                    "token_usage": {"prompt": 0, "completion": 0},
                    "actions": [],
                    "observations": observations,
                    "verdict": {
                        "status": status,
                        "reason": f"reason {index}",
                        "evidence": evidence,
                    },
                }
            ),
            encoding="utf-8",
        )
        return outcomes[index - 1][0], report

    monkeypatch.setattr(batch.cli, "run", fake_run)
    items = (
        {"scenario": "first, scenario", "url": "http://one", "model": "m1"},
        {"scenario": "second", "url": "http://two", "provider": "deepseek", "max_steps": 4},
        {"scenario": "third", "url": "http://three"},
    )

    batch_dir = batch.run_batch(items, tmp_path / "runs")
    summary = json.loads((batch_dir / "batch-summary.json").read_text(encoding="utf-8"))

    assert [call.scenario for call in calls] == ["first, scenario", "second", "third"]
    assert len({call.output for call in calls}) == 3
    assert summary["counts"] == {"PASS": 1, "FAIL": 1, "UNKNOWN": 1}
    assert [item["status"] for item in summary["items"]] == ["PASS", "FAIL", "UNKNOWN"]
    assert [item["steps"] for item in summary["items"]] == [1, 2, 3]
    assert [item["reason"] for item in summary["items"]] == ["reason 1", "reason 2", "reason 3"]
    assert "value" not in (batch_dir / "batch-summary.json").read_text(encoding="utf-8")
    json_report_paths = [item["report_path"] for item in summary["items"]]
    assert all(json_report_paths)

    with (batch_dir / "batch-summary.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["scenario"] == "first, scenario"
    assert rows[0]["status"] == "PASS"
    assert [row["report_path"] for row in rows] == json_report_paths


def test_summary_redacts_url_credentials_but_runner_receives_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_url = (
        "https://alice:password@example.test/projects?keep=ok&token=secret"
        "&API_KEY=abc&x-api-token=def&flag#section"
    )
    seen_urls: list[str] = []

    def fake_run(args: argparse.Namespace) -> tuple[int, Path]:
        seen_urls.append(args.url)
        report = args.output / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps({
                "steps": 1,
                "elapsed_seconds": 0.1,
                "token_usage": {"prompt": 0, "completion": 0},
                "actions": [],
                "observations": [{"id": "o1", "url": "http://example", "text": "visible", "screenshot": "", "fingerprint": "f"}],
                "verdict": {"status": "PASS", "reason": "ok", "evidence": [{"checkpoint_id": "c1", "observation_id": "o1", "quote": "visible", "status": "SAT"}]},
            }),
            encoding="utf-8",
        )
        return 0, report

    monkeypatch.setattr(batch.cli, "run", fake_run)
    root = batch.run_batch(
        ({"scenario": "safe URL export", "url": original_url},),
        tmp_path / "runs",
    )
    summary_text = (root / "batch-summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_text)

    assert seen_urls == [original_url]
    assert summary["items"][0]["url"] == (
        "https://example.test/projects?keep=ok&token=REDACTED&API_KEY=REDACTED"
        "&x-api-token=REDACTED&flag#section"
    )
    assert "password" not in summary_text
    assert "secret" not in summary_text
    with (root / "batch-summary.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["url"] == summary["items"][0]["url"]


def test_summary_redacts_fragment_credentials() -> None:
    assert batch._redact_url(
        "https://example.test/#section&access_token=secret&view=home&api_key=abc"
    ) == "https://example.test/#section&access_token=REDACTED&view=home&api_key=REDACTED"


def test_summary_replaces_malformed_url_without_leaking_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_url = "https://alice:password@[bad?token=secret"
    seen_urls: list[str] = []

    def fake_run(args: argparse.Namespace) -> tuple[int, Path]:
        seen_urls.append(args.url)
        report = tmp_path / "report.json"
        report.write_text(
            json.dumps({"steps": 0, "verdict": {"status": "UNKNOWN", "reason": "invalid target"}}),
            encoding="utf-8",
        )
        return 2, report

    monkeypatch.setattr(batch.cli, "run", fake_run)
    root = batch.run_batch(
        ({"scenario": "malformed URL", "url": original_url},),
        tmp_path / "runs",
    )
    summary_text = (root / "batch-summary.json").read_text(encoding="utf-8")
    summary = json.loads(summary_text)

    assert seen_urls == [original_url]
    assert summary["items"][0]["url"] == "[invalid URL]"
    assert "password" not in summary_text
    assert "secret" not in summary_text
    with (root / "batch-summary.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["url"] == "[invalid URL]"


def test_main_exit_status_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "batch.json"
    config.write_text(json.dumps([{"scenario": "A", "url": "http://a"}]), encoding="utf-8")
    summary_dir = tmp_path / "out"
    summary_dir.mkdir()

    for statuses, expected in ((["PASS"], 0), (["PASS", "FAIL"], 1), (["PASS", "UNKNOWN"], 2)):
        summary = {
            "counts": {status: statuses.count(status) for status in ("PASS", "FAIL", "UNKNOWN")},
            "items": [{"status": status} for status in statuses],
        }
        (summary_dir / "batch-summary.json").write_text(json.dumps(summary), encoding="utf-8")
        monkeypatch.setattr(batch, "run_batch", lambda items, output_root: summary_dir)
        assert batch.main([str(config), "--output", str(tmp_path / "runs")]) == expected


def test_unexpected_item_error_is_unknown_and_does_not_abort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_run(args: argparse.Namespace) -> tuple[int, Path]:
        calls.append(args.scenario)
        if args.scenario == "first":
            raise RuntimeError("setup failed")
        report = args.output / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({
            "steps": 1,
            "elapsed_seconds": 0.1,
            "token_usage": {"prompt": 0, "completion": 0},
            "actions": [],
            "observations": [{"id": "o1", "url": "http://example", "text": "visible", "screenshot": "", "fingerprint": "f"}],
            "verdict": {"status": "FAIL", "reason": "failed", "evidence": [{"checkpoint_id": "c1", "observation_id": "o1", "quote": "visible", "status": "VIOLATED"}]},
        }), encoding="utf-8")
        return 1, report

    monkeypatch.setattr(batch.cli, "run", fake_run)
    root = batch.run_batch(
        (
            {"scenario": "first", "url": "http://one"},
            {"scenario": "second", "url": "http://two"},
        ),
        tmp_path / "runs",
    )
    summary = json.loads((root / "batch-summary.json").read_text(encoding="utf-8"))

    assert calls == ["first", "second"]
    assert [item["status"] for item in summary["items"]] == ["UNKNOWN", "FAIL"]
    assert summary["items"][0]["reason"] == "runner raised RuntimeError"


def test_batch_preserves_persisted_fail_when_exit_code_disagrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(args: argparse.Namespace) -> tuple[int, Path]:
        report = args.output / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({
            "steps": 1,
            "elapsed_seconds": 0.1,
            "token_usage": {"prompt": 0, "completion": 0},
            "actions": [],
            "observations": [{"id": "o1", "url": "http://example", "text": "visible", "screenshot": "", "fingerprint": "f"}],
            "verdict": {"status": "FAIL", "reason": "failed", "evidence": [{"checkpoint_id": "c1", "observation_id": "o1", "quote": "visible", "status": "VIOLATED"}]},
        }), encoding="utf-8")
        return 0, report

    monkeypatch.setattr(batch.cli, "run", fake_run)
    root = batch.run_batch(({"scenario": "contradictory", "url": "http://one"},), tmp_path / "runs")
    summary = json.loads((root / "batch-summary.json").read_text(encoding="utf-8"))

    assert summary["items"][0]["status"] == "FAIL"
    assert summary["items"][0]["reason"] == "failed"


def test_batch_fails_closed_for_pass_with_exit_code_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(args: argparse.Namespace) -> tuple[int, Path]:
        report = args.output / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({
            "steps": 1,
            "elapsed_seconds": 0.1,
            "token_usage": {"prompt": 0, "completion": 0},
            "actions": [],
            "observations": [{"id": "o1", "url": "http://example", "text": "visible", "screenshot": "", "fingerprint": "f"}],
            "verdict": {"status": "PASS", "reason": "passed", "evidence": [{"checkpoint_id": "c1", "observation_id": "o1", "quote": "visible", "status": "SAT"}]},
        }), encoding="utf-8")
        return 1, report

    monkeypatch.setattr(batch.cli, "run", fake_run)
    root = batch.run_batch(({"scenario": "contradictory pass", "url": "http://one"},), tmp_path / "runs")
    summary = json.loads((root / "batch-summary.json").read_text(encoding="utf-8"))

    assert summary["items"][0]["status"] == "UNKNOWN"
    assert summary["items"][0]["reason"] == "runner exit code disagrees with persisted report"


def test_batch_rejects_stale_persisted_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(args: argparse.Namespace) -> tuple[int, Path]:
        report = args.output / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({
            "steps": 0,
            "elapsed_seconds": 0.1,
            "token_usage": {"prompt": 0, "completion": 0},
            "actions": [],
            "observations": [],
            "verdict": {"status": "UNKNOWN", "reason": "old", "evidence": []},
        }), encoding="utf-8")
        os.utime(report, (1, 1))
        return 2, report

    monkeypatch.setattr(batch.cli, "run", fake_run)
    root = batch.run_batch(({"scenario": "stale", "url": "http://one"},), tmp_path / "runs")
    summary = json.loads((root / "batch-summary.json").read_text(encoding="utf-8"))

    assert summary["items"][0]["status"] == "UNKNOWN"
    assert summary["items"][0]["reason"] == "persisted report unavailable or invalid"
