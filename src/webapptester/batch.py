"""Sequential batch runner and summary export for browser scenarios."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote_plus, urlsplit, urlunsplit

from . import cli
from .browser import MAX_STEPS

_ALLOWED_KEYS = frozenset({"scenario", "url", "provider", "model", "max_steps"})
_PROVIDERS = frozenset({"ollama", "deepseek"})
_STATUSES = ("PASS", "FAIL", "UNKNOWN")
_SUMMARY_FIELDS = (
    "scenario",
    "url",
    "provider",
    "model",
    "status",
    "report_path",
    "steps",
    "reason",
)
_SENSITIVE_QUERY_TERMS = frozenset(
    {
        "accesskey",
        "auth",
        "authorization",
        "credential",
        "credentials",
        "jwt",
        "key",
        "password",
        "passwd",
        "privatekey",
        "pwd",
        "refresh_token",
        "secret",
        "session",
        "sessionid",
        "sig",
        "signature",
        "token",
    }
)


def _is_sensitive_query_key(key: str) -> bool:
    normalized = "".join(character for character in key.lower() if character.isalnum())
    if normalized in _SENSITIVE_QUERY_TERMS:
        return True
    return any(term in normalized for term in ("password", "passwd", "token", "secret", "apikey", "credential"))


def _redact_url(url: str) -> str:
    """Remove URL userinfo and common credential query values for exports."""

    if not isinstance(url, str):
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url

    netloc = parsed.netloc.rsplit("@", 1)[-1]
    query_parts: list[str] = []
    for component in parsed.query.split("&") if parsed.query else []:
        key, _separator, _ = component.partition("=")
        if _is_sensitive_query_key(unquote_plus(key)):
            query_parts.append(f"{key}=REDACTED")
        else:
            query_parts.append(component)
    query = "&".join(query_parts)
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def load_batch(path: Path) -> tuple[dict[str, object], ...]:
    """Load and strictly validate a JSON batch configuration."""

    with Path(path).open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, list) or not raw:
        raise ValueError("batch must be a nonempty JSON array")

    items: list[dict[str, object]] = []
    for index, value in enumerate(raw, start=1):
        if not isinstance(value, dict):
            raise ValueError(f"batch item {index} must be an object")
        unknown = set(value) - _ALLOWED_KEYS
        if unknown:
            raise ValueError(f"batch item {index} has unknown keys: {sorted(unknown)!r}")
        for field in ("scenario", "url"):
            field_value = value.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"batch item {index} {field} must be a nonempty string")

        provider = value.get("provider")
        if "provider" in value and provider not in _PROVIDERS:
            raise ValueError(f"batch item {index} provider must be ollama or deepseek")

        if "model" in value:
            model = value["model"]
            if not isinstance(model, str) or not model.strip():
                raise ValueError(f"batch item {index} model must be a nonempty string")

        if "max_steps" in value:
            max_steps = value["max_steps"]
            if type(max_steps) is not int or not 1 <= max_steps <= MAX_STEPS:
                raise ValueError(f"batch item {index} max_steps must be an integer from 1 through {MAX_STEPS}")

        items.append(dict(value))
    return tuple(items)


def _effective_provider(item: dict[str, object]) -> str:
    provider = item.get("provider", "ollama")
    return provider if isinstance(provider, str) else "ollama"


def _report_details(report_path: object) -> tuple[int, str]:
    """Read only the safe summary fields from a run report."""

    if not isinstance(report_path, (str, Path)):
        return 0, ""
    path = Path(report_path)
    try:
        if not path.is_file():
            return 0, ""
        with path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return 0, ""
    if not isinstance(report, dict):
        return 0, ""
    steps = report.get("steps", 0)
    if type(steps) is not int or steps < 0:
        steps = 0
    verdict = report.get("verdict")
    reason = verdict.get("reason", "") if isinstance(verdict, dict) else ""
    if not isinstance(reason, str):
        reason = ""
    return steps, reason


def _summary_report_path(report_path: object, batch_root: Path) -> str:
    if not isinstance(report_path, (str, Path)):
        return ""
    try:
        path = Path(report_path)
        root = batch_root.resolve()
        resolved = path.resolve()
        if resolved.is_relative_to(root):
            return str(resolved.relative_to(root))
        return str(path)
    except (OSError, RuntimeError, ValueError):
        return str(report_path)


def _status_from_exit_code(code: object) -> str:
    if code == 0:
        return "PASS"
    if code == 1:
        return "FAIL"
    return "UNKNOWN"


def _atomic_write_json(path: Path, payload: object) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _write_csv(path: Path, items: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(items)


def run_batch(items: Iterable[dict[str, object]], output_root: Path) -> Path:
    """Run each scenario in order and return its timestamped batch directory."""

    batch_root = cli._make_run_directory(Path(output_root))
    summary_items: list[dict[str, object]] = []
    for index, item in enumerate(items, start=1):
        provider = _effective_provider(item)
        model = item.get("model")
        max_steps = item.get("max_steps", MAX_STEPS)
        args = argparse.Namespace(
            scenario=item.get("scenario", ""),
            url=item.get("url", ""),
            provider=provider,
            model=model,
            output=batch_root / f"item-{index:03d}",
            max_steps=max_steps,
        )
        report_path: object = ""
        reason = ""
        try:
            exit_code, report_path = cli.run(args)
            status = _status_from_exit_code(exit_code)
            steps, reason = _report_details(report_path)
        except Exception as error:
            status = "UNKNOWN"
            steps = 0
            reason = f"runner raised {type(error).__name__}"
        summary_items.append(
            {
                "scenario": item.get("scenario", ""),
                "url": _redact_url(item.get("url", "")),
                "provider": provider,
                "model": model,
                "status": status,
                "report_path": _summary_report_path(report_path, batch_root),
                "steps": steps,
                "reason": reason,
            }
        )

    counts = {status: sum(item["status"] == status for item in summary_items) for status in _STATUSES}
    summary = {"counts": counts, "items": summary_items}
    _atomic_write_json(batch_root / "batch-summary.json", summary)
    _write_csv(batch_root / "batch-summary.csv", summary_items)
    return batch_root


def _exit_code(summary: dict[str, object]) -> int:
    counts = summary.get("counts")
    if not isinstance(counts, dict):
        return 2
    if counts.get("FAIL", 0):
        return 1
    if counts.get("UNKNOWN", 0):
        return 2
    passed = counts.get("PASS", 0)
    return 0 if type(passed) is int and passed > 0 else 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a sequential batch of evidence-gated scenarios")
    parser.add_argument("batch_file", type=Path, help="JSON batch configuration")
    parser.add_argument("--output", type=Path, default=Path("runs/batch"), help="Batch output base directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        items = load_batch(args.batch_file)
        batch_root = run_batch(items, args.output)
        with (batch_root / "batch-summary.json").open("r", encoding="utf-8") as handle:
            summary = json.load(handle)
        status = _exit_code(summary)
        print(f"batch {('PASS' if status == 0 else 'FAIL' if status == 1 else 'UNKNOWN')}: summary={batch_root / 'batch-summary.json'}")
        return status
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"UNKNOWN: batch could not run ({type(error).__name__})", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
