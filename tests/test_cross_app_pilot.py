from __future__ import annotations

import json
from pathlib import Path

from webapptester.ablation import load_scenario_manifest

from scripts import run_cross_app_pilot as pilot


def test_generated_pilot_manifest_uses_one_named_target_and_private_states(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(pilot, "RUNS", tmp_path)
    for app in pilot.APP_URLS:
        state = tmp_path / f"{app}-local" / "auth.json"
        state.parent.mkdir()
        state.write_text("{}", encoding="utf-8")
    run_dir = tmp_path / "cross-app-pilot" / "one-run"
    run_dir.mkdir(parents=True)
    name = "Cross App Pilot 20261019T000000000000Z"
    manifest = run_dir / "manifest.json"
    pilot._write_private_json(manifest, pilot._manifest_cases(name, run_dir))

    cases = load_scenario_manifest(manifest)

    assert len(cases) == 3
    assert cases[0].scenario.text.count(name) == 1
    assert all(case.scenario.max_steps == 5 for case in cases)
    assert all(case.storage_state.is_file() for case in cases)
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert json.loads(manifest.read_text())[0]["url"] == pilot.APP_URLS["4gaboards"]
