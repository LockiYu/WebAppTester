"""Reusable configuration and result handling for B0--B3 ablations.

The course's formal scenarios are intentionally supplied at run time.  This
module contains no scenario answers or hidden expected scores; it only defines
the four feature configurations and a small harness that can run the same
external scenario manifest under each configuration.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .schema import Scenario


VARIANT_ORDER = ("B0", "B1", "B2", "B3")


@dataclass(frozen=True, slots=True)
class AblationConfig:
    """One ablation condition.

    B0 is the ordinary three-role baseline.  B1 adds the evidence contract,
    B2 adds Planner replanning for structured verifier gaps, and B3 adds
    remaining-budget arbitration over the candidate routes. The controller's
    fixed maximum remains in force in every condition.
    """

    variant: str
    evidence_contract: bool
    gap_feedback: bool
    budget_arbitration: bool

    def __post_init__(self) -> None:
        if self.variant not in VARIANT_ORDER:
            raise ValueError(f"unknown ablation variant: {self.variant!r}")
        for field in ("evidence_contract", "gap_feedback", "budget_arbitration"):
            if type(getattr(self, field)) is not bool:
                raise TypeError(f"{field} must be a boolean")

    @classmethod
    def for_variant(cls, variant: str) -> "AblationConfig":
        """Return the canonical feature set for *variant*."""

        if not isinstance(variant, str):
            raise TypeError("ablation variant must be a string")
        try:
            index = VARIANT_ORDER.index(variant.upper())
        except ValueError as error:
            raise ValueError(f"unknown ablation variant: {variant!r}") from error
        return cls(
            variant=VARIANT_ORDER[index],
            evidence_contract=index >= 1,
            gap_feedback=index >= 2,
            budget_arbitration=index >= 3,
        )

    def to_dict(self) -> dict[str, object]:
        """Return report-safe metadata for this condition."""

        return asdict(self)


@dataclass(frozen=True, slots=True)
class AblationScenario:
    """A scenario loaded from a caller-owned manifest."""

    scenario_id: str
    scenario: Scenario
    storage_state: Path | None = None


@dataclass(frozen=True, slots=True)
class AblationOutcome:
    """The comparable fields collected from one scenario run."""

    variant: str
    scenario_id: str
    status: str
    steps: int = 0
    evidence_coverage: float = 0.0
    token_prompt: int = 0
    token_completion: int = 0
    elapsed_seconds: float = 0.0
    reason: str = ""

    def __post_init__(self) -> None:
        if self.variant not in VARIANT_ORDER:
            raise ValueError(f"unknown ablation variant: {self.variant!r}")
        if self.status not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ValueError(f"unsupported ablation status: {self.status!r}")
        if type(self.steps) is not int or self.steps < 0:
            raise ValueError("ablation steps must be a non-negative integer")
        if not 0.0 <= self.evidence_coverage <= 1.0:
            raise ValueError("evidence coverage must be between zero and one")
        for field in ("token_prompt", "token_completion"):
            if type(getattr(self, field)) is not int or getattr(self, field) < 0:
                raise ValueError(f"{field} must be a non-negative integer")
        if self.elapsed_seconds < 0:
            raise ValueError("elapsed seconds must be non-negative")


def load_scenario_manifest(path: Path) -> tuple[AblationScenario, ...]:
    """Load an external scenario manifest without assuming 36 items exist.

    Each item must contain ``id``, ``scenario`` and ``url``.  ``max_steps``
    is optional and is validated by :class:`Scenario`; unknown keys are
    rejected so a typo cannot silently change an experiment.
    """

    with Path(path).open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list) or not raw:
        raise ValueError("scenario manifest must be a nonempty JSON array")
    result: list[AblationScenario] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"scenario {index} must be an object")
        if set(item) - {"id", "scenario", "url", "max_steps", "storage_state"}:
            raise ValueError(f"scenario {index} has unknown fields")
        scenario_id = item.get("id")
        if (
            not isinstance(scenario_id, str)
            or not scenario_id.strip()
            or "/" in scenario_id
            or "\\" in scenario_id
            or scenario_id in {".", ".."}
        ):
            raise ValueError(f"scenario {index} id must be a nonempty string")
        if scenario_id in seen:
            raise ValueError(f"duplicate scenario id: {scenario_id}")
        text, url = item.get("scenario"), item.get("url")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"scenario {index} scenario must be a nonempty string")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"scenario {index} url must be a nonempty string")
        max_steps = item.get("max_steps", 35)
        if type(max_steps) is not int:
            raise ValueError(f"scenario {index} max_steps must be an integer")
        state = item.get("storage_state")
        if state is not None and (not isinstance(state, str) or not state.strip()):
            raise ValueError(f"scenario {index} storage_state must be a nonempty path")
        state_path = Path(state) if state is not None else None
        if state_path is not None and not state_path.is_absolute():
            state_path = Path(path).parent / state_path
        seen.add(scenario_id)
        result.append(AblationScenario(
            scenario_id, Scenario(text, url, max_steps=max_steps), state_path
        ))
    return tuple(result)


def _outcome_from_result(
    result: object, condition: AblationConfig, case: AblationScenario
) -> AblationOutcome:
    """Coerce a runner's Verdict or report-like mapping into one outcome."""

    if isinstance(result, AblationOutcome):
        return result
    if isinstance(result, Mapping):
        source: Mapping[str, Any] = result
        status = source.get("status")
        evidence = source.get("evidence", ())
        checkpoints = source.get("checkpoints", ())
        steps = source.get("steps", 0)
        usage = source.get("token_usage", {})
        elapsed = source.get("elapsed_seconds", 0.0)
        reason = source.get("reason", "")
    else:
        status = getattr(result, "status", None)
        evidence = getattr(result, "evidence", ())
        checkpoints = ()
        steps = getattr(result, "steps", 0)
        usage = getattr(result, "token_usage", {})
        elapsed = getattr(result, "elapsed_seconds", 0.0)
        reason = getattr(result, "reason", "")
    if status not in {"PASS", "FAIL", "UNKNOWN"}:
        raise ValueError("runner result must provide PASS, FAIL, or UNKNOWN status")
    if not isinstance(evidence, (tuple, list)):
        evidence = ()
    required = [item for item in checkpoints if isinstance(item, Mapping) and item.get("required", True)]
    covered = {getattr(item, "checkpoint_id", None) for item in evidence}
    if isinstance(evidence, (tuple, list)) and evidence and isinstance(evidence[0], Mapping):
        covered = {item.get("checkpoint_id") for item in evidence}
    coverage = len({item.get("id") for item in required} & covered) / len(required) if required else 0.0
    if not isinstance(usage, Mapping):
        usage = {}
    return AblationOutcome(
        condition.variant,
        case.scenario_id,
        status,
        steps=steps if type(steps) is int and steps >= 0 else 0,
        evidence_coverage=coverage,
        token_prompt=usage.get("prompt", 0) if type(usage.get("prompt", 0)) is int else 0,
        token_completion=usage.get("completion", 0) if type(usage.get("completion", 0)) is int else 0,
        elapsed_seconds=elapsed if isinstance(elapsed, (int, float)) and elapsed >= 0 else 0.0,
        reason=reason if isinstance(reason, str) else "",
    )


class AblationHarness:
    """Run the same caller-supplied cases under selected conditions."""

    def __init__(self, runner: Callable[[AblationScenario, AblationConfig], object]) -> None:
        self.runner = runner

    def run(
        self,
        cases: Iterable[AblationScenario],
        variants: Iterable[str] = VARIANT_ORDER,
    ) -> tuple[AblationOutcome, ...]:
        cases = tuple(cases)
        configs = tuple(AblationConfig.for_variant(value) for value in variants)
        if not configs:
            raise ValueError("at least one ablation variant is required")
        outcomes: list[AblationOutcome] = []
        for config in configs:
            for case in cases:
                try:
                    outcomes.append(_outcome_from_result(self.runner(case, config), config, case))
                except Exception as error:
                    outcomes.append(
                        AblationOutcome(
                            config.variant,
                            case.scenario_id,
                            "UNKNOWN",
                            reason=f"runner stopped after {type(error).__name__}",
                        )
                    )
        return tuple(outcomes)


def summarize(outcomes: Iterable[AblationOutcome]) -> dict[str, object]:
    """Produce transparent per-variant counts and arithmetic means."""

    grouped: dict[str, list[AblationOutcome]] = {variant: [] for variant in VARIANT_ORDER}
    for outcome in outcomes:
        grouped.setdefault(outcome.variant, []).append(outcome)
    summary: dict[str, object] = {"variants": {}}
    for variant, values in grouped.items():
        count = len(values)
        summary["variants"][variant] = {
            "scenarios": count,
            "pass": sum(value.status == "PASS" for value in values),
            "fail": sum(value.status == "FAIL" for value in values),
            "unknown": sum(value.status == "UNKNOWN" for value in values),
            "pass_rate": sum(value.status == "PASS" for value in values) / count if count else None,
            "mean_evidence_coverage": sum(value.evidence_coverage for value in values) / count if count else None,
            "mean_steps": sum(value.steps for value in values) / count if count else None,
            "mean_token_prompt": sum(value.token_prompt for value in values) / count if count else None,
            "mean_token_completion": sum(value.token_completion for value in values) / count if count else None,
            "mean_elapsed_seconds": sum(value.elapsed_seconds for value in values) / count if count else None,
        }
    summary["outcomes"] = [asdict(outcome) for outcome in outcomes]
    return summary


def write_summary(
    path: Path,
    outcomes: Iterable[AblationOutcome],
    *,
    run_config: Mapping[str, str] | None = None,
) -> Path:
    """Atomically write a JSON summary under a caller-selected output path."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            summary = summarize(outcomes)
            if run_config is not None:
                summary["run_config"] = dict(run_config)
            json.dump(summary, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


__all__ = [
    "AblationConfig",
    "AblationHarness",
    "AblationOutcome",
    "AblationScenario",
    "VARIANT_ORDER",
    "load_scenario_manifest",
    "summarize",
    "write_summary",
]
