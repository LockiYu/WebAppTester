"""Read-only Streamlit view for persisted WebAppTester runs."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import streamlit as st

from webapptester.results import RunRecord, artifact_path, list_runs


_VARIANTS = ("B0", "B1", "B2", "B3")


@dataclass(frozen=True)
class ComparisonGroup:
    """One saved B0–B3 experiment and its matching, validated reports."""

    summary_path: Path
    scenario_ids: tuple[str, ...]
    scenario_texts: dict[str, str]
    reports: dict[tuple[str, str], RunRecord]


def _read_small_json(path: Path, root: Path) -> object | None:
    try:
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()) or resolved.stat().st_size > 2_000_000:
            return None
        return json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None


def _scenario_texts(summary_path: Path, root: Path) -> dict[str, str]:
    """Read only the scenario text from a nearby manifest, never auth paths."""

    candidates = (
        summary_path.parent.parent / "manifest.json",
        summary_path.parent / "manifest.json",
    )
    for path in candidates:
        data = _read_small_json(path, root)
        if isinstance(data, list):
            return {
                item["id"]: item["scenario"]
                for item in data
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("scenario"), str)
            }
    return {}


def _comparison_groups(root: Path, runs: tuple[RunRecord, ...]) -> tuple[ComparisonGroup, ...]:
    root = root.resolve()
    groups: list[ComparisonGroup] = []
    for summary_path in root.rglob("ablation-summary.json"):
        summary = _read_small_json(summary_path, root)
        if not isinstance(summary, dict) or not isinstance(summary.get("outcomes"), list):
            continue
        scenario_ids: list[str] = []
        for outcome in summary["outcomes"]:
            if not isinstance(outcome, dict):
                continue
            scenario_id = outcome.get("scenario_id")
            if (
                isinstance(scenario_id, str)
                and scenario_id
                and outcome.get("variant") in _VARIANTS
                and scenario_id not in scenario_ids
            ):
                scenario_ids.append(scenario_id)
        if not scenario_ids:
            continue
        reports: dict[tuple[str, str], RunRecord] = {}
        for run in runs:
            try:
                relative = run.directory.resolve().relative_to(summary_path.parent.resolve())
            except ValueError:
                continue
            if (
                len(relative.parts) == 3
                and relative.parts[0] in _VARIANTS
                and relative.parts[1] in scenario_ids
            ):
                reports.setdefault((relative.parts[1], relative.parts[0]), run)
        groups.append(
            ComparisonGroup(
                summary_path,
                tuple(scenario_ids),
                _scenario_texts(summary_path, root),
                reports,
            )
        )
    groups.sort(key=lambda group: group.summary_path.stat().st_mtime_ns, reverse=True)
    return tuple(groups)


def _goal_lines(run: RunRecord | None) -> tuple[str, ...]:
    if run is None:
        return ()
    goals: list[str] = []
    for checkpoint in run.checkpoints:
        if not checkpoint.get("required", True):
            continue
        target = checkpoint.get("target")
        scope = checkpoint.get("scope")
        if isinstance(target, str) and target:
            location = {
                "dashboard": "仪表盘",
                "sidebar": "侧栏",
                "heading": "页面标题",
            }.get(scope, "目标页面")
            goal = f"在{location}确认「{target}」"
        else:
            goal = str(checkpoint.get("expected", "未记录验收目标"))
        if goal not in goals:
            goals.append(goal)
    return tuple(goals)


def _case_goals(group: ComparisonGroup, scenario_id: str) -> tuple[str, ...]:
    for variant in reversed(_VARIANTS):
        goals = _goal_lines(group.reports.get((scenario_id, variant)))
        if goals:
            return goals
    return ()


def _application_name(scenario_id: str) -> str:
    return {
        "4gaboards": "4gaBoards",
        "admidio": "Admidio",
        "discourse": "Discourse",
    }.get(scenario_id.split("-", 1)[0], "目标应用")


def _case_description(group: ComparisonGroup, scenario_id: str) -> str:
    goals = _case_goals(group, scenario_id)
    if not goals:
        return "尚未记录明确检查点，请查看测试指令原文。"
    return f"在 {_application_name(scenario_id)} 中，" + "，并".join(goals) + "。"


def _status_explanation(status: str) -> str:
    return {
        "PASS": "运行报告判定通过",
        "FAIL": "运行报告判定存在不一致",
        "UNKNOWN": "证据不足或执行未完成",
        "UNAVAILABLE": "报告损坏或不完整",
    }.get(status, "没有可用报告")


def _render_comparison(groups: tuple[ComparisonGroup, ...]) -> None:
    st.subheader("场景与方案对比")
    if not groups:
        st.info("暂无可对比的 B0–B3 实验；仍可在下方查看单次运行记录。")
        return
    group = st.selectbox(
        "选择实验批次",
        groups,
        format_func=lambda item: str(item.summary_path.parent.parent.name)
        + f" · {item.summary_path.parent.parent.parent.name}",
    )
    st.caption(
        "B0 基础方案 · B1 加入证据契约 · B2 加入证据缺口反馈 · B3 加入预算仲裁。"
        "同一批次、同一场景下比较；这些是本地试跑结果，不是课程正式成绩。"
    )
    st.caption(f"本批次包含 {len(group.scenario_ids)} 个场景、{len(group.reports)} 份逐场景报告。")
    rows = []
    for scenario_id in group.scenario_ids:
        row = {
            "案例": scenario_id,
            "应用": _application_name(scenario_id),
            "在测什么（中文）": _case_description(group, scenario_id),
        }
        for variant in _VARIANTS:
            run = group.reports.get((scenario_id, variant))
            row[variant] = run.status if run else "无报告"
        rows.append(row)
    st.dataframe(rows, width="stretch", hide_index=True)

    scenario_id = st.selectbox("选择要详细对比的案例", group.scenario_ids)
    st.markdown("**本案例在测什么**")
    st.write(_case_description(group, scenario_id))
    st.markdown("**测试指令（原文）**")
    st.write(group.scenario_texts.get(scenario_id, "场景原文未随结果保存"))
    goals = _case_goals(group, scenario_id)
    st.markdown("**检查点摘要（供理解验收目标）**")
    st.write("；".join(goals) if goals else "未记录检查点")
    st.caption("检查点由智能体在运行时生成，用于理解测试目标；它不是课程标准答案。")
    status_columns = st.columns(4)
    for column, variant in zip(status_columns, _VARIANTS):
        run = group.reports.get((scenario_id, variant))
        with column:
            st.metric(variant, run.status if run else "无报告")
    comparison_rows = []
    for variant in _VARIANTS:
        run = group.reports.get((scenario_id, variant))
        comparison_rows.append({
            "方案": variant,
            "结论": run.status if run else "无报告",
            "操作步数": run.steps if run else None,
            "耗时（秒）": round(run.elapsed_seconds, 2) if run else None,
            "输入 Token": run.token_usage.get("prompt", 0) if run else None,
            "输出 Token": run.token_usage.get("completion", 0) if run else None,
            "有证据的检查点": len(run.evidence) if run else None,
            "必需检查点": sum(bool(item.get("required", True)) for item in run.checkpoints)
            if run else None,
            "状态说明": _status_explanation(run.status) if run else "没有可用报告",
        })
    st.dataframe(comparison_rows, width="stretch", hide_index=True)
    chart_rows = [row for row in comparison_rows if row["耗时（秒）"] is not None]
    if chart_rows:
        st.bar_chart(chart_rows, x="方案", y="耗时（秒）")
    st.caption("PASS 表示该次运行报告通过；UNKNOWN 表示证据不足或执行未完成。请到下方运行历史查看证据。")


def _run_contexts(groups: tuple[ComparisonGroup, ...]) -> dict[Path, tuple[str, str, str]]:
    contexts: dict[Path, tuple[str, str, str]] = {}
    for group in groups:
        for (scenario_id, variant), run in group.reports.items():
            contexts[run.directory] = (
                scenario_id,
                variant,
                group.scenario_texts.get(scenario_id, "场景原文未随结果保存"),
            )
    return contexts


def _source_label(run: RunRecord) -> str:
    """Describe source metadata without guessing at a provider or model."""

    return "来源未知 · 缺少模型提供方信息"


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
    fields = ("id", "source", "expected", "required", "scope", "target")
    return {
        key: item[key]
        for key in fields
        if key in item and (key != "target" or item[key])
    }


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
    st.warning(f"本次运行的 {label} 不可用。")


def _render_downloads(root: Path, run: RunRecord, *, reveal_raw: bool) -> None:
    if not reveal_raw:
        return
    st.subheader("下载文件")
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
                    f"下载 {name}",
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
            "本地 Playwright Trace 可能包含 Cookie 等会话凭据，因此看板不提供下载。"
            f"如需查看，请在本机打开 `{trace}`。"
        )


def _render_evidence(run: RunRecord) -> None:
    st.subheader("检查点证据")
    if not run.evidence:
        st.info("本次运行没有记录检查点证据。")
        return
    observation_by_id = {
        observation.get("id"): observation for observation in run.observations
    }
    for item in run.evidence:
        status = item["status"]
        checkpoint = item["checkpoint_id"]
        with st.expander(f"{checkpoint} · {status}"):
            st.write(f"观察记录 ID：{item['observation_id']}")
            st.write(f"证据原文：{item['quote']}")
            observation = observation_by_id.get(item["observation_id"])
            if observation is None:
                st.warning("对应的观察记录不可用。")


def _render_actions(run: RunRecord) -> None:
    st.subheader("操作时间线")
    if not run.actions:
        st.info("本次运行没有记录操作。")
        return
    for index, action in enumerate(run.actions, start=1):
        kind = action.get("kind", "未知")
        locator = action.get("locator", "")
        checkpoint = action.get("checkpoint_id", "")
        suffix = f" · 检查点 {checkpoint}" if checkpoint else ""
        st.markdown(f"**{index}. {kind}** · `{locator}`{suffix}")


def _render_screenshots(root: Path, run: RunRecord) -> None:
    st.subheader("执行前后页面对比")
    if not run.observations:
        st.info("本次运行没有记录页面观察结果。")
        return
    if len(run.observations) < 2:
        st.info("本次运行只有一次页面观察，无法形成前后截图对比。")
    else:
        for column, label, observation in zip(
            st.columns(2),
            ("执行前", "执行后"),
            (run.observations[0], run.observations[-1]),
        ):
            with column:
                st.markdown(f"**{label}**")
                _render_observation_screenshot(root, run, observation)
    st.subheader("全部观察截图")
    for observation in run.observations:
        _render_observation_screenshot(root, run, observation)


def _render_observation_screenshot(
    root: Path, run: RunRecord, observation: dict[str, object]
) -> None:
    observation_id = observation.get("id", "未知")
    screenshot_text = observation.get("screenshot")
    if not isinstance(screenshot_text, str):
        st.warning(f"观察记录 {observation_id} 的截图不可用。")
        return
    screenshot = artifact_path(root, run, screenshot_text)
    if screenshot is None:
        st.warning(f"观察记录 {observation_id} 的截图不可用。")
        return
    try:
        st.image(str(screenshot), caption=f"观察记录 {observation_id}")
    except (OSError, RuntimeError, ValueError):
        st.warning(f"观察记录 {observation_id} 的截图不可用。")


def _render_selected_run(
    root: Path,
    run: RunRecord,
    context: tuple[str, str, str] | None = None,
) -> None:
    st.subheader(f"运行记录：{run.directory.name}")
    if context is not None:
        scenario_id, variant, scenario_text = context
        st.caption(f"案例：{scenario_id} · 方案：{variant}")
        st.markdown("**测试指令（原文）**")
        st.write(scenario_text)
    else:
        st.info("这条旧记录没有保存完整的场景原文；可根据下方检查点了解验收目标。")
    goals = _goal_lines(run)
    st.markdown("**验收目标（运行时检查点）**")
    st.write("；".join(goals) if goals else "未记录检查点")
    metrics = st.columns(5)
    with metrics[0]:
        st.metric("状态", run.status)
    with metrics[1]:
        st.metric("操作步数", run.steps)
    with metrics[2]:
        st.metric("耗时（秒）", f"{run.elapsed_seconds:.2f}")
    with metrics[3]:
        st.metric("输入 Token", run.token_usage.get("prompt", 0))
    with metrics[4]:
        st.metric("输出 Token", run.token_usage.get("completion", 0))
    st.caption(f"来源：{_source_label(run)}")
    if run.status == "UNAVAILABLE":
        st.warning("本次运行的报告已损坏或不完整，无法查看详情。")
        return
    reveal_raw = st.checkbox(
        "显示页面证据和下载文件",
        help="页面文字、URL、截图、操作定位信息、证据原文和 Trace 可能包含敏感数据。",
        key=f"reveal-{run.directory}",
    )
    if reveal_raw:
        st.warning(
            "页面证据和下载文件可能包含敏感内容。"
        )
        st.write(f"判定原因：{run.reason}")
        _render_evidence(run)
        _render_actions(run)
        _render_screenshots(root, run)
        _render_downloads(root, run, reveal_raw=True)
    else:
        st.info("页面证据和下载文件已隐藏；勾选上方选项后查看。")


def render(root: Path) -> None:
    """Render the read-only results dashboard for *root*."""

    root = Path(root)
    st.set_page_config(page_title="WebAppTester 测试结果", page_icon="🧪", layout="wide")
    st.title("WebAppTester 测试结果")
    st.caption(f"只读运行记录 · 结果目录：{root}")
    runs = list_runs(root)
    if not runs:
        st.info("结果目录中没有找到可查看的运行记录。")
        return

    groups = _comparison_groups(root, runs)
    _render_comparison(groups)
    contexts = _run_contexts(groups)
    st.divider()
    st.subheader("运行历史")
    history_rows: list[dict[str, Any]] = [
        {
            "运行记录": run.directory.name,
            "案例": contexts[run.directory][0] if run.directory in contexts else "未记录",
            "方案": contexts[run.directory][1] if run.directory in contexts else "单次运行",
            "检查点摘要": "；".join(_goal_lines(run)) or "未记录检查点",
            "状态": run.status,
            "来源": _source_label(run),
            "操作步数": run.steps,
            "耗时（秒）": round(run.elapsed_seconds, 2),
            "输入 Token": run.token_usage.get("prompt", 0),
            "输出 Token": run.token_usage.get("completion", 0),
        }
        for run in runs
    ]
    st.dataframe(history_rows, width="stretch", hide_index=True)
    selected_index = st.selectbox(
        "选择运行记录",
        options=list(range(len(runs))),
        format_func=lambda index: _history_label(runs[index]),
    )
    _render_selected_run(root, runs[selected_index], contexts.get(runs[selected_index].directory))


def _main() -> None:
    parser = argparse.ArgumentParser(description="View WebAppTester run results")
    parser.add_argument("--runs-root", type=Path, default=Path("runs"))
    # AppTest executes the file in the pytest process, whose argv contains
    # pytest's own flags.  Ignore those while accepting only our one option.
    args, _unknown = parser.parse_known_args()
    render(args.runs_root)


if __name__ == "__main__":
    _main()
