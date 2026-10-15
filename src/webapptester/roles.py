"""Planner, Executor, and Verifier role boundaries.

Each role can ask its model provider for one JSON object.  The Python
validators below remain authoritative: a model cannot invent a source quote,
observation, locator program, or evidence claim.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .providers import ModelOutputError, ModelProvider
from .schema import (
    ACTION_KINDS,
    Action,
    Checkpoint,
    Evidence,
    Observation,
    Scenario,
    canonical_scope,
    expected_targets_supported,
    location_scope_pair,
    parse_action,
    parse_checkpoints,
    parse_evidence,
    scope_matches_region,
)


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class GapRequest:
    """A verifier request for one missing piece of checkpoint evidence."""

    checkpoint_id: str
    requested_evidence: str
    suggested_location: str = ""
    estimated_action_cost: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint_id, str) or not self.checkpoint_id.strip():
            raise ValueError("gap request checkpoint_id must be nonempty")
        if not isinstance(self.requested_evidence, str) or not self.requested_evidence.strip():
            raise ValueError("gap request requested_evidence must be nonempty")
        if not isinstance(self.suggested_location, str):
            raise ValueError("gap request suggested_location must be a string")
        if type(self.estimated_action_cost) is not int or self.estimated_action_cost < 0:
            raise ValueError("gap request estimated_action_cost must be non-negative")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Evidence plus an optional request; no-gap calls still return a tuple."""

    evidence: tuple[Evidence, ...]
    gap_request: GapRequest


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
{"checkpoints":[{"id":"c1","source":"exact quote from the scenario","expected":"visible state","required":true,"scope":"main"}]}
Every source must be copied exactly from the scenario text. IDs must be distinct.
The expected field describes visible page state. Required must be a JSON boolean.
Scope identifies one independently observable page region. Use "page" only for a
whole-page assertion. Split requirements that mention multiple locations into one
checkpoint per location, each with its own non-page scope. Never use one checkpoint
or one body-text quote to cover multiple locations.
Do not return prose, actions, locators, code, or unsupported fields."""


EXECUTOR_SYSTEM = """You are the Executor role in an evidence-gated Playwright tester.
Return one JSON object with exactly this shape:
{"action":{"kind":"click","locator":"role=button;name=Create","value":"","checkpoint_id":"c1"}}
Choose exactly one allowed kind: goto, click, fill, select, scroll, drag, stop.
Locators are CSS selectors, Playwright role locators formatted role=<role>;name=<name>,
or temporary accessibility refs formatted ref=eN. Use ref=eN only when that exact ref
appears in the current accessibility snapshot; never invent a ref or reuse one from
history. When a role/name matches more than one element, prefer the matching current
snapshot ref so the controller can select the intended element.
For drag, use source locator => destination locator. Do not return JavaScript, evaluate,
arbitrary code, multiple actions, or prose. If the observation URL is about:blank or
does not match the scenario URL, the next action MUST be goto with the scenario URL.
The controller executes only the action object."""


VERIFIER_SYSTEM = """You are the Verifier role in an evidence-gated Playwright tester.
Return one JSON object with exactly this shape:
{"evidence":[{"checkpoint_id":"c1","observation_id":"o1","quote":"copied visible text","status":"SAT","region_id":"main"}]}
Each entry must reference a supplied checkpoint and observation. Copy quote verbatim
from that observation's stored visible text. Status must be SAT, VIOLATED, or UNKNOWN.
For a checkpoint with a non-page scope, region_id is required and must name one of
the supplied observation regions; the quote must be copied from that region's text.
Never claim SAT for a scoped checkpoint from body text alone.
When required evidence is not available, optionally include
{"gap_request":{"checkpoint_id":"c1","requested_evidence":"visible sidebar link",
"suggested_location":"sidebar","estimated_action_cost":1}}.
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
                if "scope" in item and not isinstance(item["scope"], str):
                    raise ValueError("checkpoint scope must be a string")
            checkpoints = parse_checkpoints(raw_checkpoints)
            for checkpoint in checkpoints:
                if checkpoint.source not in scenario.text:
                    raise ValueError(
                        f"checkpoint source is not an exact scenario quote: {checkpoint.source!r}"
                    )
            # Only standard location pairs are decomposed.  Ordinary prose
            # containing conjunctions remains compatible with page scope.
            required_scopes = location_scope_pair(scenario.text)
            supplied_scopes = {
                canonical_scope(checkpoint.scope)
                for checkpoint in checkpoints
                if checkpoint.scope != "page"
            }
            if required_scopes and not required_scopes.issubset(supplied_scopes):
                raise ValueError(
                    "multi-location scenario requires separately scoped checkpoints"
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
        gap_request: GapRequest | object | None = None,
        accessibility_snapshot: str = "",
        failure_hint: str = "",
    ) -> Action:
        system = EXECUTOR_SYSTEM
        prior_values = tuple(
            action.value for action in history if isinstance(action.value, str) and action.value
        )
        observation_text = _redact_values(observation.text, prior_values)
        sanitized_snapshot = _redact_values(accessibility_snapshot, prior_values)
        payload: dict[str, object] = {
            "scenario": {"text": scenario.text, "url": scenario.url},
            "checkpoints": [
                {
                    "id": checkpoint.id,
                    "source": checkpoint.source,
                    "expected": checkpoint.expected,
                    "required": checkpoint.required,
                    "scope": checkpoint.scope,
                }
                for checkpoint in checkpoints
            ],
            "observation": {
                "id": observation.id,
                "url": observation.url,
                "text": observation_text,
            },
            "accessibility_snapshot": sanitized_snapshot,
            "failure_hint": _safe_failure_hint(failure_hint),
            "history": [
                {
                    "kind": action.kind,
                    "locator": action.locator,
                    # Values may contain passwords, tokens, or other user
                    # input. Keep the shape useful to the model while never
                    # sending the prior input itself to a provider.
                    "value": "<redacted>" if action.value else "",
                    "checkpoint_id": action.checkpoint_id,
                }
                for action in history
            ],
        }
        if gap_request is not None:
            payload["gap_request"] = _gap_request_payload(gap_request)
        user = "Input:\n" + _json(payload)
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
    ) -> tuple[Evidence, ...] | VerificationResult:
        system = VERIFIER_SYSTEM
        user = "Verification inputs:\n" + _json(
            {
                "checkpoints": [
                    {
                        "id": checkpoint.id,
                        "source": checkpoint.source,
                        "expected": checkpoint.expected,
                        "required": checkpoint.required,
                        "scope": checkpoint.scope,
                    }
                    for checkpoint in checkpoints
                ],
                "observations": [
                    {
                        "id": observation.id,
                        "url": observation.url,
                        "text": observation.text,
                        "fingerprint": observation.fingerprint,
                        "regions": [
                            {"id": region.id, "text": region.text, "locator": region.locator}
                            for region in observation.regions
                        ],
                    }
                    for observation in observations
                ],
            }
        )
        checkpoint_ids = {checkpoint.id for checkpoint in checkpoints}
        observation_by_id = {observation.id: observation for observation in observations}
        if len(observation_by_id) != len(observations):
            raise ValueError("observation IDs must be unique")

        def parse(raw: dict[str, object]) -> tuple[Evidence, ...] | VerificationResult:
            if not isinstance(raw, dict):
                raise TypeError("verifier output must be an object")
            if set(raw) - {"evidence", "gap_request"}:
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
                checkpoint = next(
                    checkpoint for checkpoint in checkpoints if checkpoint.id == item.checkpoint_id
                )
                if checkpoint.scope != "page":
                    if not item.region_id:
                        raise ValueError(
                            f"scoped checkpoint {checkpoint.id} requires region_id evidence"
                        )
                    region = next(
                        (region for region in observation.regions if region.id == item.region_id),
                        None,
                    )
                    if (
                        region is None
                        or not scope_matches_region(checkpoint.scope, region.id, region.locator)
                        or item.quote not in region.text
                    ):
                        raise ValueError(
                            f"evidence quote is not grounded in region {item.region_id}"
                        )
                if item.status == "SAT" and not expected_targets_supported(
                    checkpoint.expected, item.quote
                ):
                    raise ValueError(
                        f"SAT evidence misses the expected target for {checkpoint.id}"
                    )
            if "gap_request" not in raw:
                return evidence
            gap_raw = raw["gap_request"]
            if not isinstance(gap_raw, dict):
                raise TypeError("gap_request must be an object")
            if set(gap_raw) - {
                "checkpoint_id",
                "requested_evidence",
                "suggested_location",
                "estimated_action_cost",
            }:
                raise ValueError("gap_request has unsupported fields")
            required_gap_fields = ("checkpoint_id", "requested_evidence")
            if any(field not in gap_raw for field in required_gap_fields):
                raise ValueError("gap_request has missing required fields")
            checkpoint_id = gap_raw["checkpoint_id"]
            if checkpoint_id not in checkpoint_ids:
                raise ValueError(f"unknown gap checkpoint ID: {checkpoint_id}")
            gap = GapRequest(
                checkpoint_id=checkpoint_id,
                requested_evidence=gap_raw["requested_evidence"],
                suggested_location=gap_raw.get("suggested_location", ""),
                estimated_action_cost=gap_raw.get("estimated_action_cost", 1),
            )
            return VerificationResult(evidence=evidence, gap_request=gap)

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


def _gap_request_payload(gap_request: object) -> dict[str, object]:
    """Copy only the structured, non-browser fields into an Executor prompt."""

    values = {
        "checkpoint_id": getattr(gap_request, "checkpoint_id", ""),
        "requested_evidence": getattr(gap_request, "requested_evidence", ""),
        "suggested_location": getattr(gap_request, "suggested_location", ""),
        "estimated_action_cost": getattr(gap_request, "estimated_action_cost", 1),
    }
    if not isinstance(values["checkpoint_id"], str) or not values["checkpoint_id"].strip():
        raise ValueError("gap_request checkpoint_id must be nonempty")
    if not isinstance(values["requested_evidence"], str) or not values["requested_evidence"].strip():
        raise ValueError("gap_request requested_evidence must be nonempty")
    return values


_SAFE_FAILURE_HINTS = frozenset({"ambiguous_locator"})


def _safe_failure_hint(value: object) -> str:
    """Allow only controller-produced, non-free-form recovery hints."""

    if isinstance(value, str) and value in _SAFE_FAILURE_HINTS:
        return value
    return ""


def _redact_values(text: str, values: tuple[str, ...]) -> str:
    """Remove prior action input values before text reaches a model provider."""

    if not text or not values:
        return text
    # Replace longer values first so a shorter value that is a prefix cannot
    # leave the remainder of the longer secret in the prompt.
    for value in sorted(set(values), key=len, reverse=True):
        text = text.replace(value, "<redacted>")
    return text


_ROLE_LOCATOR = re.compile(r"^role=[^;\s]+;name=.+$")
_REF_LOCATOR = re.compile(r"^ref=e\d+$")
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
    if locator.startswith("ref=") and not _REF_LOCATOR.fullmatch(locator):
        raise ValueError("ref locator must use ref=eN")


__all__ = [
    "Executor",
    "GapRequest",
    "ModelOutputError",
    "Planner",
    "VerificationResult",
    "Verifier",
    "retry_parse",
]
