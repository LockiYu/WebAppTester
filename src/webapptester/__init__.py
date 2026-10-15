"""Typed protocol for evidence-gated web application testing."""

from .schema import (
    Action,
    Checkpoint,
    Evidence,
    Observation,
    Scenario,
    Verdict,
    parse_action,
    parse_checkpoint,
    parse_checkpoints,
    parse_evidence,
    parse_observation,
    parse_scenario,
    parse_verdict,
)

__all__ = [
    "Action",
    "Checkpoint",
    "Evidence",
    "Observation",
    "Scenario",
    "Verdict",
    "parse_action",
    "parse_checkpoint",
    "parse_checkpoints",
    "parse_evidence",
    "parse_observation",
    "parse_scenario",
    "parse_verdict",
]
