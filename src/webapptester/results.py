"""Read-only, path-safe access to persisted browser test results."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .schema import (
    ACTION_KINDS,
    checkpoint_target_supported,
    is_atomic_scope,
    scope_matches_region,
)

_STATUSES = frozenset({"PASS", "FAIL", "UNKNOWN"})
_EVIDENCE_STATUSES = frozenset({"SAT", "VIOLATED", "UNKNOWN"})
_EVIDENCE_FIELDS = frozenset({"checkpoint_id", "observation_id", "quote", "status"})
_REQUIRED_FIELDS = frozenset(
    {"steps", "elapsed_seconds", "token_usage", "actions", "observations", "verdict"}
)


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Validated, displayable metadata for one persisted run."""

    directory: Path
    status: str
    reason: str
    steps: int
    elapsed_seconds: float
    token_usage: dict[str, int]
    evidence: tuple[dict[str, str], ...]
    observations: tuple[dict[str, object], ...]
    actions: tuple[dict[str, str], ...]
    trace: Path | None
    checkpoints: tuple[dict[str, object], ...] = ()


def list_runs(root: Path) -> tuple[RunRecord, ...]:
    """Load valid ``report.json`` files under *root*, newest first.

    Invalid or incomplete reports are represented as ``UNAVAILABLE`` records.
    This keeps a partial write or corrupt artifact visible in history without
    ever allowing it to look like a successful run in a consumer such as the
    results page.
    """

    root_path = Path(root).resolve()
    if not root_path.exists() or not root_path.is_dir():
        return ()

    reports: list[tuple[int, str, Path]] = []
    try:
        candidates = root_path.rglob("report.json")
        for report_path in candidates:
            try:
                if not report_path.is_file():
                    continue
                resolved = report_path.resolve()
                if not resolved.is_relative_to(root_path):
                    continue
                stat = resolved.stat()
                reports.append((stat.st_mtime_ns, str(resolved), resolved))
            except (OSError, RuntimeError):
                continue
    except (OSError, RuntimeError):
        return ()

    reports.sort(key=lambda item: (item[0], item[1]), reverse=True)
    records: list[RunRecord] = []
    for _, _, report_path in reports:
        try:
            records.append(load_run(root_path, report_path))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            records.append(_unavailable_record(report_path.parent))
    return tuple(records)


def _unavailable_record(directory: Path) -> RunRecord:
    """Create a safe placeholder for a corrupt or incomplete report."""

    return RunRecord(
        directory=directory,
        status="UNAVAILABLE",
        reason="report unavailable: corrupt or incomplete",
        steps=0,
        elapsed_seconds=0.0,
        token_usage={"prompt": 0, "completion": 0},
        evidence=(),
        observations=(),
        actions=(),
        trace=None,
    )


def load_run(root: Path, report_path: Path) -> RunRecord:
    """Validate and load one report, rejecting paths outside *root*."""

    root_path = Path(root).resolve()
    resolved_report = Path(report_path).resolve()
    if not resolved_report.is_relative_to(root_path):
        raise ValueError("report path is outside the configured runs root")
    if not resolved_report.is_file():
        raise ValueError("report path does not name an existing file")

    with resolved_report.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("report must be a JSON object")
    if not _REQUIRED_FIELDS.issubset(raw):
        raise ValueError("report is missing required fields")

    steps = raw["steps"]
    if type(steps) is not int or not 0 <= steps <= 35:
        raise ValueError("steps must be an integer between 0 and 35")

    elapsed = raw["elapsed_seconds"]
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        raise ValueError("elapsed_seconds must be numeric")
    elapsed_seconds = float(elapsed)
    if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be finite and nonnegative")

    token_usage = _parse_token_usage(raw["token_usage"])
    actions = _parse_actions(raw["actions"])
    observations = _parse_observations(raw["observations"])
    checkpoints = _parse_checkpoints(raw.get("checkpoints", []))
    status, reason, evidence = _parse_verdict(raw["verdict"])
    _validate_evidence(evidence, observations, checkpoints)
    _validate_verdict_consistency(status, evidence, checkpoints)

    directory = resolved_report.parent
    trace_text = raw.get("trace_path")
    if trace_text is not None and not isinstance(trace_text, str):
        raise ValueError("trace_path must be a string or null")
    record = RunRecord(
        directory=directory,
        status=status,
        reason=reason,
        steps=steps,
        elapsed_seconds=elapsed_seconds,
        token_usage=token_usage,
        evidence=evidence,
        observations=observations,
            actions=actions,
            trace=None,
            checkpoints=checkpoints,
    )
    if trace_text:
        trace = artifact_path(root_path, record, trace_text)
        record = RunRecord(
            directory=record.directory,
            status=record.status,
            reason=record.reason,
            steps=record.steps,
            elapsed_seconds=record.elapsed_seconds,
            token_usage=record.token_usage,
            evidence=record.evidence,
            observations=record.observations,
                actions=record.actions,
                trace=trace,
                checkpoints=record.checkpoints,
        )
    return record


def artifact_path(root: Path, run: RunRecord, path_text: str) -> Path | None:
    """Resolve an existing artifact only when it is inside the run and root."""

    if not isinstance(path_text, str) or not path_text:
        return None
    root_path = Path(root).resolve()
    run_directory = Path(run.directory).resolve()
    if not run_directory.is_relative_to(root_path):
        return None

    path = Path(path_text)
    bases = (Path("/"),) if path.is_absolute() else (run_directory, Path.cwd())
    for base in bases:
        try:
            candidate = path.resolve() if path.is_absolute() else (base / path).resolve()
            if not candidate.is_relative_to(run_directory):
                continue
            if not candidate.is_relative_to(root_path) or not candidate.is_file():
                continue
            return candidate
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def _parse_token_usage(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        raise ValueError("token_usage must be an object")
    if not {"prompt", "completion"}.issubset(raw):
        raise ValueError("token_usage is missing required fields")
    if any(not isinstance(key, str) for key in raw):
        raise ValueError("token_usage keys must be strings")
    if any(type(value) is not int or value < 0 for value in raw.values()):
        raise ValueError("token_usage values must be nonnegative integers")
    return dict(raw)


def _parse_actions(raw: object) -> tuple[dict[str, str], ...]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("actions must be a list of dictionaries")
    actions: list[dict[str, str]] = []
    for item in raw:
        safe_item: dict[str, str] = {}
        for key in ("kind", "locator", "checkpoint_id"):
            if key not in item:
                continue
            value = item[key]
            if not isinstance(value, str):
                raise ValueError("action fields must be strings")
            safe_item[key] = value
        if safe_item.get("kind") not in ACTION_KINDS:
            raise ValueError("action kind is invalid")
        actions.append(safe_item)
    return tuple(actions)


def _parse_observations(raw: object) -> tuple[dict[str, object], ...]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("observations must be a list of dictionaries")
    fields = frozenset({"id", "url", "text", "screenshot", "fingerprint", "regions"})
    legacy_fields = frozenset({"id", "url", "text", "screenshot", "fingerprint"})
    observations: list[dict[str, object]] = []
    for item in raw:
        if set(item) not in {fields, legacy_fields}:
            raise ValueError("observation has missing or unsupported fields")
        if any(
            not isinstance(item[key], str)
            for key in ("id", "url", "text", "screenshot", "fingerprint")
        ):
            raise ValueError("observation fields must be strings")
        if not item["id"].strip() or not item["url"].strip():
            raise ValueError("observation id and url must be nonempty")
        regions = item.get("regions", [])
        if not isinstance(regions, list) or not all(isinstance(region, dict) for region in regions):
            raise ValueError("observation regions must be a list")
        region_ids: set[str] = set()
        for region in regions:
            if set(region) != {"id", "text", "locator"}:
                raise ValueError("observation region has missing or unsupported fields")
            if any(not isinstance(value, str) for value in region.values()):
                raise ValueError("observation region fields must be strings")
            if not region["id"].strip() or region["id"] in region_ids:
                raise ValueError("observation region IDs must be unique and nonempty")
            region_ids.add(region["id"])
        observations.append(dict(item))
    return tuple(observations)


def _parse_checkpoints(raw: object) -> tuple[dict[str, object], ...]:
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("checkpoints must be a list of dictionaries")
    checkpoints: list[dict[str, object]] = []
    ids: set[str] = set()
    for item in raw:
        allowed = {"id", "source", "expected", "required", "scope", "target"}
        if set(item) - allowed or not {"id", "source", "expected"}.issubset(item):
            raise ValueError("checkpoint has missing or unsupported fields")
        if any(not isinstance(item[key], str) for key in ("id", "source", "expected")):
            raise ValueError("checkpoint text fields must be strings")
        if type(item.get("required", True)) is not bool:
            raise ValueError("checkpoint required must be a boolean")
        if not isinstance(item.get("target", ""), str):
            raise ValueError("checkpoint target must be a string")
        scope = item.get("scope", "page")
        if not isinstance(scope, str) or not is_atomic_scope(scope):
            raise ValueError("checkpoint scope must name one observable region")
        checkpoint = {
            "id": item["id"],
            "source": item["source"],
            "expected": item["expected"],
            "required": item.get("required", True),
            "scope": scope,
            "target": item.get("target", ""),
        }
        if not checkpoint["id"] or not checkpoint["source"] or not checkpoint["expected"]:
            raise ValueError("checkpoint fields must be nonempty")
        if checkpoint["id"] in ids:
            raise ValueError("checkpoint IDs must be unique")
        ids.add(checkpoint["id"])
        checkpoints.append(checkpoint)
    return tuple(checkpoints)


def _parse_verdict(raw: object) -> tuple[str, str, tuple[dict[str, str], ...]]:
    if not isinstance(raw, dict):
        raise ValueError("verdict must be an object")
    status = raw.get("status")
    reason = raw.get("reason")
    evidence = raw.get("evidence")
    if status not in _STATUSES or not isinstance(reason, str):
        raise ValueError("verdict status or reason is invalid")
    if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
        raise ValueError("evidence must be a list of dictionaries")
    parsed: list[dict[str, str]] = []
    for item in evidence:
        allowed_fields = _EVIDENCE_FIELDS | {"region_id"}
        if set(item) - allowed_fields or not _EVIDENCE_FIELDS.issubset(item):
            raise ValueError("evidence has missing or unsupported fields")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in item.items()):
            raise ValueError("evidence fields must be strings")
        if any(not item[key].strip() for key in _EVIDENCE_FIELDS):
            raise ValueError("evidence fields must be nonempty")
        if item["status"] not in _EVIDENCE_STATUSES:
            raise ValueError("evidence status is invalid")
        parsed.append({**item, "region_id": item.get("region_id", "")})
    return status, reason, tuple(parsed)


def _validate_evidence(
    evidence: tuple[dict[str, str], ...],
    observations: tuple[dict[str, object], ...],
    checkpoints: tuple[dict[str, object], ...] = (),
) -> None:
    """Require every stored evidence claim to retain its observation provenance."""

    observation_by_id: dict[str, dict[str, object]] = {}
    duplicate_ids: set[str] = set()
    for observation in observations:
        observation_id = observation.get("id")
        if not isinstance(observation_id, str) or not observation_id.strip():
            continue
        if observation_id in observation_by_id:
            duplicate_ids.add(observation_id)
        observation_by_id[observation_id] = observation
    checkpoint_ids: set[str] = set()
    declared_checkpoint_ids = {str(item["id"]) for item in checkpoints}
    for item in evidence:
        checkpoint_id = item["checkpoint_id"]
        if checkpoints and checkpoint_id not in declared_checkpoint_ids:
            raise ValueError("evidence references an undeclared checkpoint")
        if checkpoint_id in checkpoint_ids:
            raise ValueError("evidence contains duplicate checkpoint IDs")
        checkpoint_ids.add(checkpoint_id)
        observation_id = item["observation_id"]
        observation = observation_by_id.get(observation_id)
        text = observation.get("text") if observation is not None else None
        if (
            observation is None
            or observation_id in duplicate_ids
            or not isinstance(text, str)
            or item["quote"] not in text
        ):
            raise ValueError("evidence is not grounded in an observation")
        checkpoint = next(
            (candidate for candidate in checkpoints if candidate["id"] == checkpoint_id),
            None,
        )
        if checkpoint is not None and checkpoint.get("scope", "page") != "page":
            region_id = item.get("region_id", "")
            regions = observation.get("regions", []) if observation is not None else []
            region = next(
                (candidate for candidate in regions if candidate.get("id") == region_id),
                None,
            )
            if (
                not isinstance(region_id, str)
                or not region_id.strip()
                or not isinstance(region, dict)
                or not isinstance(region.get("text"), str)
                or not scope_matches_region(
                    str(checkpoint["scope"]),
                    str(region.get("id", "")),
                    str(region.get("locator", "")),
                )
                or item["quote"] not in region["text"]
            ):
                raise ValueError("scoped evidence is not grounded in an observation region")
        if (
            checkpoint is not None
            and item["status"] == "SAT"
            and not checkpoint_target_supported(
                str(checkpoint.get("target", "")),
                str(checkpoint["expected"]),
                item["quote"],
            )
        ):
            raise ValueError("SAT evidence misses the expected target")

    if checkpoints:
        required_ids = {
            str(item["id"]) for item in checkpoints if item.get("required", True) is True
        }
        evidenced_ids = {item["checkpoint_id"] for item in evidence}
        if not required_ids.issubset(evidenced_ids):
            raise ValueError("evidence is missing a required checkpoint")
        scoped_regions: set[tuple[str, str]] = set()
        for item in evidence:
            checkpoint = next(
                candidate for candidate in checkpoints if candidate["id"] == item["checkpoint_id"]
            )
            if not checkpoint.get("required", True) or checkpoint.get("scope", "page") == "page":
                continue
            key = (item["observation_id"], item.get("region_id", ""))
            if key in scoped_regions:
                raise ValueError("scoped checkpoints cite the same observation region")
            scoped_regions.add(key)


def _validate_verdict_consistency(
    status: str,
    evidence: tuple[dict[str, str], ...],
    checkpoints: tuple[dict[str, object], ...] = (),
) -> None:
    """Reject status/evidence combinations that cannot be trusted."""

    if not evidence and status == "PASS":
        raise ValueError("PASS reports require observations and evidence")
    if checkpoints:
        required_ids = {
            str(item["id"])
            for item in checkpoints
            if item.get("required", True) is True
        }
        required_statuses = {
            item["status"]
            for item in evidence
            if item["checkpoint_id"] in required_ids
        }
        if status == "PASS" and required_statuses != {"SAT"}:
            raise ValueError("PASS evidence is incomplete or unsupported")
        if status == "FAIL" and "VIOLATED" not in required_statuses:
            raise ValueError("FAIL reports require directly violated evidence")
        return
    statuses = {item["status"] for item in evidence}
    if status == "PASS" and statuses != {"SAT"}:
        raise ValueError("PASS evidence is incomplete or unsupported")
    if status == "FAIL" and "VIOLATED" not in statuses:
        raise ValueError("FAIL reports require directly violated evidence")


__all__ = ["RunRecord", "artifact_path", "list_runs", "load_run"]
