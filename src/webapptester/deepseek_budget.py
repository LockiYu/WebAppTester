"""Conservative, shared CNY reservation for optional DeepSeek Flash calls.

The estimate uses the peak, cache-miss Flash prices published by DeepSeek on
2026-10-19 ($0.30/M input and $1.20/M output) and a deliberately high
10 CNY/USD safety factor. Recheck pricing before a paid evaluation. A 10 CNY
reservation is written before every request so a crash cannot silently erase
possible spending. The file records an upper estimate, not an invoice.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from collections.abc import Callable
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import TypeVar


T = TypeVar("T")
LIMIT_CNY = Decimal("20.00")
RESERVATION_CNY = Decimal("10.00")
MAX_PROMPT_BYTES = 1_000_000
INPUT_USD_PER_MILLION = Decimal("0.30")
OUTPUT_USD_PER_MILLION = Decimal("1.20")
CNY_PER_USD_SAFETY_FACTOR = Decimal("10")


class DeepSeekBudgetError(RuntimeError):
    """A paid call was refused because its budget cannot be established."""


class DeepSeekBudget:
    """Serialize paid requests and persist conservative spending across runs."""

    def __init__(self, ledger_path: Path) -> None:
        self.ledger_path = Path(ledger_path)
        self.lock_path = self.ledger_path.with_suffix(".lock")

    def call(
        self,
        *,
        model: str,
        system: str,
        user: str,
        usage_counts: Callable[[], tuple[int, int]],
        request: Callable[[], T],
    ) -> T:
        if model != "deepseek-flash":
            raise DeepSeekBudgetError("the paid budget only covers deepseek-flash")
        if len(system.encode("utf-8")) + len(user.encode("utf-8")) + 1024 > MAX_PROMPT_BYTES:
            raise DeepSeekBudgetError("prompt exceeds the paid budget input bound")
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.lock_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            spent = self._read_spent()
            if spent + RESERVATION_CNY > LIMIT_CNY:
                raise DeepSeekBudgetError("DeepSeek CNY budget has no safe request reserve")
            self._write_spent(spent + RESERVATION_CNY)
            before_prompt, before_completion = usage_counts()
            try:
                return request()
            finally:
                after_prompt, after_completion = usage_counts()
                prompt = after_prompt - before_prompt
                completion = after_completion - before_completion
                charge = _conservative_charge(prompt, completion)
                # Missing usage, including a transport failure, retains the
                # full reservation until it can be reconciled manually.
                self._write_spent(spent + (charge if charge is not None else RESERVATION_CNY))
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_spent(self) -> Decimal:
        path = self.ledger_path
        if not path.exists() and not path.is_symlink():
            return Decimal("0.00")
        if not stat.S_ISREG(path.lstat().st_mode):
            raise DeepSeekBudgetError("DeepSeek budget ledger is not a regular file")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("version") != 1 or value.get("limit_cny") != "20.00":
                raise ValueError("unsupported ledger version or limit")
            spent = Decimal(value["reserved_or_spent_cny"])
            if not spent.is_finite() or spent < 0 or spent > LIMIT_CNY:
                raise ValueError("invalid ledger amount")
            return spent
        except (OSError, ValueError, TypeError, KeyError, AttributeError, InvalidOperation) as error:
            raise DeepSeekBudgetError("DeepSeek budget ledger is invalid") from error

    def _write_spent(self, spent: Decimal) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".deepseek-budget-", dir=self.ledger_path.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({
                    "version": 1,
                    "limit_cny": "20.00",
                    "reserved_or_spent_cny": str(spent.quantize(Decimal("0.01"))),
                    "pricing_basis": "DeepSeek Flash peak cache-miss 2026-10-19; 10 CNY/USD safety factor",
                }, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.ledger_path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _conservative_charge(prompt: int, completion: int) -> Decimal | None:
    if prompt < 0 or completion < 0 or prompt + completion == 0:
        return None
    usd = (
        Decimal(prompt) * INPUT_USD_PER_MILLION
        + Decimal(completion) * OUTPUT_USD_PER_MILLION
    ) / Decimal(1_000_000)
    return max(
        Decimal("0.01"),
        (usd * CNY_PER_USD_SAFETY_FACTOR).quantize(Decimal("0.01"), rounding=ROUND_CEILING),
    )
