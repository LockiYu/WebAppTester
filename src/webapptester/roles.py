"""Planner, Executor, and Verifier role boundaries.

Each role can ask its model provider for one JSON object.  The Python
validators below remain authoritative: a model cannot invent a source quote,
observation, locator program, or evidence claim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import TypeVar

from .providers import ModelOutputError, ModelProvider
from .schema import (
    ACTION_KINDS,
    Action,
    Checkpoint,
    Evidence,
    Observation,
    Scenario,
    parse_action,
    parse_checkpoints,
    parse_evidence,
)


T = TypeVar("T")


def retry_parse(
    provider: ModelProvider,
    system: str,
    user: str,
    parse: Callable[[dict[str, object]], T],
) -> T:
    """Call a provider twice at most, correcting one malformed role output."""

    for attempt in range(2):
        try:
            return parse(provider.complete_json(system, user))
        except (ValueError, KeyError, TypeError) as error:
            if attempt:
                raise ModelOutputError("invalid role output") from error
            user += f"\nReturn corrected JSON only. Error: {error}"
    raise AssertionError("retry loop must return or raise")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


PLANNER_SYSTEM = """You are the Planner role in an evidence-gated Playwright tester.
Return one JSON object with exactly this shape:
{"checkpoints":[{"id":"c1","source":"exact quote from the scenario","expected":"visible state","required":true}]}
Every source must be copied exactly from the scenario text. IDs must be distinct.
The expected field describes visible page state. Required must be a JSON boolean.
Do not return prose, actions, locators, code, or unsupported fields."""


EXECUTOR_SYSTEM = """You are the Executor role in an evidence-gated Playwright tester.
Return one JSON object with exactly this shape:
{"action":{"kind":"click","locator":"role=button;name=Create","value":"","checkpoint_id":"c1"}}
Choose exactly one allowed kind: goto, click, fill, select, scroll, drag, stop.
Locators are CSS selectors or Playwright role locators formatted role=<role>;name=<name>.
For drag, use source locator => destination locator. Do not return JavaScript, evaluate,
arbitrary code, multiple actions, or prose. The controller executes only the action object."""


VERIFIER_SYSTEM = """You are the Verifier role in an evidence-gated Playwright tester.
Return one JSON object with exactly this shape:
{"evidence":[{"checkpoint_id":"c1","observation_id":"o1","quote":"copied visible text","status":"SAT"}]}
Each entry must reference a supplied checkpoint and observation. Copy quote verbatim
from that observation's stored visible text. Status must be SAT, VIOLATED, or UNKNOWN.
Do not infer evidence without an observation and do not return prose or unsupported fields."""


class Planner:
    """Turn a scenario into source-mapped, observable checkpoints."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def plan(self, scenario: Scenario) -> tuple[Checkpoint, ...]:
        system = PLANNER_SYSTEM
        user = "Scenario:\n" + _json(
            {"text": scenario.text, "url": scenario.url, "max_steps": scenario.max_steps}
        )

        def parse(raw: dict[str, object]) -> tuple[Checkpoint, ...]:
            if not isinstance(raw, dict):
                raise TypeError("planner output must be an object")
            if set(raw) - {"checkpoints"}:
                raise ValueError("planner output has unsupported fields")
            raw_checkpoints = raw["checkpoints"]
            if not isinstance(raw_checkpoints, list):
                raise TypeError("checkpoints must be a list")
            for item in raw_checkpoints:
                if not isinstance(item, dict):
                    raise TypeError("checkpoint must be an object")
                if "required" not in item or type(item["required"]) is not bool:
                    raise ValueError("checkpoint required must be an explicit boolean")
            checkpoints = parse_checkpoints(raw_checkpoints)
            for checkpoint in checkpoints:
                if checkpoint.source not in scenario.text:
                    raise ValueError(
                        f"checkpoint source is not an exact scenario quote: {checkpoint.source!r}"
                    )
            return checkpoints

        return retry_parse(self.provider, system, user, parse)


class Executor:
    """Propose one controlled BrowserGateway action from one observation."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def next_action(
        self,
        scenario: Scenario,
        checkpoints: tuple[Checkpoint, ...],
        observation: Observation,
        history: tuple[Action, ...],
    ) -> Action:
        system = EXECUTOR_SYSTEM
        user = "Input:\n" + _json(
            {
                "scenario": {"text": scenario.text, "url": scenario.url},
                "checkpoints": [
                    {
                        "id": checkpoint.id,
                        "source": checkpoint.source,
                        "expected": checkpoint.expected,
                        "required": checkpoint.required,
                    }
                    for checkpoint in checkpoints
                ],
                "observation": {
                    "id": observation.id,
                    "url": observation.url,
                    "text": observation.text,
                },
                "history": [
                    {
                        "kind": action.kind,
                        "locator": action.locator,
                        "value": action.value,
                        "checkpoint_id": action.checkpoint_id,
                    }
                    for action in history
                ],
            }
        )
        checkpoint_ids = {checkpoint.id for checkpoint in checkpoints}

        def parse(raw: dict[str, object]) -> Action:
            if not isinstance(raw, dict):
                raise TypeError("executor output must be an object")
            if set(raw) - {"action"}:
                raise ValueError("executor output has unsupported fields")
            action = parse_action(raw["action"])
            _validate_action(action, checkpoint_ids)
            return action

        return retry_parse(self.provider, system, user, parse)


class Verifier:
    """Map stored observations to evidence with strict provenance checks."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def verify(
        self,
        checkpoints: tuple[Checkpoint, ...],
        observations: tuple[Observation, ...],
    ) -> tuple[Evidence, ...]:
        system = VERIFIER_SYSTEM
        user = "Verification inputs:\n" + _json(
            {
                "checkpoints": [
                    {
                        "id": checkpoint.id,
                        "source": checkpoint.source,
                        "expected": checkpoint.expected,
                        "required": checkpoint.required,
                    }
                    for checkpoint in checkpoints
                ],
                "observations": [
                    {
                        "id": observation.id,
                        "url": observation.url,
                        "text": observation.text,
                        "fingerprint": observation.fingerprint,
                    }
                    for observation in observations
                ],
            }
        )
        checkpoint_ids = {checkpoint.id for checkpoint in checkpoints}
        observation_by_id = {observation.id: observation for observation in observations}
        if len(observation_by_id) != len(observations):
            raise ValueError("observation IDs must be unique")

        def parse(raw: dict[str, object]) -> tuple[Evidence, ...]:
            if not isinstance(raw, dict):
                raise TypeError("verifier output must be an object")
            if set(raw) - {"evidence"}:
                raise ValueError("verifier output has unsupported fields")
            values = raw["evidence"]
            if not isinstance(values, list):
                raise TypeError("evidence must be a list")
            evidence = tuple(parse_evidence(value) for value in values)
            seen_checkpoints: set[str] = set()
            for item in evidence:
                if item.checkpoint_id not in checkpoint_ids:
                    raise ValueError(f"unknown checkpoint ID: {item.checkpoint_id}")
                if item.checkpoint_id in seen_checkpoints:
                    raise ValueError(f"duplicate evidence for checkpoint: {item.checkpoint_id}")
                seen_checkpoints.add(item.checkpoint_id)
                observation = observation_by_id.get(item.observation_id)
                if observation is None:
                    raise ValueError(f"unknown observation ID: {item.observation_id}")
                if item.quote not in observation.text:
                    raise ValueError(
                        f"evidence quote is not copied from observation {item.observation_id}"
                    )
            return evidence

        return retry_parse(self.provider, system, user, parse)


def _validate_action(action: Action, checkpoint_ids: set[str]) -> None:
    if action.kind not in ACTION_KINDS:
        raise ValueError(f"unsupported action kind: {action.kind!r}")
    if action.checkpoint_id and action.checkpoint_id not in checkpoint_ids:
        raise ValueError(f"unknown checkpoint ID: {action.checkpoint_id}")
    if action.kind in {"click", "fill", "select", "drag"} and not action.locator.strip():
        raise ValueError(f"{action.kind} requires a locator")
    if action.locator:
        _validate_locator(action.locator, action.kind)


_ROLE_LOCATOR = re.compile(r"^role=[^;\s]+;name=.+$")
_FORBIDDEN_CODE_MARKERS = (
    "javascript:",
    "document.",
    "window.",
    "evaluate(",
    "<script",
    "() =>",
)


def _validate_locator(locator: str, kind: str) -> None:
    """Allow CSS/role selectors while rejecting executable model output."""

    lower = locator.lower()
    if any(marker in lower for marker in _FORBIDDEN_CODE_MARKERS):
        raise ValueError("locator must not contain executable code")
    if kind == "drag":
        pieces = locator.split(" => ")
        if len(pieces) != 2 or any(not piece.strip() for piece in pieces):
            raise ValueError("drag locator must be source => destination")
        for piece in pieces:
            _validate_locator(piece.strip(), "click")
        return
    if locator.startswith("role=") and not _ROLE_LOCATOR.fullmatch(locator):
        raise ValueError("role locator must use role=<role>;name=<name>")


__all__ = [
    "Executor",
    "ModelOutputError",
    "Planner",
    "Verifier",
    "retry_parse",
]
