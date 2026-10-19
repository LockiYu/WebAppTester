from __future__ import annotations

import json
from pathlib import Path

import pytest

from webapptester.ablation import (
    AblationConfig,
    AblationHarness,
    AblationScenario,
    VARIANT_ORDER,
    load_scenario_manifest,
    summarize,
)
from webapptester.controller import Controller, GapRequest, verdict_from_evidence
from webapptester.schema import Action, Checkpoint, Evidence, Observation, Scenario
from webapptester.results import load_run
from webapptester.ablation_cli import run_manifest


def test_variants_add_one_capability_at_a_time() -> None:
    values = [AblationConfig.for_variant(name) for name in VARIANT_ORDER]

    assert [(value.evidence_contract, value.gap_feedback, value.budget_arbitration) for value in values] == [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (True, True, True),
    ]


def test_manifest_is_external_and_strict(tmp_path) -> None:
    path = tmp_path / "scenarios.json"
    path.write_text(
        json.dumps([
            {"id": "s01", "scenario": "Open the page", "url": "http://example.test"},
        ]),
        encoding="utf-8",
    )

    cases = load_scenario_manifest(path)

    assert cases[0].scenario_id == "s01"
    assert cases[0].scenario.text == "Open the page"
    path.write_text(json.dumps([{"id": "s01", "scenario": "x", "url": "u", "extra": 1}]), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown fields"):
        load_scenario_manifest(path)


def test_manifest_resolves_private_auth_state_relative_to_manifest(tmp_path) -> None:
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps([{
        "id": "s01", "scenario": "Open the page", "url": "http://example.test",
        "storage_state": "auth.json",
    }]), encoding="utf-8")

    assert load_scenario_manifest(path)[0].storage_state == tmp_path / "auth.json"


def test_ablation_passes_fixed_deepseek_model_to_each_condition(tmp_path, monkeypatch) -> None:
    manifest = tmp_path / "scenarios.json"
    manifest.write_text(json.dumps([{
        "id": "s01", "scenario": "Open the page", "url": "http://example.test",
    }]), encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    passed = []

    def fake_run(args):
        passed.append((args.provider, args.model, args.ablation_variant))
        path = Path(args.output) / "report.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "verdict": {"status": "UNKNOWN", "reason": "test", "evidence": []},
            "steps": 0,
        }), encoding="utf-8")
        return 2, path

    monkeypatch.setattr("webapptester.ablation_cli.cli.run", fake_run)

    summary_path = run_manifest(manifest, tmp_path / "out", provider="deepseek", variants=("B0", "B3"))

    assert passed == [
        ("deepseek", "deepseek-flash", "B0"),
        ("deepseek", "deepseek-flash", "B3"),
    ]
    config = json.loads(summary_path.read_text())["run_config"]
    assert config["provider"] == "deepseek"
    assert config["model"] == "deepseek-flash"
    assert len(config["manifest_sha256"]) == 64


def test_deepseek_ablation_requires_key_and_fixed_model_before_any_case(
    tmp_path, monkeypatch,
) -> None:
    manifest = tmp_path / "scenarios.json"
    manifest.write_text(json.dumps([{
        "id": "s01", "scenario": "Open the page", "url": "http://example.test",
    }]), encoding="utf-8")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="key is unavailable"):
        run_manifest(manifest, tmp_path / "out", provider="deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    with pytest.raises(ValueError, match="require deepseek-flash"):
        run_manifest(manifest, tmp_path / "out", provider="deepseek", model="deepseek-v4-pro")
    assert not (tmp_path / "out").exists()


def test_harness_runs_each_case_and_keeps_unknown_runner_failures() -> None:
    cases = (
        # Scenario text and URL are deliberately synthetic; no formal course
        # scenario is embedded in this test or in the package.
        AblationScenario("s01", Scenario("Open the page", "http://example.test")),
    )

    def runner(case, config):
        if config.variant == "B2":
            raise RuntimeError("simulated provider failure")
        return {"status": "PASS", "steps": 3, "reason": "ok"}

    outcomes = AblationHarness(runner).run(cases)
    assert [item.variant for item in outcomes] == list(VARIANT_ORDER)
    assert outcomes[2].status == "UNKNOWN"
    assert "RuntimeError" in outcomes[2].reason
    report = summarize(outcomes)
    assert report["variants"]["B0"]["pass_rate"] == 1.0
    assert report["variants"]["B2"]["unknown"] == 1


def test_b0_accepts_verifier_status_without_b1_provenance_checks() -> None:
    checkpoint = Checkpoint("c1", "verify", "Course Test is visible", True, target="Course Test")
    unsupported = Evidence("c1", "missing-observation", "unsupported quote", "SAT")

    baseline = verdict_from_evidence(
        (checkpoint,),
        (unsupported,),
        (),
        evidence_contract=False,
    )
    strict = verdict_from_evidence((checkpoint,), (unsupported,), (), evidence_contract=True)

    assert baseline.status == "PASS"
    assert strict.status == "UNKNOWN"


def test_b0_report_can_be_loaded_with_explicit_relaxed_contract(tmp_path) -> None:
    run_dir = tmp_path / "b0"
    run_dir.mkdir()
    report = {
        "steps": 1,
        "elapsed_seconds": 0.1,
        "token_usage": {"prompt": 1, "completion": 1},
        "actions": [{"kind": "click", "locator": "#button", "checkpoint_id": ""}],
        "observations": [],
        "checkpoints": [{
            "id": "c1", "source": "verify", "expected": "visible state",
            "required": True, "scope": "page", "target": "",
        }],
        "ablation": {
            "variant": "B0", "evidence_contract": False,
            "gap_feedback": False, "budget_arbitration": False,
        },
        "verdict": {
            "status": "PASS", "reason": "baseline verifier status",
            "evidence": [{
                "checkpoint_id": "c1", "observation_id": "unavailable",
                "quote": "model supplied quote", "status": "SAT",
            }],
        },
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    loaded = load_run(tmp_path, report_path)

    assert loaded.status == "PASS"


class _Planner:
    def plan(self, scenario):
        return (Checkpoint("c1", "verify", "visible state", True),)


class _Executor:
    def __init__(self):
        self.calls = 0

    def next_action(self, scenario, checkpoints, observation, history, **kwargs):
        self.calls += 1
        return Action("click", "#button")


class _GapVerifier:
    def verify(self, checkpoints, observations):
        return {
            "evidence": (),
            "gap_request": GapRequest("c1", "capture the missing state", estimated_action_cost=2),
        }


class _Browser:
    max_steps = 2
    steps = 0

    def observe(self, store):
        return Observation("o1", "http://example.test", "nothing", "shot.png", "same")

    def act(self, action):
        self.steps += 1
        return type("Result", (), {"success": True, "steps": self.steps, "error": ""})()


class _Store:
    def __init__(self):
        self.events = []
        self.report = None

    def append_event(self, event):
        self.events.append(event)

    def write_report(self, report):
        self.report = report


def test_b3_declines_gap_when_estimated_cost_exceeds_remaining_budget() -> None:
    store = _Store()
    controller = Controller(
        _Planner(),
        _Executor(),
        _GapVerifier(),
        _Browser(),
        store,
        ablation=AblationConfig.for_variant("B3"),
    )

    verdict = controller.run(Scenario("verify", "http://example.test", max_steps=2))

    assert verdict.status == "UNKNOWN"
    assert "budget arbitration" in verdict.reason
    assert any(event.get("type") == "budget_arbitration" for event in store.events)
