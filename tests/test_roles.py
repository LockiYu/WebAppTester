import pytest

from webapptester.roles import Executor, ModelOutputError, Planner, Verifier
from webapptester.schema import Action, Checkpoint, Observation, Scenario


class FakeProvider:
    def __init__(self, *responses: dict[str, object]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str) -> dict[str, object]:
        self.calls.append((system, user))
        if not self.responses:
            raise AssertionError("unexpected provider call")
        return self.responses.pop(0)


def _scenario() -> Scenario:
    return Scenario("Create a project named Alpha and show it in the dashboard", "http://localhost")


def _observation(text: str = "Project Alpha exists") -> Observation:
    return Observation("o1", "http://localhost", text, "shot.png", "fingerprint")


def test_planner_parses_checkpoint_and_retries_with_schema_correction() -> None:
    provider = FakeProvider(
        {"checkpoints": [{"id": "c1", "source": "wrong", "expected": "exists", "required": True}]},
        {
            "checkpoints": [
                {
                    "id": "c1",
                    "source": "Create a project named Alpha",
                    "expected": "project Alpha is visible",
                    "required": True,
                }
            ]
        },
    )
    checkpoints = Planner(provider).plan(_scenario())
    assert len(checkpoints) == 1
    assert checkpoints[0].source in _scenario().text
    assert "Return corrected JSON only" in provider.calls[1][1]


def test_planner_retries_checkpoint_without_explicit_required_boolean() -> None:
    provider = FakeProvider(
        {
            "checkpoints": [
                {
                    "id": "c1",
                    "source": "Create a project named Alpha",
                    "expected": "project Alpha is visible",
                }
            ]
        },
        {
            "checkpoints": [
                {
                    "id": "c1",
                    "source": "Create a project named Alpha",
                    "expected": "project Alpha is visible",
                    "required": True,
                }
            ]
        },
    )
    assert Planner(provider).plan(_scenario())[0].required is True
    assert len(provider.calls) == 2


def test_executor_retries_unsupported_action_then_returns_action() -> None:
    provider = FakeProvider(
        {"action": {"kind": "evaluate"}},
        {"action": {"kind": "click", "locator": "role=button;name=Create"}},
    )
    checkpoints = ()
    action = Executor(provider).next_action(_scenario(), checkpoints, _observation(), ())
    assert action == Action(kind="click", locator="role=button;name=Create")
    assert "Return corrected JSON only" in provider.calls[1][1]


def test_executor_rejects_arbitrary_code_locator() -> None:
    provider = FakeProvider(
        {"action": {"kind": "click", "locator": "javascript:alert(1)"}},
        {"action": {"kind": "click", "locator": "javascript:alert(1)"}},
    )
    with pytest.raises(ModelOutputError):
        Executor(provider).next_action(_scenario(), (), _observation(), ())


def test_executor_rejects_whitespace_only_locator() -> None:
    provider = FakeProvider(
        {"action": {"kind": "click", "locator": "   "}},
        {"action": {"kind": "click", "locator": "\t"}},
    )
    with pytest.raises(ModelOutputError):
        Executor(provider).next_action(_scenario(), (), _observation(), ())


def test_verifier_accepts_only_quotes_from_observations() -> None:
    provider = FakeProvider(
        {
            "evidence": [
                {
                    "checkpoint_id": "c1",
                    "observation_id": "o1",
                    "quote": "Project Alpha exists",
                    "status": "SAT",
                }
            ]
        }
    )
    checkpoints = (Checkpoint("c1", "Create a project", "project exists", True),)
    evidence = Verifier(provider).verify(checkpoints, (_observation(),))
    assert evidence[0].status == "SAT"


def test_verifier_rejects_unknown_observation_and_retries_once() -> None:
    provider = FakeProvider(
        {
            "evidence": [
                {
                    "checkpoint_id": "c1",
                    "observation_id": "missing",
                    "quote": "Project Alpha exists",
                    "status": "SAT",
                }
            ]
        },
        {
            "evidence": [
                {
                    "checkpoint_id": "c1",
                    "observation_id": "missing",
                    "quote": "Project Alpha exists",
                    "status": "SAT",
                }
            ]
        },
    )
    with pytest.raises(ModelOutputError):
        Verifier(provider).verify((Checkpoint("c1", "Create", "exists"),), (_observation(),))
