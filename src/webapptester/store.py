"""Append-only run evidence and atomic report persistence."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .schema import ACTION_KINDS, Observation


class RunStore:
    """Persist observations, screenshots, events, and a final run report."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.screenshots = self.root / "screenshots"
        self.root.mkdir(parents=True, exist_ok=True)
        self.screenshots.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"
        self._next_observation_number = self._find_next_observation_number()

    def append_event(self, event: dict[str, Any]) -> None:
        """Append one JSON event without ever writing an action's input value."""

        safe_event = _sanitize_event(event)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe_event, ensure_ascii=False))
            handle.write("\n")

    def save_observation(self, url: str, text: str, screenshot: bytes) -> Observation:
        number = self._next_observation_number
        self._next_observation_number += 1
        observation_id = f"o{number}"
        screenshot_path = self.screenshots / f"observation-{number:04d}.png"
        screenshot_path.write_bytes(screenshot)
        fingerprint = hashlib.sha256((url + text).encode("utf-8")).hexdigest()
        observation = Observation(
            id=observation_id,
            url=url,
            text=text,
            screenshot=str(screenshot_path),
            fingerprint=fingerprint,
        )
        event = asdict(observation)
        event["type"] = "observation"
        self.append_event(event)
        return observation

    def write_report(self, report: dict[str, Any]) -> Path:
        """Atomically replace report.json with the supplied JSON report."""

        report_path = self.root / "report.json"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.root,
                prefix=".report-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(report, handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(report_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return report_path

    def _find_next_observation_number(self) -> int:
        numbers: list[int] = []
        for path in self.screenshots.glob("observation-*.png"):
            try:
                numbers.append(int(path.stem.removeprefix("observation-")))
            except ValueError:
                continue
        if self.events_path.exists():
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                observation_id = event.get("id")
                if isinstance(observation_id, str) and observation_id.startswith("o"):
                    try:
                        numbers.append(int(observation_id.removeprefix("o")))
                    except ValueError:
                        continue
        return max(numbers, default=0) + 1


def _sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Remove secret-bearing values from nested or flat action events."""

    if not isinstance(event, dict):
        raise TypeError("event must be a dictionary")
    is_flat_action = event.get("kind") in ACTION_KINDS
    sanitized: dict[str, Any] = {}
    for key, value in event.items():
        if is_flat_action and key == "value":
            continue
        if key == "action" and isinstance(value, dict):
            sanitized[key] = {
                nested_key: nested_value
                for nested_key, nested_value in value.items()
                if nested_key != "value"
            }
        else:
            sanitized[key] = value
    return sanitized


__all__ = ["RunStore"]
