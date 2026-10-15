"""Read-only Streamlit view for persisted WebAppTester runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import streamlit as st

from webapptester.results import RunRecord, artifact_path, list_runs


def _source_label(run: RunRecord) -> str:
    """Describe the source without guessing at a provider or model."""

    if run.token_usage.get("prompt", 0) == 0 and run.token_usage.get("completion", 0) == 0:
        return "scripted/local · provider metadata unavailable"
    return "provider unspecified · token usage recorded"


def _history_label(run: RunRecord) -> str:
    return f"{run.directory.name} · {run.status} · {_source_label(run)}"


def _read_artifact(root: Path, run: RunRecord, relative_path: str) -> bytes | None:
    path = artifact_path(root, run, relative_path)
    if path is None:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _redact_values(value: Any) -> Any:
    """Remove action input values before exposing persisted JSON for download."""

    if isinstance(value, dict):
        return {
            key: _redact_values(item)
            for key, item in value.items()
            if key != "value"
        }
    if isinstance(value, list):
        return [_redact_values(item) for item in value]
    return value


def _read_redacted_json(root: Path, run: RunRecord, relative_path: str) -> bytes | None:
    raw = _read_artifact(root, run, relative_path)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return (json.dumps(_redact_values(parsed), ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _read_redacted_jsonl(root: Path, run: RunRecord, relative_path: str) -> bytes | None:
    raw = _read_artifact(root, run, relative_path)
    if raw is None:
        return None
    try:
        lines = raw.decode("utf-8").splitlines()
        redacted = [json.dumps(_redact_values(json.loads(line)), ensure_ascii=False) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    suffix = "\n" if redacted else ""
    return ("\n".join(redacted) + suffix).encode("utf-8")


def _artifact_warning(label: str) -> None:
    st.warning(f"{label} unavailable for this run.")


def _render_downloads(root: Path, run: RunRecord) -> None:
    st.subheader("Downloads")
    columns = st.columns(3)
    download_specs = (
        ("report.json", "application/json", _read_redacted_json),
        ("events.jsonl", "application/x-ndjson", _read_redacted_jsonl),
    )
    for column, (name, mime, reader) in zip(columns, download_specs):
        data = reader(root, run, name)
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

    with columns[2]:
        trace = run.trace
        if trace is None:
            _artifact_warning("trace.zip")
        else:
            try:
                trace_data = trace.read_bytes()
            except OSError:
                trace_data = None
            if trace_data is None:
                _artifact_warning("trace.zip")
            else:
                st.download_button(
                    "Download trace.zip",
                    data=trace_data,
                    file_name="trace.zip",
                    mime="application/zip",
                    key=f"download-{run.directory}-trace.zip",
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


def _render_trace(run: RunRecord) -> None:
    if run.trace is None:
        st.warning("Trace unavailable for this run.")


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
    st.write(f"Reason: {run.reason}")
    st.caption(f"Source: {_source_label(run)}")

    _render_evidence(run)
    _render_actions(run)
    _render_screenshots(root, run)
    _render_trace(run)
    _render_downloads(root, run)


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
