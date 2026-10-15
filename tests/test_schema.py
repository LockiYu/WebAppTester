import pytest

from webapptester.schema import Action, Scenario, parse_action, parse_checkpoints


def test_scenario_rejects_budget_over_course_limit():
    with pytest.raises(ValueError):
        Scenario("create a project", "http://localhost:3000", 36)


def test_action_rejects_uncontrolled_code():
    with pytest.raises(ValueError):
        parse_action({"kind": "evaluate", "locator": "document.body.remove()"})


def test_checkpoint_requires_source_mapping():
    with pytest.raises(ValueError):
        parse_checkpoints([{"id": "c1", "source": "", "expected": "project exists"}])
