"""Immutable, validated values shared by the web tester roles."""

from dataclasses import dataclass
from typing import Final


ACTION_KINDS: Final[frozenset[str]] = frozenset(
    {"goto", "click", "fill", "select", "scroll", "drag", "stop"}
)
EVIDENCE_STATUSES: Final[frozenset[str]] = frozenset(
    {"SAT", "VIOLATED", "UNKNOWN"}
)
VERDICT_STATUSES: Final[frozenset[str]] = frozenset(
    {"PASS", "FAIL", "UNKNOWN"}
)


def _require_text(value: object, field: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if nonempty and not value.strip():
        raise ValueError(f"{field} must be nonempty")
    return value


@dataclass(frozen=True, slots=True)
class Scenario:
    text: str
    url: str
    max_steps: int = 35

    def __post_init__(self) -> None:
        _require_text(self.text, "scenario text")
        _require_text(self.url, "scenario URL")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 35:
            raise ValueError("max_steps must be between 1 and 35")


@dataclass(frozen=True, slots=True)
class Checkpoint:
    id: str
    source: str
    expected: str
    required: bool = True

    def __post_init__(self) -> None:
        _require_text(self.id, "checkpoint id")
        _require_text(self.source, "checkpoint source")
        _require_text(self.expected, "checkpoint expected")
        if type(self.required) is not bool:
            raise ValueError("checkpoint required must be a boolean")


@dataclass(frozen=True, slots=True)
class Action:
    kind: str
    locator: str = ""
    value: str = ""
    checkpoint_id: str = ""

    def __post_init__(self) -> None:
        _require_text(self.kind, "action kind")
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"unsupported action kind: {self.kind!r}")
        _require_text(self.locator, "locator", nonempty=False)
        _require_text(self.value, "value", nonempty=False)
        _require_text(self.checkpoint_id, "checkpoint_id", nonempty=False)


@dataclass(frozen=True, slots=True)
class Observation:
    id: str
    url: str
    text: str
    screenshot: str
    fingerprint: str

    def __post_init__(self) -> None:
        _require_text(self.id, "observation id")
        _require_text(self.url, "observation URL")
        _require_text(self.text, "observation text", nonempty=False)
        _require_text(self.screenshot, "observation screenshot", nonempty=False)
        _require_text(self.fingerprint, "observation fingerprint", nonempty=False)


@dataclass(frozen=True, slots=True)
class Evidence:
    checkpoint_id: str
    observation_id: str
    quote: str
    status: str

    def __post_init__(self) -> None:
        _require_text(self.checkpoint_id, "evidence checkpoint_id")
        _require_text(self.observation_id, "evidence observation_id")
        _require_text(self.quote, "evidence quote")
        _require_text(self.status, "evidence status")
        if self.status not in EVIDENCE_STATUSES:
            raise ValueError(f"unsupported evidence status: {self.status!r}")


@dataclass(frozen=True, slots=True)
class Verdict:
    status: str
    evidence: tuple[Evidence, ...]
    reason: str

    def __post_init__(self) -> None:
        _require_text(self.status, "verdict status")
        if self.status not in VERDICT_STATUSES:
            raise ValueError(f"unsupported verdict status: {self.status!r}")
        if not isinstance(self.evidence, tuple) or not all(
            isinstance(item, Evidence) for item in self.evidence
        ):
            raise ValueError("verdict evidence must be a tuple of Evidence")
        _require_text(self.reason, "verdict reason", nonempty=False)


def _mapping(raw: object, allowed: set[str]) -> dict[str, object]:
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("invalid fields")
    return raw


def parse_scenario(raw: dict[str, object]) -> Scenario:
    values = _mapping(raw, {"text", "url", "max_steps"})
    if "text" not in values or "url" not in values:
        raise ValueError("scenario requires text and url")
    for key in ("text", "url"):
        _require_text(values[key], key)
    if "max_steps" in values and type(values["max_steps"]) is not int:
        raise ValueError("max_steps must be an integer")
    return Scenario(**values)


def parse_checkpoint(raw: dict[str, object]) -> Checkpoint:
    values = _mapping(raw, {"id", "source", "expected", "required"})
    for key in ("id", "source", "expected"):
        if key not in values:
            raise ValueError(f"checkpoint requires {key}")
        _require_text(values[key], f"checkpoint {key}")
    if "required" in values and type(values["required"]) is not bool:
        raise ValueError("checkpoint required must be a boolean")
    return Checkpoint(**values)


def parse_checkpoints(raw: list[dict[str, object]]) -> tuple[Checkpoint, ...]:
    if not isinstance(raw, list):
        raise ValueError("checkpoints must be a list")
    checkpoints = tuple(parse_checkpoint(item) for item in raw)
    ids = [checkpoint.id for checkpoint in checkpoints]
    if len(ids) != len(set(ids)):
        raise ValueError("checkpoint IDs must be unique")
    return checkpoints


def parse_action(raw: dict[str, object]) -> Action:
    allowed = {"kind", "locator", "value", "checkpoint_id"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("invalid action fields")
    if not isinstance(raw.get("kind"), str):
        raise ValueError("action kind must be a string")
    for key in ("locator", "value", "checkpoint_id"):
        if key in raw and not isinstance(raw[key], str):
            raise ValueError(f"{key} must be a string")
    return Action(**raw)


def parse_observation(raw: dict[str, object]) -> Observation:
    values = _mapping(raw, {"id", "url", "text", "screenshot", "fingerprint"})
    required = ("id", "url", "text", "screenshot", "fingerprint")
    if any(key not in values for key in required):
        raise ValueError("observation has missing required fields")
    for key in required:
        _require_text(values[key], key, nonempty=key in {"id", "url"})
    return Observation(**values)


def parse_evidence(raw: dict[str, object]) -> Evidence:
    values = _mapping(raw, {"checkpoint_id", "observation_id", "quote", "status"})
    required = ("checkpoint_id", "observation_id", "quote", "status")
    if any(key not in values for key in required):
        raise ValueError("evidence has missing required fields")
    for key in required:
        if not isinstance(values[key], str):
            raise ValueError(f"{key} must be a string")
    return Evidence(**values)


def parse_verdict(raw: dict[str, object]) -> Verdict:
    values = _mapping(raw, {"status", "evidence", "reason"})
    if "status" not in values or "evidence" not in values or "reason" not in values:
        raise ValueError("verdict has missing required fields")
    if not isinstance(values["status"], str):
        raise ValueError("status must be a string")
    if not isinstance(values["reason"], str):
        raise ValueError("reason must be a string")
    if not isinstance(values["evidence"], list):
        raise ValueError("evidence must be a list")
    evidence = tuple(parse_evidence(item) for item in values["evidence"])
    return Verdict(status=values["status"], evidence=evidence, reason=values["reason"])
