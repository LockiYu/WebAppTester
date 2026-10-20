"""Start one local CLI run from the dashboard and read its safe progress fields."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .results import list_runs


_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_ACTION_KINDS = frozenset({"goto", "click", "fill", "select", "scroll", "drag", "stop"})


@dataclass(slots=True)
class UiJob:
    """The process handle stays in one local Streamlit session."""

    process: subprocess.Popen[bytes]
    directory: Path
    max_steps: int


@dataclass(frozen=True, slots=True)
class UiProgress:
    status: str
    phase: str
    steps: int
    max_steps: int
    last_action: str
    errors: int
    report_path: Path | None


def storage_state_options(root: Path) -> tuple[Path, ...]:
    """Offer only local auth files in immediate result subdirectories."""

    root = Path(root).resolve()
    if not root.is_dir():
        return ()
    found: list[Path] = []
    for directory in root.iterdir():
        if directory.is_dir() and not directory.is_symlink():
            candidate = directory / "auth.json"
            if candidate.is_file() and not candidate.is_symlink():
                found.append(candidate.resolve())
    return tuple(sorted(found))


def validate_request(
    *, scenario: str, url: str, provider: str, model: str,
    max_steps: int, storage_state: Path | None, root: Path,
    variant: str | None,
) -> None:
    if not isinstance(scenario, str) or not scenario.strip():
        raise ValueError("请输入测试场景。")
    if len(scenario) > 20_000:
        raise ValueError("测试场景过长。")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("应用地址必须是不含账号密码的 http(s) URL。")
    if provider not in {"ollama", "deepseek"}:
        raise ValueError("模型来源无效。")
    if not isinstance(model, str) or _MODEL_NAME.fullmatch(model) is None:
        raise ValueError("模型名称无效。")
    if provider == "deepseek" and model != "deepseek-flash":
        raise ValueError("DeepSeek 运行必须使用 deepseek-flash。")
    if provider == "deepseek" and not os.environ.get("DEEPSEEK_API_KEY"):
        raise ValueError("本机尚未配置 DEEPSEEK_API_KEY。")
    if type(max_steps) is not int or not 1 <= max_steps <= 35:
        raise ValueError("操作步数上限必须在 1 到 35 之间。")
    if variant not in {None, "B0", "B1", "B2", "B3"}:
        raise ValueError("实验配置无效。")
    if storage_state is not None:
        path = Path(storage_state).resolve()
        if path not in storage_state_options(root):
            raise ValueError("登录状态文件必须位于本地结果目录内。")


def start_ui_job(
    *, scenario: str, url: str, provider: str, model: str,
    max_steps: int, storage_state: Path | None, root: Path,
    variant: str | None = "B3",
) -> UiJob:
    """Launch the existing CLI without shell interpolation or public logs."""

    validate_request(
        scenario=scenario, url=url, provider=provider, model=model,
        max_steps=max_steps, storage_state=storage_state, root=root, variant=variant,
    )
    directory = Path(root).resolve() / "ui-runs" / datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    directory.mkdir(parents=True, exist_ok=False)
    request_path = directory / "request.json"
    request = {
        "scenario": scenario, "url": url, "provider": provider, "model": model,
        "max_steps": max_steps, "variant": variant,
        "storage_state": str(storage_state) if storage_state is not None else None,
    }
    request_descriptor = os.open(request_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(request_descriptor, "w", encoding="utf-8") as request_file:
        json.dump(request, request_file, ensure_ascii=False)
    command = [sys.executable, "-m", "webapptester.ui_worker", str(request_path)]
    descriptor = os.open(directory / "runner.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT,
            cwd=Path(__file__).resolve().parents[2], start_new_session=True,
        )
    return UiJob(process, directory, max_steps)


def read_ui_progress(job: UiJob) -> UiProgress:
    """Read only action types, counts, and validated final status."""

    steps = 0
    phase = "正在规划与连接应用"
    last_action = "尚未执行浏览器操作"
    errors = 0
    reports = list_runs(job.directory)
    for events_path in job.directory.glob("*/events.jsonl"):
        try:
            with events_path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = event.get("type")
                    if event_type == "gap_replanned":
                        phase = "Planner 补查规划"
                    elif event_type == "budget_arbitration":
                        phase = "预算仲裁"
                    elif event_type == "gap_replan_failed":
                        phase = "Planner 补查失败"
                    if event_type != "action":
                        continue
                    action = event.get("action")
                    kind = action.get("kind") if isinstance(action, dict) else None
                    if kind not in _ACTION_KINDS:
                        continue
                    phase = "浏览器操作"
                    last_action = kind
                    count = event.get("steps")
                    steps = max(steps, count) if type(count) is int and count >= 0 else steps + 1
                    if event.get("success") is False:
                        errors += 1
        except (OSError, UnicodeError):
            continue
    if reports and job.process.poll() is not None:
        report = reports[0]
        return UiProgress(
            report.status, "已结束", report.steps, job.max_steps,
            last_action, errors, report.directory / "report.json",
        )
    return UiProgress(
        "运行中" if job.process.poll() is None else "运行中断",
        phase, steps, job.max_steps, last_action, errors, None,
    )


def parse_scenario_upload(name: str, data: bytes) -> tuple[tuple[str, str], ...]:
    """Accept plain text or a JSON object/list; never read files named by JSON."""

    if len(data) > 1_000_000:
        raise ValueError("导入文件过大。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise ValueError("导入文件必须使用 UTF-8 编码。") from error
    if name.casefold().endswith(".txt"):
        if not text.strip():
            raise ValueError("导入文件没有测试场景。")
        return ((text.strip(), ""),)
    if not name.casefold().endswith(".json"):
        raise ValueError("只支持 .txt 或 .json 场景文件。")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError("JSON 场景文件格式无效。") from error
    values = raw if isinstance(raw, list) else [raw]
    if not values or len(values) > 100:
        raise ValueError("场景文件须包含 1 到 100 个案例。")
    scenarios: list[tuple[str, str]] = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("每个案例必须是 JSON 对象。")
        scenario = item.get("scenario", item.get("text"))
        url = item.get("url", "")
        if not isinstance(scenario, str) or not scenario.strip() or not isinstance(url, str):
            raise ValueError("每个案例须包含 scenario 和可选的 url。")
        scenarios.append((scenario.strip(), url.strip()))
    return tuple(scenarios)
