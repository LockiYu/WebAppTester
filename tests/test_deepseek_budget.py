from __future__ import annotations

import json
from pathlib import Path

import pytest

from webapptester.deepseek_budget import DeepSeekBudget, DeepSeekBudgetError


def _amount(path: Path) -> str:
    return json.loads(path.read_text())["reserved_or_spent_cny"]


def test_budget_reserves_before_request_then_charges_reported_usage(tmp_path: Path) -> None:
    path = tmp_path / "runs" / "deepseek-budget.json"
    budget = DeepSeekBudget(path)
    usage = [0, 0]

    def request() -> str:
        assert _amount(path) == "10.00"
        usage[:] = [1000, 100]
        return "ok"

    assert budget.call(
        model="deepseek-flash", system="system", user="user",
        usage_counts=lambda: tuple(usage), request=request,
    ) == "ok"
    assert _amount(path) == "0.01"
    assert path.stat().st_mode & 0o777 == 0o600


def test_uncertain_requests_keep_reservations_and_stop_before_limit(tmp_path: Path) -> None:
    path = tmp_path / "deepseek-budget.json"
    budget = DeepSeekBudget(path)
    calls = 0

    def broken_request() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("transport failed")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="transport failed"):
            budget.call(
                model="deepseek-flash", system="system", user="user",
                usage_counts=lambda: (0, 0), request=broken_request,
            )
    with pytest.raises(DeepSeekBudgetError, match="no safe request reserve"):
        budget.call(
            model="deepseek-flash", system="system", user="user",
            usage_counts=lambda: (0, 0), request=broken_request,
        )
    assert calls == 2
    assert _amount(path) == "20.00"


def test_budget_rejects_unpriced_model_and_oversized_prompt(tmp_path: Path) -> None:
    budget = DeepSeekBudget(tmp_path / "deepseek-budget.json")
    with pytest.raises(DeepSeekBudgetError, match="only covers"):
        budget.call(
            model="deepseek-v4-pro", system="s", user="u",
            usage_counts=lambda: (0, 0), request=lambda: None,
        )
    with pytest.raises(DeepSeekBudgetError, match="input bound"):
        budget.call(
            model="deepseek-flash", system="s", user="a" * 1_000_000,
            usage_counts=lambda: (0, 0), request=lambda: None,
        )


def test_corrupt_ledger_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "deepseek-budget.json"
    path.write_text("{broken", encoding="utf-8")
    budget = DeepSeekBudget(path)

    with pytest.raises(DeepSeekBudgetError, match="ledger is invalid"):
        budget.call(
            model="deepseek-flash", system="s", user="u",
            usage_counts=lambda: (0, 0), request=lambda: None,
        )
