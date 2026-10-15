from __future__ import annotations

import json
from types import SimpleNamespace

from webapptester.controller import Controller, verdict_from_evidence
from webapptester.schema import (
    Action,
    Checkpoint,
    Evidence,
    Observation,
    ObservationRegion,
    Scenario,
)


def _checkpoint(identifier: str = "c1", required: bool = True) -> Checkpoint:
    return Checkpoint(identifier, "verify this", "visible state", required)


def _observation(identifier: str = "o1", text: str = "visible state", fingerprint: str = "same") -> Observation:
    return Observation(identifier, "https://example.test", text, "shot.png", fingerprint)


def _evidence(checkpoint_id: str = "c1", observation_id: str = "o1", status: str = "SAT", quote: str = "visible state") -> Evidence:
    return Evidence(checkpoint_id, observation_id, quote, status)


def _scoped_observation(
    *, dashboard: str = "Dashboard project: Course Test", sidebar: str = "Sidebar project: Course Test"
) -> Observation:
    return Observation(
        "o-scoped",
        "https://example.test",
        f"{dashboard}\n{sidebar}",
        "shot.png",
        "scoped",
        (
            ObservationRegion("dashboard", dashboard, "main"),
            ObservationRegion("sidebar", sidebar, "aside"),
        ),
    )


def test_all_required_sat_evidence_from_observations_passes() -> None:
    checkpoints = (_checkpoint("c1"), _checkpoint("c2"))
    observations = (_observation("o1"), _observation("o2", "sidebar visible"))
    evidence = (_evidence("c1", "o1"), _evidence("c2", "o2", quote="sidebar visible"))

    verdict = verdict_from_evidence(checkpoints, evidence, observations)

    assert verdict.status == "PASS"
    assert len(verdict.evidence) == 2


def test_unknown_required_evidence_cannot_pass() -> None:
    verdict = verdict_from_evidence(
        (_checkpoint("c1"), _checkpoint("c2")),
        (_evidence("c1"), _evidence("c2", status="UNKNOWN")),
        (_observation("o1"),),
    )

    assert verdict.status == "UNKNOWN"


def test_unreferenced_sat_quote_is_unknown() -> None:
    verdict = verdict_from_evidence(
        (_checkpoint(),),
        (_evidence("other"),),
        (_observation(),),
    )

    assert verdict.status == "UNKNOWN"


def test_unsupported_extra_evidence_forces_unknown() -> None:
    verdict = verdict_from_evidence(
        (_checkpoint(),),
        (_evidence(), _evidence("unexpected", quote="visible state")),
        (_observation(),),
    )

    assert verdict.status == "UNKNOWN"


def test_direct_violated_quote_is_fail() -> None:
    verdict = verdict_from_evidence(
        (_checkpoint(),),
        (_evidence(status="VIOLATED"),),
        (_observation(),),
    )

    assert verdict.status == "FAIL"


def test_one_location_cannot_pass_a_two_region_assertion() -> None:
    checkpoints = (
        Checkpoint("dashboard", "appears on dashboard", "Course Test", True, "dashboard"),
        Checkpoint("sidebar", "appears in sidebar", "Course Test", True, "sidebar"),
    )
    observation = Observation(
        "o-one",
        "https://example.test",
        "Dashboard project: Course Test",
        "shot.png",
        "one",
        (ObservationRegion("dashboard", "Dashboard project: Course Test", "main"),),
    )
    evidence = (
        Evidence("dashboard", "o-one", "Course Test", "SAT", "dashboard"),
        # A body quote without a captured sidebar region is deliberately not
        # enough to satisfy the second assertion.
        Evidence("sidebar", "o-one", "Course Test", "SAT"),
    )

    assert verdict_from_evidence(checkpoints, evidence, (observation,)).status == "UNKNOWN"


def test_missing_project_cannot_pass_scoped_checkpoint() -> None:
    checkpoint = Checkpoint("dashboard", "project appears", "Course Test", True, "dashboard")
    observation = _scoped_observation(dashboard="Dashboard project: Other")
    evidence = Evidence("dashboard", observation.id, "Course Test", "SAT", "dashboard")

    assert verdict_from_evidence((checkpoint,), (evidence,), (observation,)).status == "UNKNOWN"


def test_both_scoped_locations_can_pass() -> None:
    observation = _scoped_observation()
    checkpoints = (
        Checkpoint("dashboard", "appears on dashboard", "Course Test", True, "dashboard"),
        Checkpoint("sidebar", "appears in sidebar", "Course Test", True, "sidebar"),
    )
    evidence = (
        Evidence("dashboard", observation.id, "Course Test", "SAT", "dashboard"),
        Evidence("sidebar", observation.id, "Course Test", "SAT", "sidebar"),
    )

    assert verdict_from_evidence(checkpoints, evidence, (observation,)).status == "PASS"


class _FakePlanner:
    def __init__(self, checkpoints: tuple[Checkpoint, ...]) -> None:
        self.checkpoints = checkpoints

    def plan(self, scenario: Scenario) -> tuple[Checkpoint, ...]:
        return self.checkpoints


class _FakeExecutor:
    def __init__(self, actions: list[Action]) -> None:
        self.actions = actions
        self.calls = 0

    def next_action(self, scenario, checkpoints, observation, history):
        self.calls += 1
        if len(self.actions) == 1:
            return self.actions[0]
        return self.actions.pop(0)


class _FakeVerifier:
    def __init__(self, evidence_factory=None) -> None:
        self.evidence_factory = evidence_factory

    def verify(self, checkpoints, observations):
        if self.evidence_factory is None:
            return ()
        return tuple(self.evidence_factory(observations))


class _FakeBrowser:
    def __init__(self, *, success: bool = True, max_steps: int = 35) -> None:
        self.success = success
        self.max_steps = max_steps
        self.steps = 0
        self.observations = 0

    def observe(self, store):
        self.observations += 1
        return _observation(f"o{self.observations}")

    def act(self, action):
        self.steps += 1
        return SimpleNamespace(
            action=action,
            success=self.success,
            error="TimeoutError: secret input value" if not self.success else "",
            steps=self.steps,
        )


class _RecoveryBrowser(_FakeBrowser):
    def __init__(self, results):
        super().__init__()
        self.latest_snapshot = ""
        self.results = list(results)

    def observe(self, store):
        self.observations += 1
        self.latest_snapshot = f"snapshot-{self.observations}"
        text = "completed" if self.observations >= 3 else "not completed"
        return _observation(f"o{self.observations}", text=text, fingerprint=f"state-{self.observations}")

    def act(self, action):
        self.steps += 1
        result = self.results.pop(0)
        return SimpleNamespace(action=action, steps=self.steps, **result)


class _SnapshotExecutor:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []

    def next_action(
        self,
        scenario,
        checkpoints,
        observation,
        history,
        accessibility_snapshot="",
        failure_hint="",
    ):
        self.calls.append((accessibility_snapshot, failure_hint))
        return self.actions.pop(0)


class _GroundedVerifier:
    def __init__(self):
        self.calls = 0

    def verify(self, checkpoints, observations):
        self.calls += 1
        current = observations[-1]
        if current.text == "completed":
            return (_evidence(observation_id=current.id, quote="completed"),)
        return ()


class _AliasedExecutor:
    def __init__(self) -> None:
        self.calls = []

    def next_action(
        self,
        scenario,
        checkpoints,
        observation,
        history,
        gap=None,
        latest_snapshot="",
        **kwargs,
    ):
        self.calls.append((gap, latest_snapshot, kwargs))
        return Action("stop")


class _RaisingBrowser(_FakeBrowser):
    def act(self, action):
        self.steps += 1
        raise RuntimeError("secret-value must not be persisted")


class _FakeStore:
    def __init__(self) -> None:
        self.events = []
        self.report = None

    def append_event(self, event):
        self.events.append(event)

    def write_report(self, report):
        self.report = report


def _scenario(max_steps: int = 35) -> Scenario:
    return Scenario("verify this", "https://example.test", max_steps=max_steps)


def test_action_timeout_alone_is_unknown_and_redacts_action_value() -> None:
    store = _FakeStore()
    browser = _FakeBrowser(success=False)
    controller = Controller(
        _FakePlanner((_checkpoint(),)),
        _FakeExecutor([Action("fill", "#password", "secret-value")]),
        _FakeVerifier(),
        browser,
        store,
    )

    verdict = controller.run(_scenario())

    assert verdict.status == "UNKNOWN"
    report_text = json.dumps(store.report)
    assert "secret-value" not in report_text
    assert "secret-value" not in json.dumps(store.events)


def test_controller_report_persists_checkpoint_definitions() -> None:
    store = _FakeStore()
    checkpoint = Checkpoint("dashboard", "appears on dashboard", "Course Test", True, "dashboard")
    controller = Controller(
        _FakePlanner((checkpoint,)),
        _FakeExecutor([Action("stop")]),
        _FakeVerifier(),
        _FakeBrowser(),
        store,
    )

    controller.run(_scenario())

    assert store.report["checkpoints"] == [
        {
            "id": "dashboard",
            "source": "appears on dashboard",
            "expected": "Course Test",
            "required": True,
            "scope": "dashboard",
        }
    ]


def test_browser_exception_after_reserving_step_is_appended_without_secret() -> None:
    store = _FakeStore()
    browser = _RaisingBrowser()
    controller = Controller(
        _FakePlanner((_checkpoint(),)),
        _FakeExecutor([Action("fill", "#password", "secret-value")]),
        _FakeVerifier(),
        browser,
        store,
    )

    verdict = controller.run(_scenario())

    assert verdict.status == "UNKNOWN"
    assert browser.steps == 1
    assert store.events[0]["error_class"] == "RuntimeError"
    event_text = json.dumps(store.events)
    assert "secret-value" not in event_text
    assert "must not be persisted" not in event_text


def test_nonrecoverable_action_result_skips_verifier_after_observing_failure() -> None:
    store = _FakeStore()
    verifier = _GroundedVerifier()
    browser = _FakeBrowser(success=False)

    verdict = Controller(
        _FakePlanner((_checkpoint(),)),
        _FakeExecutor([Action("click", "#button")]),
        verifier,
        browser,
        store,
    ).run(_scenario())

    assert verdict.status == "UNKNOWN"
    assert verifier.calls == 0
    assert browser.steps == 1
    assert browser.observations == 2
    assert store.events[0]["error_class"] == "TimeoutError"


def test_explicit_executor_aliases_win_over_kwargs_fallback() -> None:
    browser = SimpleNamespace(latest_snapshot="current snapshot")
    executor = _AliasedExecutor()
    controller = Controller(
        _FakePlanner((_checkpoint(),)), executor, _FakeVerifier(), browser, _FakeStore()
    )

    action = controller._next_action(
        _scenario(),
        (_checkpoint(),),
        _observation(),
        (),
        None,
        failure_hint="ambiguous_locator",
    )

    assert action.kind == "stop"
    assert executor.calls == [
        (None, "current snapshot", {"failure_hint": "ambiguous_locator"})
    ]


def test_ambiguous_locator_recovers_with_fresh_snapshot_and_grounded_pass() -> None:
    store = _FakeStore()
    executor = _SnapshotExecutor(
        [
            Action("click", "role=button;name=Add Project", value="secret-value"),
            Action("click", "ref=e2"),
        ]
    )
    verifier = _GroundedVerifier()
    browser = _RecoveryBrowser(
        [
            {"success": False, "error": "AmbiguousLocatorError: duplicate button"},
            {"success": True, "error": ""},
        ]
    )

    verdict = Controller(
        _FakePlanner((_checkpoint(),)),
        executor,
        verifier,
        browser,
        store,
    ).run(_scenario())

    assert verdict.status == "PASS"
    assert verifier.calls == 1
    assert executor.calls == [("snapshot-1", ""), ("snapshot-2", "ambiguous_locator")]
    assert [event["steps"] for event in store.events] == [1, 2]
    assert store.events[0]["success"] is False
    assert store.events[0]["error_class"] == "AmbiguousLocatorError"
    assert store.events[1]["success"] is True
    report_text = json.dumps(store.report)
    assert "secret-value" not in report_text


def test_repeated_ambiguous_locator_stops_after_two_retries_without_verification() -> None:
    store = _FakeStore()
    executor = _SnapshotExecutor(
        [Action("click", f"ref=e{index}") for index in range(1, 4)]
    )
    verifier = _GroundedVerifier()
    browser = _RecoveryBrowser(
        [
            {"success": False, "error": "AmbiguousLocatorError: duplicate button"},
            {"success": False, "error": "AmbiguousLocatorError: duplicate button"},
            {"success": False, "error": "AmbiguousLocatorError: duplicate button"},
        ]
    )

    verdict = Controller(
        _FakePlanner((_checkpoint(),)),
        executor,
        verifier,
        browser,
        store,
    ).run(_scenario(max_steps=10))

    assert verdict.status == "UNKNOWN"
    assert verifier.calls == 0
    assert browser.steps == 3
    assert len(store.events) == 3
    assert all(event["success"] is False for event in store.events)
    assert store.report["steps"] == 3


def test_budget_exhaustion_terminates_at_exactly_35_steps() -> None:
    store = _FakeStore()
    browser = _FakeBrowser()
    actions = [Action("click", f"#button-{index}") for index in range(35)]
    controller = Controller(
        _FakePlanner((_checkpoint(),)),
        _FakeExecutor(actions),
        _FakeVerifier(),
        browser,
        store,
    )

    verdict = controller.run(_scenario())

    assert verdict.status == "UNKNOWN"
    assert browser.steps == 35
    assert store.report["steps"] == 35


def test_repeated_state_and_action_stops_unknown_before_unbounded_loop() -> None:
    store = _FakeStore()
    browser = _FakeBrowser()
    controller = Controller(
        _FakePlanner((_checkpoint(),)),
        _FakeExecutor([Action("click", "#same")]),
        _FakeVerifier(),
        browser,
        store,
    )

    verdict = controller.run(_scenario())

    assert verdict.status == "UNKNOWN"
    assert browser.steps == 2
    assert "loop limit" in verdict.reason


def test_shared_provider_usage_is_counted_once_across_roles() -> None:
    provider = SimpleNamespace(usage={"prompt": 7, "completion": 3})
    planner = SimpleNamespace(provider=provider)
    executor = SimpleNamespace(provider=provider)
    verifier = SimpleNamespace(provider=provider)
    controller = Controller(planner, executor, verifier, _FakeBrowser(), _FakeStore())

    assert controller._token_usage() == {"prompt": 7, "completion": 3}


class _MalformedGapVerifier:
    def verify(self, checkpoints, observations):
        return {
            "evidence": (),
            "gap_request": {
                "checkpoint_id": "c1",
                "requested_evidence": 123,
            },
        }


def test_malformed_gap_request_forces_unknown_instead_of_being_ignored() -> None:
    store = _FakeStore()
    controller = Controller(
        _FakePlanner((_checkpoint(),)),
        _FakeExecutor([Action("click", "#button")]),
        _MalformedGapVerifier(),
        _FakeBrowser(),
        store,
    )

    verdict = controller.run(_scenario())

    assert verdict.status == "UNKNOWN"
    assert "ValueError" in verdict.reason


def test_unknown_report_preserves_steps_reserved_before_failed_navigation(tmp_path) -> None:
    from webapptester.cli import _unknown_report
    from webapptester.store import RunStore

    store = RunStore(tmp_path)
    report = _unknown_report(
        store,
        trace_path=tmp_path / "trace.zip",
        started=0.0,
        reason="initial navigation failed",
        steps=1,
    )

    assert json.loads(report.read_text())["steps"] == 1
