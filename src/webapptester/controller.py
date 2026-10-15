"""Deterministic run controller and evidence-gated verdict calculation.

The controller is deliberately boring: roles propose structured values, while
this module owns the action budget, loop detection, evidence provenance, and
the final three-state verdict.  Browser/context and tracing lifecycles belong
to the caller; the controller only uses the supplied gateway.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .browser import BudgetExhausted
from .schema import Action, Checkpoint, Evidence, Observation, Scenario, Verdict


MAX_STEPS = 35
REPEAT_LIMIT = 3


@dataclass(frozen=True, slots=True)
class GapRequest:
    """Optional verifier request for a missing piece of checkpoint evidence."""

    checkpoint_id: str
    requested_evidence: str = ""
    suggested_location: str = ""
    estimated_action_cost: int = 1


def verdict_from_evidence(
    checkpoints: Iterable[Checkpoint],
    evidence: Iterable[Evidence],
    observations: Iterable[Observation],
) -> Verdict:
    """Return a verdict only when evidence is directly grounded in observations.

    A model supplied status is never sufficient by itself.  The observation ID
    must exist, and the quote must be a non-empty substring of that stored
    observation.  Optional checkpoints do not gate the result, but their
    evidence is retained in the returned verdict for reporting.
    """

    checkpoint_values = tuple(checkpoints)
    evidence_values = tuple(evidence)
    observation_values = tuple(observations)

    observation_by_id: dict[str, Observation] = {}
    duplicate_observations: set[str] = set()
    for observation in observation_values:
        observation_id = getattr(observation, "id", None)
        if not isinstance(observation_id, str):
            continue
        if observation_id in observation_by_id:
            duplicate_observations.add(observation_id)
        observation_by_id[observation_id] = observation

    required = tuple(checkpoint for checkpoint in checkpoint_values if checkpoint.required)
    if not required:
        return Verdict(
            status="UNKNOWN",
            evidence=evidence_values,
            reason="no required checkpoints were supplied",
        )

    evidence_by_checkpoint: dict[str, list[Evidence]] = {}
    checkpoint_ids = {checkpoint.id for checkpoint in checkpoint_values}
    for item in evidence_values:
        checkpoint_id = getattr(item, "checkpoint_id", None)
        observation_id = getattr(item, "observation_id", None)
        quote = getattr(item, "quote", None)
        status = getattr(item, "status", None)
        observation = observation_by_id.get(observation_id)
        # Validate every submitted item, including optional or extra entries.
        # Otherwise an unsupported model claim could sit beside valid SAT
        # evidence and still accidentally produce PASS.
        if (
            not isinstance(item, Evidence)
            or not isinstance(checkpoint_id, str)
            or checkpoint_id not in checkpoint_ids
            or not isinstance(observation_id, str)
            or observation is None
            or observation_id in duplicate_observations
            or not isinstance(quote, str)
            or not quote.strip()
            or quote not in observation.text
            or status not in {"SAT", "VIOLATED", "UNKNOWN"}
        ):
            return Verdict(
                status="UNKNOWN",
                evidence=evidence_values,
                reason="submitted evidence is unsupported or not grounded in an observation",
            )
        evidence_by_checkpoint.setdefault(checkpoint_id, []).append(item)

    supported: dict[str, Evidence] = {}
    for checkpoint in required:
        candidates = evidence_by_checkpoint.get(checkpoint.id, [])
        if len(candidates) != 1:
            return Verdict(
                status="UNKNOWN",
                evidence=evidence_values,
                reason=f"required checkpoint {checkpoint.id} lacks one supported evidence item",
            )

        item = candidates[0]
        observation_id = getattr(item, "observation_id", None)
        quote = getattr(item, "quote", None)
        status = getattr(item, "status", None)
        observation = observation_by_id.get(observation_id)
        if (
            observation is None
            or observation_id in duplicate_observations
            or not isinstance(quote, str)
            or not quote.strip()
            or quote not in observation.text
            or status not in {"SAT", "VIOLATED", "UNKNOWN"}
        ):
            return Verdict(
                status="UNKNOWN",
                evidence=evidence_values,
                reason=f"required checkpoint {checkpoint.id} has unsupported evidence",
            )
        supported[checkpoint.id] = item

    if any(item.status == "UNKNOWN" for item in supported.values()):
        return Verdict(
            status="UNKNOWN",
            evidence=evidence_values,
            reason="a required checkpoint remains UNKNOWN",
        )
    if any(item.status == "VIOLATED" for item in supported.values()):
        return Verdict(
            status="FAIL",
            evidence=evidence_values,
            reason="a required checkpoint is directly shown to be VIOLATED",
        )
    return Verdict(
        status="PASS",
        evidence=evidence_values,
        reason="all required checkpoints have supported SAT evidence",
    )


class Controller:
    """Run one scenario through the supplied roles and browser gateway."""

    def __init__(self, planner: Any, executor: Any, verifier: Any, browser: Any, store: Any) -> None:
        self.planner = planner
        self.executor = executor
        self.verifier = verifier
        self.browser = browser
        self.store = store

    def run(self, scenario: Scenario) -> Verdict:
        started = time.monotonic()
        checkpoints: tuple[Checkpoint, ...] = ()
        observations: list[Observation] = []
        evidence: list[Evidence] = []
        actions: list[Action] = []
        history: list[Action] = []
        gap_request: GapRequest | None = None
        current_verdict = Verdict("UNKNOWN", (), "run has not produced evidence")
        repeat_counts: dict[tuple[str, tuple[object, ...]], int] = {}
        action_failure = False
        stop_reason: str | None = None

        try:
            checkpoints = tuple(self.planner.plan(scenario))
            initial = self.browser.observe(self.store)
            observations.append(initial)

            while True:
                current_steps = self._steps(actions)
                if current_steps >= self._budget(scenario):
                    stop_reason = "browser action budget exhausted"
                    break

                action = self._next_action(
                    scenario,
                    checkpoints,
                    initial,
                    tuple(history),
                    gap_request,
                )
                if not isinstance(action, Action):
                    raise ValueError("executor returned an invalid action")

                repeat_key = (initial.fingerprint, self._action_key(action))
                repeat_counts[repeat_key] = repeat_counts.get(repeat_key, 0) + 1
                if repeat_counts[repeat_key] >= REPEAT_LIMIT:
                    stop_reason = "repeated state and action reached loop limit"
                    break

                actions.append(action)
                history.append(action)
                if action.kind == "stop":
                    stop_reason = "executor requested stop"
                    break

                try:
                    result = self.browser.act(action)
                except BudgetExhausted:
                    self._append_action_event(action, success=False, error_class="BudgetExhausted")
                    stop_reason = "browser action budget exhausted"
                    break
                except Exception as error:
                    self._append_action_event(
                        action,
                        success=False,
                        error_class=type(error).__name__,
                    )
                    stop_reason = "browser action failed"
                    break

                success = bool(getattr(result, "success", True))
                self._append_action_event(
                    action,
                    success=success,
                    error_class=self._error_class(getattr(result, "error", "")) if not success else "",
                    steps=getattr(result, "steps", None),
                )
                if not success:
                    action_failure = True

                initial = self.browser.observe(self.store)
                observations.append(initial)
                new_evidence, gap_request = self._verify(checkpoints, tuple(observations))
                self._merge_evidence(evidence, new_evidence)
                current_verdict = verdict_from_evidence(checkpoints, evidence, observations)

                if current_verdict.status in {"PASS", "FAIL"}:
                    if action_failure:
                        current_verdict = Verdict(
                            "UNKNOWN",
                            tuple(evidence),
                            "a browser action failed; the result cannot be classified as an application verdict",
                        )
                    else:
                        return self._finish(
                            current_verdict,
                            checkpoints,
                            actions,
                            observations,
                            started,
                        )
                if action_failure:
                    stop_reason = "browser action failed"
                    break

            if stop_reason:
                current_verdict = Verdict(
                    "UNKNOWN",
                    tuple(evidence),
                    stop_reason,
                )
            return self._finish(
                current_verdict,
                checkpoints,
                actions,
                observations,
                started,
            )
        except Exception as error:
            # Exception messages may contain locator or input values.  Keep the
            # report class-only so a secret cannot leak through diagnostics.
            current_verdict = Verdict(
                "UNKNOWN",
                tuple(evidence),
                f"controller stopped after {type(error).__name__}",
            )
            return self._finish(
                current_verdict,
                checkpoints,
                actions,
                observations,
                started,
            )

    def _finish(
        self,
        verdict: Verdict,
        checkpoints: tuple[Checkpoint, ...],
        actions: list[Action],
        observations: list[Observation],
        started: float,
    ) -> Verdict:
        report = {
            "steps": self._steps(actions),
            "elapsed_seconds": max(0.0, time.monotonic() - started),
            "token_usage": self._token_usage(),
            "trace_path": self._trace_path(),
            "actions": [self._action_dict(action) for action in actions],
            "observations": [asdict(observation) for observation in observations],
            "verdict": {
                "status": verdict.status,
                "reason": verdict.reason,
                "evidence": [asdict(item) for item in verdict.evidence],
            },
        }
        self.store.write_report(report)
        return verdict

    def _verify(
        self,
        checkpoints: tuple[Checkpoint, ...],
        observations: tuple[Observation, ...],
    ) -> tuple[tuple[Evidence, ...], GapRequest | None]:
        result = self.verifier.verify(checkpoints, observations)
        gap: GapRequest | None = None
        values: object = result
        if isinstance(result, dict):
            values = result.get("evidence", ())
            gap = self._coerce_gap(result.get("gap_request"))
        elif hasattr(result, "evidence"):
            values = getattr(result, "evidence")
            gap = self._coerce_gap(getattr(result, "gap_request", None))
        elif isinstance(result, tuple) and len(result) == 2 and not all(
            isinstance(item, Evidence) for item in result
        ):
            values, gap_value = result
            gap = self._coerce_gap(gap_value)
        if values is None:
            values = ()
        evidence = tuple(values)
        if not all(isinstance(item, Evidence) for item in evidence):
            raise ValueError("verifier returned invalid evidence")
        return evidence, gap

    def _next_action(
        self,
        scenario: Scenario,
        checkpoints: tuple[Checkpoint, ...],
        observation: Observation,
        history: tuple[Action, ...],
        gap_request: GapRequest | None,
    ) -> Action:
        method = self.executor.next_action
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "gap_request" in parameters:
            return method(scenario, checkpoints, observation, history, gap_request=gap_request)
        if "gap" in parameters:
            return method(scenario, checkpoints, observation, history, gap=gap_request)
        return method(scenario, checkpoints, observation, history)

    def _append_action_event(
        self,
        action: Action,
        *,
        success: bool,
        error_class: str = "",
        steps: object = None,
    ) -> None:
        event: dict[str, object] = {
            "type": "action",
            "action": self._action_dict(action),
            "success": success,
        }
        if isinstance(steps, int):
            event["steps"] = steps
        if error_class:
            event["error_class"] = error_class
        self.store.append_event(event)

    def _steps(self, actions: list[Action]) -> int:
        value = getattr(self.browser, "steps", None)
        if type(value) is int and value >= 0:
            return value
        return sum(action.kind != "stop" for action in actions)

    def _budget(self, scenario: Scenario) -> int:
        browser_budget = getattr(self.browser, "max_steps", MAX_STEPS)
        if type(browser_budget) is not int:
            browser_budget = MAX_STEPS
        return min(scenario.max_steps, browser_budget, MAX_STEPS)

    @staticmethod
    def _action_key(action: Action) -> tuple[object, ...]:
        return (action.kind, action.locator, action.value, action.checkpoint_id)

    @staticmethod
    def _action_dict(action: Action) -> dict[str, str]:
        # Never place Action.value in reports or events.  The value can be a
        # password or another secret even when the action kind is fill.
        return {
            "kind": action.kind,
            "locator": action.locator,
            "checkpoint_id": action.checkpoint_id,
        }

    @staticmethod
    def _error_class(error: object) -> str:
        if isinstance(error, BaseException):
            return type(error).__name__
        if isinstance(error, str) and error:
            prefix = error.split(":", 1)[0].strip()
            # BrowserGateway uses ``ClassName: message``.  Accept only a
            # class-shaped prefix; arbitrary provider/browser text may itself
            # be a submitted secret and must never become a report reason.
            if prefix.isidentifier() and prefix.endswith(("Error", "Exception")):
                return prefix
        return "ActionError"

    @staticmethod
    def _merge_evidence(target: list[Evidence], incoming: tuple[Evidence, ...]) -> None:
        for item in incoming:
            target[:] = [existing for existing in target if existing.checkpoint_id != item.checkpoint_id]
            target.append(item)

    @staticmethod
    def _coerce_gap(value: object) -> GapRequest | None:
        if value is None:
            return None
        if isinstance(value, GapRequest):
            return value
        if isinstance(value, dict):
            try:
                return GapRequest(
                    checkpoint_id=str(value["checkpoint_id"]),
                    requested_evidence=str(value.get("requested_evidence", value.get("missing_evidence", ""))),
                    suggested_location=str(value.get("suggested_location", "")),
                    estimated_action_cost=int(value.get("estimated_action_cost", 1)),
                )
            except (KeyError, TypeError, ValueError):
                return None
        if hasattr(value, "checkpoint_id") and hasattr(value, "requested_evidence"):
            try:
                return GapRequest(
                    checkpoint_id=str(getattr(value, "checkpoint_id")),
                    requested_evidence=str(getattr(value, "requested_evidence")),
                    suggested_location=str(getattr(value, "suggested_location", "")),
                    estimated_action_cost=int(getattr(value, "estimated_action_cost", 1)),
                )
            except (TypeError, ValueError):
                return None
        return None

    def _trace_path(self) -> str:
        value = getattr(self.browser, "trace_path", None)
        if value is None:
            value = getattr(self.store, "trace_path", "")
        return str(value) if isinstance(value, (str, Path)) else ""

    def _token_usage(self) -> dict[str, int]:
        total = {"prompt": 0, "completion": 0}
        for role in (self.planner, self.executor, self.verifier):
            provider = getattr(role, "provider", role)
            usage = getattr(provider, "usage", None)
            if usage is None:
                usage = getattr(provider, "token_usage", None)
            if isinstance(usage, dict):
                prompt, completion = usage.get("prompt", 0), usage.get("completion", 0)
            else:
                prompt = getattr(usage, "prompt", 0)
                completion = getattr(usage, "completion", 0)
            if type(prompt) is int and prompt >= 0:
                total["prompt"] += prompt
            if type(completion) is int and completion >= 0:
                total["completion"] += completion
        return total


__all__ = ["Controller", "GapRequest", "verdict_from_evidence"]
