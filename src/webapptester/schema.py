"""Immutable, validated values shared by the web tester roles."""

import re
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
_COMBINED_SCOPE = re.compile(r"(?:\s+(?:and|or)\s+|[,/&|])", re.IGNORECASE)
_SCOPE_ALIASES: Final[dict[str, str]] = {"dashboard": "main", "sidebar": "aside"}
_TARGET_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")


def _require_text(value: object, field: str, *, nonempty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if nonempty and not value.strip():
        raise ValueError(f"{field} must be nonempty")
    return value


def is_atomic_scope(value: str) -> bool:
    """Return whether a scope names one observable region."""

    return bool(value.strip()) and _COMBINED_SCOPE.search(value) is None


def canonical_scope(value: str) -> str:
    """Normalize the small semantic aliases accepted for standard regions."""

    normalized = value.strip().casefold()
    return _SCOPE_ALIASES.get(normalized, normalized)


def expected_targets_supported(expected: str, quote: str) -> bool:
    """Require explicit target words in a SAT quote when the expectation has them.

    Title-cased or identifier-like words are the observable target portion of
    natural-language expectations (for example ``Course Test`` or ``Alpha``).
    Generic expectations such as ``visible state`` intentionally impose no
    extra lexical constraint and remain governed by quote provenance.
    """

    targets = tuple(
        token.casefold()
        for token in _TARGET_TOKEN.findall(expected)
        if token[0].isupper() or any(character.isdigit() for character in token)
    )
    lowered_quote = quote.casefold()
    return all(target in lowered_quote for target in targets)


def location_scope_pair(text: str) -> frozenset[str]:
    """Return canonical standard regions explicitly named as a pair."""

    lowered = text.casefold()
    if "dashboard" in lowered and "sidebar" in lowered:
        return frozenset({"main", "aside"})
    if "main" in lowered and "aside" in lowered:
        return frozenset({"main", "aside"})
    return frozenset()


def scope_matches_region(scope: str, region_id: str, locator: str) -> bool:
    """Bind a checkpoint scope to the captured region identity or locator."""

    declared = canonical_scope(scope)
    return declared in {canonical_scope(region_id), canonical_scope(locator)}


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
    # ``page`` preserves the original body-text contract.  More specific
    # scopes (for example ``main`` or ``aside``) require region-grounded
    # evidence before they can contribute to a PASS verdict.
    scope: str = "page"

    def __post_init__(self) -> None:
        _require_text(self.id, "checkpoint id")
        _require_text(self.source, "checkpoint source")
        _require_text(self.expected, "checkpoint expected")
        if type(self.required) is not bool:
            raise ValueError("checkpoint required must be a boolean")
        _require_text(self.scope, "checkpoint scope")
        if not is_atomic_scope(self.scope):
            raise ValueError("checkpoint scope must name one observable region")


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
    regions: tuple["ObservationRegion", ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.id, "observation id")
        _require_text(self.url, "observation URL")
        _require_text(self.text, "observation text", nonempty=False)
        _require_text(self.screenshot, "observation screenshot", nonempty=False)
        _require_text(self.fingerprint, "observation fingerprint", nonempty=False)
        if not isinstance(self.regions, tuple) or not all(
            isinstance(item, ObservationRegion) for item in self.regions
        ):
            raise ValueError("observation regions must be a tuple of ObservationRegion")


@dataclass(frozen=True, slots=True)
class ObservationRegion:
    """A separately captured DOM region available for scoped evidence."""

    id: str
    text: str
    locator: str = ""

    def __post_init__(self) -> None:
        _require_text(self.id, "observation region id")
        _require_text(self.text, "observation region text", nonempty=False)
        _require_text(self.locator, "observation region locator", nonempty=False)


@dataclass(frozen=True, slots=True)
class Evidence:
    checkpoint_id: str
    observation_id: str
    quote: str
    status: str
    region_id: str = ""

    def __post_init__(self) -> None:
        _require_text(self.checkpoint_id, "evidence checkpoint_id")
        _require_text(self.observation_id, "evidence observation_id")
        _require_text(self.quote, "evidence quote")
        _require_text(self.status, "evidence status")
        _require_text(self.region_id, "evidence region_id", nonempty=False)
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
    values = _mapping(raw, {"id", "source", "expected", "required", "scope"})
    for key in ("id", "source", "expected"):
        if key not in values:
            raise ValueError(f"checkpoint requires {key}")
        _require_text(values[key], f"checkpoint {key}")
    if "required" in values and type(values["required"]) is not bool:
        raise ValueError("checkpoint required must be a boolean")
    if "scope" in values and not isinstance(values["scope"], str):
        raise ValueError("checkpoint scope must be a string")
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
    values = _mapping(raw, {"id", "url", "text", "screenshot", "fingerprint", "regions"})
    required = ("id", "url", "text", "screenshot", "fingerprint")
    if any(key not in values for key in required):
        raise ValueError("observation has missing required fields")
    for key in required:
        _require_text(values[key], key, nonempty=key in {"id", "url"})
    if "regions" in values:
        raw_regions = values["regions"]
        if not isinstance(raw_regions, list):
            raise ValueError("observation regions must be a list")
        regions: list[ObservationRegion] = []
        region_ids: set[str] = set()
        for item in raw_regions:
            if not isinstance(item, dict) or set(item) - {"id", "text", "locator"}:
                raise ValueError("observation region has invalid fields")
            if "id" not in item or "text" not in item:
                raise ValueError("observation region has missing required fields")
            region = ObservationRegion(
                id=item["id"],
                text=item["text"],
                locator=item.get("locator", ""),
            )
            if region.id in region_ids:
                raise ValueError("observation region IDs must be unique")
            region_ids.add(region.id)
            regions.append(region)
        values["regions"] = tuple(regions)
    return Observation(**values)


def parse_evidence(raw: dict[str, object]) -> Evidence:
    values = _mapping(raw, {"checkpoint_id", "observation_id", "quote", "status", "region_id"})
    required = ("checkpoint_id", "observation_id", "quote", "status")
    if any(key not in values for key in required):
        raise ValueError("evidence has missing required fields")
    for key in required:
        if not isinstance(values[key], str):
            raise ValueError(f"{key} must be a string")
    if "region_id" in values and not isinstance(values["region_id"], str):
        raise ValueError("region_id must be a string")
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
