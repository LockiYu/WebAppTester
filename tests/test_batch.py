import argparse
import csv
import json
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
        report = tmp_path / f"report-{index}.json"
        report.write_text(
            json.dumps(
                {
                    "steps": index,
                    "verdict": {
                        "status": ("PASS", "FAIL", "UNKNOWN")[index - 1],
                        "reason": f"reason {index}",
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

    with (batch_dir / "batch-summary.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["scenario"] == "first, scenario"
    assert rows[0]["status"] == "PASS"


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
        return 1, tmp_path / "second-report.json"

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
