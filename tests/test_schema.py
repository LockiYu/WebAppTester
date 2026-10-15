import pytest

from webapptester.schema import (
    Action,
    Checkpoint,
    Scenario,
    expected_targets_supported,
    parse_action,
    parse_checkpoints,
    scope_matches_region,
)


def test_scenario_rejects_budget_over_course_limit():
    with pytest.raises(ValueError):
        Scenario("create a project", "http://localhost:3000", 36)


def test_action_rejects_uncontrolled_code():
    with pytest.raises(ValueError):
        parse_action({"kind": "evaluate", "locator": "document.body.remove()"})


def test_checkpoint_requires_source_mapping():
    with pytest.raises(ValueError):
        parse_checkpoints([{"id": "c1", "source": "", "expected": "project exists"}])


def test_checkpoint_scope_must_name_one_region():
    with pytest.raises(ValueError):
        Checkpoint("c1", "verify", "visible", scope="dashboard and sidebar")


def test_expected_target_ignores_natural_language_generic_words():
    assert expected_targets_supported("The Course Test is visible", "Course Test")
    assert expected_targets_supported("Project Alpha is visible", "Alpha")


def test_semantic_role_region_aliases_bind_to_atomic_scopes():
    assert scope_matches_region("dashboard", "role-main", "[role=main]")
    assert scope_matches_region("sidebar", "role-complementary", '[role="complementary"]')
