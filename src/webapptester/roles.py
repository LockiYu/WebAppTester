"""Planner, Executor, and Verifier role boundaries.

Each role can ask its model provider for one JSON object.  The Python
validators below remain authoritative: a model cannot invent a source quote,
observation, locator program, or evidence claim.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .providers import ModelOutputError, ModelProvider, OllamaProvider
from .schema import (
    ACTION_KINDS,
    Action,
    Checkpoint,
    Evidence,
    Observation,
    Scenario,
    canonical_scope,
    checkpoint_target_supported,
    location_scope_pair,
    parse_action,
    parse_checkpoints,
    parse_evidence,
    same_page_url,
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
    *,
    attempts: int = 2,
    schema: dict[str, object] | None = None,
) -> T:
    """Call a provider a bounded number of times to repair malformed output."""

    for attempt in range(attempts):
        try:
            if schema is not None and isinstance(provider, OllamaProvider):
                raw = provider.complete_json(system, user, schema=schema)
            else:
                raw = provider.complete_json(system, user)
            return parse(raw)
        except (ValueError, KeyError, TypeError) as error:
            if attempt == attempts - 1:
                raise ModelOutputError("invalid role output") from error
            user += f"\nReturn corrected JSON only. Error: {error}"
    raise AssertionError("retry loop must return or raise")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


PLANNER_SYSTEM = """You are the Planner role in an evidence-gated Playwright tester.
Return one JSON object with exactly this shape:
{"checkpoints":[{"id":"c1","source":"exact quote from the scenario","expected":"visible state","required":true,"scope":"main","target":"optional named item"}]}
Every source must be copied exactly from the scenario text. IDs must be distinct.
The expected field describes visible page state. Required must be a JSON boolean.
Scope identifies one independently observable page region. Use "page" only for a
whole-page assertion. Split requirements that mention multiple locations into one
checkpoint per location, each with its own non-page scope. Never use one checkpoint
or one body-text quote to cover multiple locations.
When an assertion names a concrete item (such as a project name), copy that item
into target; target must be observable text and is checked against the evidence quote.
Do not return prose, actions, locators, code, or unsupported fields."""


EXECUTOR_SYSTEM = """You are the Executor role in an evidence-gated Playwright tester.
Return one JSON object with exactly this shape:
{"action":{"kind":"click","locator":"role=button;name=Create","value":""}}
checkpoint_id is optional; omit it unless it exactly matches a supplied checkpoint ID.
Choose exactly one allowed kind: goto, click, fill, select, scroll, drag, stop.
Locators are CSS selectors, Playwright role locators formatted role=<role>;name=<name>,
or temporary accessibility refs formatted ref=eN. Use ref=eN only when that exact ref
appears in the current accessibility snapshot; never invent a ref or reuse one from
history. When a role/name matches more than one element, use a matching current
snapshot ref so the controller can select the intended element. If multiple
current snapshot controls share a role/name, a role locator is invalid: choose
one exact ref from the current snapshot before the browser action.
Prefer a role locator or current ref copied from the accessibility snapshot.
Do not invent a CSS class from visible text (for example, ".Preferences" is
not a valid locator merely because a Preferences link is visible).
The ambiguous_controls input lists repeated current controls and their valid refs.
Use that list to select a specific ref before proposing an action.
If pending_form_input is present, fill that active textbox with its supplied
value before clicking a submit control.
If pending_form_submit is present, click its exact dialog submit locator.
If failure_hint is ambiguous_locator, the previous locator matched multiple
elements. For the same action, choose one of allowed_next_locators (current
snapshot refs) when that list is nonempty; do not repeat the role locator.
For drag, use source locator => destination locator. Do not return JavaScript, evaluate,
arbitrary code, multiple actions, or prose. If the observation URL is about:blank or
does not match the scenario URL, the next action MUST be goto with the scenario URL.
The controller executes only the action object."""


EXECUTOR_ACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["goto", "click", "fill", "select", "scroll", "drag", "stop"],
                },
                "locator": {"type": "string"},
                "value": {"type": "string"},
                "checkpoint_id": {"type": "string"},
            },
            "required": ["kind", "locator", "value"],
            "additionalProperties": False,
        }
    },
    "required": ["action"],
    "additionalProperties": False,
}


_INTERACTIVE_SNAPSHOT_REF = re.compile(
    r'^\s*-\s*(?:button|link|textbox|searchbox|combobox|checkbox|radio|'
    r'menuitem|option|tab|switch|spinbutton|slider|treeitem)\b.*?\[ref=(e\d+)\]',
    re.IGNORECASE,
)


def _executor_action_schema(
    snapshot: str, scenario: Scenario, checkpoints: tuple[Checkpoint, ...],
    history: tuple[Action, ...],
) -> dict[str, object]:
    """Constrain local-model action locators to current interactive refs.

    Drag interactions need a two-part locator, so they retain the base schema.
    Pages without AI refs also keep the existing CSS and role locator support.
    """

    refs = tuple(dict.fromkeys(
        f"ref={match.group(1)}"
        for line in snapshot.splitlines()
        if (match := _INTERACTIVE_SNAPSHOT_REF.match(line)) is not None
    ))
    if not refs or "drag" in scenario.text.casefold():
        return EXECUTOR_ACTION_SCHEMA
    open_target_refs = False
    for checkpoint in checkpoints:
        target = checkpoint.target.strip()
        if not target or re.search(
            rf"\bopen\s+{re.escape(target)}\b", scenario.text, re.IGNORECASE
        ) is None:
            continue
        target_refs = tuple(dict.fromkeys(
            f"ref={match.group(2)}"
            for line in snapshot.splitlines()
            if (match := re.match(
                r'^\s*-\s*(?:link|menuitem)\s+"([^"]+)"\s+\[ref=(e\d+)\]',
                line, re.IGNORECASE,
            )) is not None
            and re.search(rf"\b{re.escape(target)}\s*$", match.group(1), re.IGNORECASE)
        ))
        if target_refs and not any(
            action.kind == "click" and action.locator in target_refs for action in history
        ):
            refs = target_refs
            open_target_refs = True
            break
    schema = deepcopy(EXECUTOR_ACTION_SCHEMA)
    schema["properties"]["action"]["properties"]["locator"]["enum"] = ["", *refs]
    if open_target_refs:
        schema["properties"]["action"]["properties"]["kind"]["enum"] = ["click"]
        schema["properties"]["action"]["properties"]["locator"]["enum"] = list(refs)
    return schema


VERIFIER_SYSTEM = """You are the Verifier role in an evidence-gated Playwright tester.
Return one JSON object with exactly this shape:
{"evidence":[{"checkpoint_id":"c1","observation_id":"o1","quote":"copied visible text","status":"SAT","region_id":"main"}]}
Each entry must reference a supplied checkpoint and observation. Copy quote verbatim
from that observation's stored visible text. Status must be SAT, VIOLATED, or UNKNOWN.
Return at most one evidence entry per checkpoint. For a current-state presence
check, use the newest supplied observation and quote the exact named target.
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
                if "required" in item and type(item["required"]) is not bool:
                    raise ValueError("checkpoint required must be an explicit boolean")
                if "scope" in item and not isinstance(item["scope"], str):
                    raise ValueError("checkpoint scope must be a string")
            heading_checkpoint = _explicit_heading_checkpoint(scenario)
            if heading_checkpoint is not None:
                remaining: list[Checkpoint] = []
                for item in raw_checkpoints:
                    expected = item.get("expected", "")
                    if isinstance(expected, str) and "heading" in expected.casefold():
                        continue
                    checkpoint = parse_checkpoints([item])[0]
                    if checkpoint.source not in scenario.text:
                        raise ValueError("checkpoint source is not an exact scenario quote")
                    remaining.append(checkpoint)
                return (heading_checkpoint, *remaining)
            required_scopes = location_scope_pair(scenario.text)
            if required_scopes:
                return _decompose_location_checkpoints(scenario, raw_checkpoints)
            for item in raw_checkpoints:
                if "required" not in item:
                    raise ValueError("checkpoint required must be an explicit boolean")
            checkpoints = parse_checkpoints(raw_checkpoints)
            for checkpoint in checkpoints:
                if checkpoint.source not in scenario.text:
                    raise ValueError(
                        f"checkpoint source is not an exact scenario quote: {checkpoint.source!r}"
                    )
            # Only standard location pairs are decomposed.  Ordinary prose
            # containing conjunctions remains compatible with page scope.
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


def _explicit_heading_checkpoint(scenario: Scenario) -> Checkpoint | None:
    """Ground a named visible heading in the main content of the final page."""

    match = re.search(
        r"\bverify\s+(?:the\s+)?(?P<target>[A-Za-z][A-Za-z0-9 _-]{0,80}?)"
        r"\s+heading\s+is\s+visible\b",
        scenario.text,
        re.IGNORECASE,
    )
    if match is None:
        return None
    target = match.group("target").strip()
    return Checkpoint(
        "heading", match.group(0), f"{target} heading is visible", True, "main", target
    )


def _decompose_location_checkpoints(
    scenario: Scenario, raw_checkpoints: list[object]
) -> tuple[Checkpoint, ...]:
    """Build atomic standard-region assertions from an explicit scenario pair.

    The final assertion is deterministic in the scenario text; interim model
    checkpoints are execution hints and must not be allowed to collapse the
    final dashboard/sidebar coverage into one claim.
    """

    source = _location_pair_source(scenario.text)
    if not source:
        raise ValueError("multi-location scenario has no exact source span")
    target = _named_target(scenario.text)
    fallback_expected = "visible state"
    for item in raw_checkpoints:
        if not isinstance(item, dict):
            continue
        expected = item.get("expected")
        if isinstance(expected, str) and expected.strip():
            fallback_expected = expected.strip()
            break
    expected_target = target or fallback_expected
    return (
        Checkpoint(
            "dashboard",
            source,
            f"{expected_target} is visible in dashboard",
            True,
            "dashboard",
            target,
        ),
        Checkpoint(
            "sidebar",
            source,
            f"{expected_target} is visible in sidebar",
            True,
            "sidebar",
            target,
        ),
    )


def _location_pair_source(text: str) -> str:
    """Return the smallest exact sentence containing dashboard and sidebar."""

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    for sentence in sentences:
        if "dashboard" in sentence.casefold() and "sidebar" in sentence.casefold():
            return sentence.strip()
    if "dashboard" in text.casefold() and "sidebar" in text.casefold():
        return text.strip()
    return ""


def _named_target(text: str) -> str:
    """Extract a named project value from common create-and-confirm wording."""

    patterns = (
        r"(?:project\s+(?:name|named)|named)\s+(.+?)(?:\s+and\b|,\s*|\s+then\b)",
        r"\bcreate\s+(?:a\s+)?project\s+(.+?)(?:\s+and\b|,\s*|\s+then\b)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        value = match.group(1).strip(" \t\"'“”‘’")
        value = value.rstrip(" ,.;:!?\"")
        if value:
            return value
    return ""


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
        ambiguous_controls = _duplicate_snapshot_controls(sanitized_snapshot)
        safe_failure_hint = _safe_failure_hint(failure_hint)
        candidate_refs = (
            _matching_snapshot_refs(sanitized_snapshot, history[-1].locator)
            if safe_failure_hint == "ambiguous_locator" and history
            else ()
        )
        pending_form_input = _pending_form_input(
            scenario, checkpoints, observation, history, sanitized_snapshot
        )
        pending_form_submit = _pending_form_submit(
            scenario, checkpoints, observation, history, sanitized_snapshot
        )
        if _needs_return_to_scoped_start(scenario, checkpoints, observation, history):
            return Action("goto", value=scenario.url)
        payload: dict[str, object] = {
            "scenario": {"text": scenario.text, "url": scenario.url},
            "checkpoints": [
                {
                    "id": checkpoint.id,
                    "source": checkpoint.source,
                    "expected": checkpoint.expected,
                    "required": checkpoint.required,
                    "scope": checkpoint.scope,
                    "target": checkpoint.target,
                }
                for checkpoint in checkpoints
            ],
            "observation": {
                "id": observation.id,
                "url": observation.url,
                "text": observation_text,
            },
            "accessibility_snapshot": sanitized_snapshot,
            "ambiguous_controls": ambiguous_controls,
            "failure_hint": safe_failure_hint,
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
        if candidate_refs:
            payload["allowed_next_locators"] = list(candidate_refs)
        if pending_form_input is not None:
            payload["pending_form_input"] = pending_form_input
        if pending_form_submit is not None:
            payload["pending_form_submit"] = pending_form_submit
        if gap_request is not None:
            payload["gap_request"] = _gap_request_payload(gap_request)
        user = "Input:\n" + _json(payload)
        checkpoint_ids = {checkpoint.id for checkpoint in checkpoints}

        def parse(raw: dict[str, object]) -> Action:
            if not isinstance(raw, dict):
                raise TypeError("executor output must be an object")
            # Small models sometimes echo read-only input fields alongside
            # their action. Only the action can affect browser behavior.
            if set(raw) - {
                "action", "ambiguous_controls", "checkpoints", "failure_hint",
                "pending_form_input", "pending_form_submit"
            }:
                raise ValueError("executor output has unsupported fields")
            action = parse_action(raw["action"])
            if action.checkpoint_id and action.checkpoint_id not in checkpoint_ids:
                # This optional label does not authorize the browser action.
                # Discard a hallucinated label instead of discarding a valid,
                # grounded action that would otherwise move the test forward.
                action = Action(action.kind, action.locator, action.value)
            _validate_action(action, checkpoint_ids)
            if pending_form_input is not None and action.kind != "stop":
                if (
                    action.kind != "fill"
                    or action.locator != pending_form_input["locator"]
                    or action.value != pending_form_input["value"]
                ):
                    raise ValueError("fill the active textbox before another action")
            if pending_form_submit is not None and action.kind != "stop":
                if action.kind != "click" or action.locator != pending_form_submit["locator"]:
                    raise ValueError("click the current dialog submit button")
            duplicate_refs = _matching_snapshot_refs(sanitized_snapshot, action.locator)
            if len(duplicate_refs) > 1:
                raise ValueError(
                    f"role locator matches multiple current controls; choose one of {duplicate_refs}"
                )
            if (
                safe_failure_hint == "ambiguous_locator"
                and history
                and action.kind != "stop"
                and action.locator == history[-1].locator
            ):
                raise ValueError(
                    f"ambiguous locator was already tried; choose one of {candidate_refs}"
                )
            if (
                candidate_refs
                and history
                and action.kind == history[-1].kind
                and action.locator not in candidate_refs
            ):
                raise ValueError(f"ambiguous action requires one of {candidate_refs}")
            return action

        try:
            return retry_parse(
                self.provider,
                system,
                user,
                parse,
                attempts=3 if ambiguous_controls or pending_form_input or pending_form_submit else 2,
                schema=_executor_action_schema(
                    sanitized_snapshot, scenario, checkpoints, history
                ),
            )
        except ModelOutputError as error:
            if str(error) != "invalid role output":
                raise
            if pending_form_input is not None:
                # A single active textbox plus one exact scenario target is a
                # grounded recovery path; it cannot invent an input value.
                return Action("fill", pending_form_input["locator"], pending_form_input["value"])
            if pending_form_submit is not None:
                return Action("click", pending_form_submit["locator"])
            raise


def _matching_snapshot_refs(snapshot: str, failed_locator: str) -> tuple[str, ...]:
    match = re.fullmatch(r"role=([^;\s]+);name=(.+)", failed_locator)
    if match is None:
        return ()
    role, name = match.groups()
    normalized_name = " ".join(name.split()).casefold()
    candidates: list[str] = []
    for line in snapshot.splitlines():
        role_match = re.match(r'^\s*-\s*([\w-]+)\s+"([^"]+)"', line)
        if role_match is None:
            continue
        snapshot_role, snapshot_name = role_match.groups()
        if snapshot_role != role or normalized_name not in " ".join(snapshot_name.split()).casefold():
            continue
        ref_match = re.search(r"\[ref=(e\d+)\]", line)
        if ref_match is not None:
            candidates.append(f"ref={ref_match.group(1)}")
    return tuple(dict.fromkeys(candidates))


def _duplicate_snapshot_controls(snapshot: str) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for line in snapshot.splitlines():
        role_match = re.match(r'^\s*-\s*([\w-]+)\s+"([^"]+)"', line)
        ref_match = re.search(r"\[ref=(e\d+)\]", line)
        if role_match is None or ref_match is None:
            continue
        role, name = role_match.groups()
        key = (role, " ".join(name.split()).casefold())
        if key not in grouped:
            grouped[key] = (name, [])
        grouped[key][1].append(f"ref={ref_match.group(1)}")
    return [
        {"role": role, "name": name, "locators": list(dict.fromkeys(refs))}
        for (role, _), (name, refs) in grouped.items()
        if len(set(refs)) > 1
    ]


def _pending_form_input(
    scenario: Scenario,
    checkpoints: tuple[Checkpoint, ...],
    observation: Observation,
    history: tuple[Action, ...],
    snapshot: str,
) -> dict[str, str] | None:
    if not history or history[-1].kind != "click":
        return None
    targets = {
        checkpoint.target
        for checkpoint in checkpoints
        if checkpoint.required and checkpoint.target and checkpoint.target in scenario.text
    }
    if len(targets) != 1:
        return None
    target = next(iter(targets))
    if target in observation.text:
        return None
    active_refs = []
    dialog_indent: int | None = None
    for line in snapshot.splitlines():
        indent = len(line) - len(line.lstrip())
        if dialog_indent is not None and line.strip() and indent <= dialog_indent:
            dialog_indent = None
        if re.match(r"^\s*-\s*dialog\b", line):
            dialog_indent = indent
            continue
        if dialog_indent is None or indent <= dialog_indent:
            continue
        if not re.match(r"^\s*-\s*(?:textbox|searchbox|combobox)\b", line):
            continue
        if "[active]" not in line:
            continue
        ref_match = re.search(r"\[ref=(e\d+)\]", line)
        if ref_match is not None:
            active_refs.append(f"ref={ref_match.group(1)}")
    if len(set(active_refs)) != 1:
        return None
    return {"locator": active_refs[0], "value": target}


def _pending_form_submit(
    scenario: Scenario,
    checkpoints: tuple[Checkpoint, ...],
    observation: Observation,
    history: tuple[Action, ...],
    snapshot: str,
) -> dict[str, str] | None:
    if not history or history[-1].kind != "fill":
        return None
    targets = {
        checkpoint.target
        for checkpoint in checkpoints
        if checkpoint.required and checkpoint.target and checkpoint.target in scenario.text
    }
    if len(targets) != 1 or history[-1].value != next(iter(targets)):
        return None
    if history[-1].value in observation.text:
        return None
    lines = snapshot.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"^\s*-\s*dialog\b", line):
            continue
        dialog_indent = len(line) - len(line.lstrip())
        buttons: list[str] = []
        for child in lines[index + 1 :]:
            if child.strip() and len(child) - len(child.lstrip()) <= dialog_indent:
                break
            match = re.match(r'^\s*-\s*button\s+"([^"]+)".*\[ref=(e\d+)\]', child)
            if match is None or "[disabled]" in child:
                continue
            name, ref = match.groups()
            if name.strip().casefold() in {"cancel", "close", "back"}:
                continue
            buttons.append(f"ref={ref}")
        if len(set(buttons)) == 1:
            # Use the exact button observed in the dialog. Its type is not
            # present in an accessibility snapshot, so a submit selector
            # would assert an attribute that has not been observed.
            return {"locator": buttons[0]}
    return None


def _needs_return_to_scoped_start(
    scenario: Scenario,
    checkpoints: tuple[Checkpoint, ...],
    observation: Observation,
    history: tuple[Action, ...],
) -> bool:
    """Revisit the supplied start page after submitting a named item.

    A creation form can navigate to an item detail page before the requested
    dashboard and sidebar are both observable. This is a scope driven action,
    not a target application selector or a claim of test success.
    """

    scopes = {canonical_scope(item.scope) for item in checkpoints if item.required}
    if not {"main", "aside"}.issubset(scopes) or len(history) < 2:
        return False
    fill, submit = history[-2:]
    if fill.kind != "fill" or submit.kind != "click":
        return False
    targets = {item.target for item in checkpoints if item.required and item.target}
    if len(targets) != 1 or fill.value != next(iter(targets)):
        return False
    # The target may also be rendered on an item detail page.  Scoped
    # dashboard/sidebar evidence is valid only after returning to the page
    # from which the scenario began.
    if not same_page_url(observation.url, scenario.url):
        return True
    return not any(
        canonical_scope(region.id) == "main" and fill.value in region.text
        for region in observation.regions
    )


def _is_positive_presence(checkpoint: Checkpoint) -> bool:
    if not checkpoint.target or checkpoint.target not in checkpoint.expected:
        return False
    expected = checkpoint.expected.casefold()
    return bool(re.search(r"\b(?:visible|appears?|shown|exists?|listed|present)\b", expected)) and not bool(
        re.search(r"\b(?:not|absent|missing|hidden|removed)\b", expected)
    )


class Verifier:
    """Map stored observations to evidence with strict provenance checks."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    def verify(
        self,
        checkpoints: tuple[Checkpoint, ...],
        observations: tuple[Observation, ...],
        scenario_url: str = "",
    ) -> tuple[Evidence, ...] | VerificationResult:
        system = VERIFIER_SYSTEM
        start_url = scenario_url.strip() if isinstance(scenario_url, str) else ""
        if not start_url and observations:
            start_url = observations[0].url
        model_observations = (
            observations[-1:]
            if observations and all(_is_positive_presence(item) for item in checkpoints if item.required)
            else observations
        )
        user = "Verification inputs:\n" + _json(
            {
                "checkpoints": [
                    {
                        "id": checkpoint.id,
                        "source": checkpoint.source,
                        "expected": checkpoint.expected,
                        "required": checkpoint.required,
                        "scope": checkpoint.scope,
                        "target": checkpoint.target,
                    }
                    for checkpoint in checkpoints
                ],
                "scenario_url": start_url,
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
                    for observation in model_observations
                ],
            }
        )
        checkpoint_ids = {checkpoint.id for checkpoint in checkpoints}
        observation_by_id = {observation.id: observation for observation in model_observations}
        if len(observation_by_id) != len(model_observations):
            raise ValueError("observation IDs must be unique")

        def parse(raw: dict[str, object]) -> tuple[Evidence, ...] | VerificationResult:
            if not isinstance(raw, dict):
                raise TypeError("verifier output must be an object")
            if set(raw) - {"evidence", "gap_request"}:
                raise ValueError("verifier output has unsupported fields")
            values = raw["evidence"]
            if not isinstance(values, list):
                raise TypeError("evidence must be a list")
            evidence_items: list[Evidence] = []
            for value in values:
                item = parse_evidence(value)
                checkpoint = next(
                    (candidate for candidate in checkpoints if candidate.id == item.checkpoint_id),
                    None,
                )
                observation = observation_by_id.get(item.observation_id)
                if (
                    checkpoint is not None
                    and observation is not None
                    and item.status == "SAT"
                    and _is_positive_presence(checkpoint)
                ):
                    if (
                        checkpoint.scope in {"dashboard", "sidebar"}
                        and not same_page_url(observation.url, start_url)
                    ):
                        raise ValueError(
                            f"scoped presence evidence for {checkpoint.id} is not from the scenario start page"
                        )
                    region = next(
                        (candidate for candidate in observation.regions if candidate.id == item.region_id),
                        None,
                    ) if checkpoint.scope != "page" else None
                    source = region.text if region is not None else observation.text
                    if (
                        item.quote not in source
                        and
                        (checkpoint.scope == "page" or (
                            region is not None
                            and scope_matches_region(checkpoint.scope, region.id, region.locator)
                        ))
                        and checkpoint.target in source
                    ):
                        item = Evidence(
                            item.checkpoint_id,
                            item.observation_id,
                            checkpoint.target,
                            item.status,
                            item.region_id,
                        )
                evidence_items.append(item)
            evidence = tuple(evidence_items)
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
                if (
                    item.status == "SAT"
                    and _is_positive_presence(checkpoint)
                    and checkpoint.scope in {"dashboard", "sidebar"}
                    and not same_page_url(observation.url, start_url)
                ):
                    raise ValueError(
                        f"scoped presence evidence for {checkpoint.id} is not from the scenario start page"
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
                if item.status == "SAT" and not checkpoint_target_supported(
                    checkpoint.target, checkpoint.expected, item.quote
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
