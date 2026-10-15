"""Read-only Streamlit view for persisted WebAppTester runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import streamlit as st

from webapptester.results import RunRecord, artifact_path, list_runs


def _source_label(run: RunRecord) -> str:
    """Describe source metadata without guessing at a provider or model."""

    return "source unknown · provider metadata unavailable"


def _history_label(run: RunRecord) -> str:
    return f"{run.directory.name} · {run.status} · {_source_label(run)}"


def _safe_action(action: dict[str, str]) -> dict[str, str]:
    safe = {key: action[key] for key in ("kind", "locator", "checkpoint_id") if key in action}
    if "locator" in safe:
        safe["locator"] = _safe_locator(safe["locator"])
    return safe


def _safe_locator(locator: str) -> str:
    """Avoid copying credential-shaped locator text into an export."""

    normalized = "".join(character for character in locator.lower() if character.isalnum())
    sensitive = ("password", "passwd", "token", "secret", "apikey", "credential", "authorization")
    if any(term in normalized for term in sensitive):
        return "[REDACTED LOCATOR]"
    return locator


def _safe_observation(observation: dict[str, object]) -> dict[str, object]:
    fields = ("id", "url", "text", "screenshot", "fingerprint")
    safe = {key: observation[key] for key in fields if key in observation}
    regions = observation.get("regions", [])
    if isinstance(regions, list):
        safe["regions"] = [
            {
                key: region[key]
                for key in ("id", "text", "locator")
                if isinstance(region, dict) and key in region and isinstance(region[key], str)
            }
            for region in regions
            if isinstance(region, dict)
            and all(isinstance(region.get(key), str) for key in ("id", "text", "locator"))
        ]
    return safe


def _safe_evidence(item: dict[str, str]) -> dict[str, str]:
    fields = ("checkpoint_id", "observation_id", "quote", "status", "region_id")
    return {key: item[key] for key in fields}


def _safe_checkpoint(item: dict[str, object]) -> dict[str, object]:
    fields = ("id", "source", "expected", "required", "scope")
    return {key: item[key] for key in fields if key in item}


def _trace_artifact(root: Path, run: RunRecord) -> Path | None:
    """Revalidate the trace path immediately before exposing or reading it."""

    if run.trace is None:
        return None
    return artifact_path(root, run, str(run.trace))


def _report_export(root: Path, run: RunRecord) -> bytes | None:
    if artifact_path(root, run, "report.json") is None:
        return None
    trace = _trace_artifact(root, run)
    payload: dict[str, Any] = {
        "steps": run.steps,
        "elapsed_seconds": run.elapsed_seconds,
        "token_usage": {
            "prompt": run.token_usage.get("prompt", 0),
            "completion": run.token_usage.get("completion", 0),
        },
        "trace_path": "trace.zip" if trace is not None else None,
        "checkpoints": [_safe_checkpoint(item) for item in run.checkpoints],
        "actions": [_safe_action(action) for action in run.actions],
        "observations": [_safe_observation(item) for item in run.observations],
        "verdict": {
            "status": run.status,
            "reason": run.reason,
            "evidence": [_safe_evidence(item) for item in run.evidence],
        },
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _events_export(root: Path, run: RunRecord) -> bytes | None:
    events_path = artifact_path(root, run, "events.jsonl")
    if events_path is None:
        return None
    events: list[dict[str, Any]] = []
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return None
    for line in lines:
        try:
            raw = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        event = _safe_event(raw)
        if event is not None:
            events.append(event)
    if not events:
        return b""
    return ("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n").encode(
        "utf-8"
    )


def _safe_event(raw: object) -> dict[str, Any] | None:
    """Keep the original event order while exporting a strict safe subset."""

    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        return None
    event_type = raw["type"]
    if event_type == "action":
        action = raw.get("action")
        if not isinstance(action, dict):
            return None
        safe_action: dict[str, str] = {}
        for key in ("kind", "locator", "checkpoint_id"):
            value = action.get(key)
            if value is not None:
                if not isinstance(value, str):
                    return None
                safe_action[key] = _safe_locator(value) if key == "locator" else value
        if safe_action.get("kind") not in {"goto", "click", "fill", "select", "scroll", "drag", "stop"}:
            return None
        safe: dict[str, Any] = {"type": "action", "action": safe_action}
        success = raw.get("success")
        if success is not None:
            if type(success) is not bool:
                return None
            safe["success"] = success
        steps = raw.get("steps")
        if steps is not None:
            if type(steps) is not int or steps < 0:
                return None
            safe["steps"] = steps
        error_class = raw.get("error_class")
        if error_class is not None:
            if (
                not isinstance(error_class, str)
                or not error_class.isidentifier()
                or not error_class.endswith(("Error", "Exception"))
            ):
                return None
            safe["error_class"] = error_class
        return safe
    if event_type == "observation":
        safe_observation: dict[str, Any] = {"type": "observation"}
        for key in ("id", "url", "text", "screenshot", "fingerprint"):
            value = raw.get(key)
            if value is not None:
                if not isinstance(value, str):
                    return None
                safe_observation[key] = value
        regions = raw.get("regions")
        if regions is not None:
            if not isinstance(regions, list):
                return None
            safe_regions: list[dict[str, str]] = []
            for region in regions:
                if not isinstance(region, dict) or any(
                    not isinstance(region.get(key), str) for key in ("id", "text", "locator")
                ):
                    return None
                safe_regions.append(
                    {key: region[key] for key in ("id", "text", "locator")}
                )
            safe_observation["regions"] = safe_regions
        return safe_observation
    return None


def _artifact_warning(label: str) -> None:
    st.warning(f"{label} unavailable for this run.")


def _render_downloads(root: Path, run: RunRecord, *, reveal_raw: bool) -> None:
    if not reveal_raw:
        return
    st.subheader("Downloads")
    columns = st.columns(2)
    download_specs = (
        ("report.json", "application/json", _report_export),
        ("events.jsonl", "application/x-ndjson", _events_export),
    )
    for column, (name, mime, reader) in zip(columns, download_specs):
        data = reader(root, run)
        with column:
            if data is None:
                _artifact_warning(name)
            else:
                st.download_button(
                    f"Download {name}",
                    data=data,
                    file_name=name,
                    mime=mime,
                    key=f"download-{run.directory}-{name}",
                )

    trace = _trace_artifact(root, run)
    if trace is None:
        _artifact_warning("trace.zip")
    else:
        st.info(
            "The local Playwright trace may contain session secrets, including "
            "cookies. It is never available as a dashboard download; inspect "
            f"the local file manually at `{trace}`."
        )


def _render_evidence(run: RunRecord) -> None:
    st.subheader("Checkpoint evidence")
    if not run.evidence:
        st.info("No checkpoint evidence was recorded.")
        return
    observation_by_id = {
        observation.get("id"): observation for observation in run.observations
    }
    for item in run.evidence:
        status = item["status"]
        checkpoint = item["checkpoint_id"]
        with st.expander(f"{checkpoint} · {status}"):
            st.write(f"Observation ID: {item['observation_id']}")
            st.write(f"Quote: {item['quote']}")
            observation = observation_by_id.get(item["observation_id"])
            if observation is None:
                st.warning("Source observation unavailable.")


def _render_actions(run: RunRecord) -> None:
    st.subheader("Action timeline")
    if not run.actions:
        st.info("No actions were recorded.")
        return
    for index, action in enumerate(run.actions, start=1):
        kind = action.get("kind", "unknown")
        locator = action.get("locator", "")
        checkpoint = action.get("checkpoint_id", "")
        suffix = f" · checkpoint {checkpoint}" if checkpoint else ""
        st.markdown(f"**{index}. {kind}** · `{locator}`{suffix}")


def _render_screenshots(root: Path, run: RunRecord) -> None:
    st.subheader("Screenshots")
    if not run.observations:
        st.info("No observations were recorded.")
        return
    for observation in run.observations:
        observation_id = observation.get("id", "unknown")
        screenshot_text = observation.get("screenshot")
        if not isinstance(screenshot_text, str):
            st.warning(f"Screenshot for {observation_id} unavailable.")
            continue
        screenshot = artifact_path(root, run, screenshot_text)
        if screenshot is None:
            st.warning(f"Screenshot for {observation_id} unavailable.")
            continue
        try:
            st.image(str(screenshot), caption=f"Observation {observation_id}")
        except (OSError, RuntimeError, ValueError):
            st.warning(f"Screenshot for {observation_id} unavailable.")


def _render_selected_run(root: Path, run: RunRecord) -> None:
    st.subheader(f"Run: {run.directory.name}")
    metrics = st.columns(5)
    with metrics[0]:
        st.metric("Status", run.status)
    with metrics[1]:
        st.metric("Steps", run.steps)
    with metrics[2]:
        st.metric("Elapsed seconds", f"{run.elapsed_seconds:.2f}")
    with metrics[3]:
        st.metric("Prompt tokens", run.token_usage.get("prompt", 0))
    with metrics[4]:
        st.metric("Completion tokens", run.token_usage.get("completion", 0))
    st.caption(f"Source: {_source_label(run)}")
    if run.status == "UNAVAILABLE":
        st.warning("This run is unavailable because its persisted report is corrupt or incomplete.")
        return
    reveal_raw = st.checkbox(
        "Reveal page-derived evidence and downloads",
        help="Page text, URLs, screenshots, action locators, evidence quotes, and traces may contain sensitive data.",
        key=f"reveal-{run.directory}",
    )
    if reveal_raw:
        st.warning(
            "Page-derived evidence and downloads may contain sensitive page content."
        )
        st.write(f"Reason: {run.reason}")
        _render_evidence(run)
        _render_actions(run)
        _render_screenshots(root, run)
        _render_downloads(root, run, reveal_raw=True)
    else:
        st.info("Page-derived evidence and downloads are hidden until you reveal them.")


def render(root: Path) -> None:
    """Render the read-only results dashboard for *root*."""

    root = Path(root)
    st.set_page_config(page_title="WebAppTester results", page_icon="🧪", layout="wide")
    st.title("WebAppTester results")
    st.caption(f"Read-only run history · runs root: {root}")
    runs = list_runs(root)
    if not runs:
        st.info("No valid runs found in the configured runs root.")
        return

    st.subheader("Run history")
    history_rows: list[dict[str, Any]] = [
        {
            "Run": run.directory.name,
            "Status": run.status,
            "Source": _source_label(run),
            "Steps": run.steps,
            "Elapsed seconds": round(run.elapsed_seconds, 2),
            "Prompt tokens": run.token_usage.get("prompt", 0),
            "Completion tokens": run.token_usage.get("completion", 0),
        }
        for run in runs
    ]
    st.dataframe(history_rows, width="stretch", hide_index=True)
    selected_index = st.selectbox(
        "Select a run",
        options=list(range(len(runs))),
        format_func=lambda index: _history_label(runs[index]),
    )
    _render_selected_run(root, runs[selected_index])


def _main() -> None:
    parser = argparse.ArgumentParser(description="View WebAppTester run results")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    # AppTest executes the file in the pytest process, whose argv contains
    # pytest's own flags.  Ignore those while accepting only our one option.
    args, _unknown = parser.parse_known_args()
    render(args.runs_root)


if __name__ == "__main__":
    _main()
