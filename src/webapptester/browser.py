"""The only gateway through which the tester may interact with a browser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .schema import Action, Observation

if TYPE_CHECKING:
    from .store import RunStore


DEFAULT_ACTION_TIMEOUT_MS = 5_000
MAX_STEPS = 35


class BudgetExhausted(RuntimeError):
    """Raised before a browser call when the per-run action budget is spent."""


@dataclass(frozen=True, slots=True)
class ActionResult:
    action: Action
    success: bool
    error: str
    steps: int


class BrowserGateway:
    """Execute a strictly bounded set of Playwright actions on one page."""

    def __init__(self, page: Page, max_steps: int) -> None:
        if type(max_steps) is not int or not 1 <= max_steps <= MAX_STEPS:
            raise ValueError(f"max_steps must be between 1 and {MAX_STEPS}")
        self.page = page
        self.max_steps = max_steps
        self.steps = 0
        # A page-wide default keeps observation and all supported actions within
        # the same five-second Playwright timeout. Test doubles may omit it.
        set_default_timeout = getattr(page, "set_default_timeout", None)
        if set_default_timeout is not None:
            set_default_timeout(DEFAULT_ACTION_TIMEOUT_MS)
        set_default_navigation_timeout = getattr(page, "set_default_navigation_timeout", None)
        if set_default_navigation_timeout is not None:
            set_default_navigation_timeout(DEFAULT_ACTION_TIMEOUT_MS)

    def act(self, action: Action) -> ActionResult:
        """Execute one action, counting failed browser attempts as steps."""

        if action.kind == "stop":
            return ActionResult(action=action, success=True, error="", steps=self.steps)
        if self.steps >= self.max_steps:
            raise BudgetExhausted(
                f"browser action budget exhausted ({self.max_steps} steps)"
            )

        # Reserve the step before dispatch so a timeout or any other browser
        # failure cannot be retried for free.
        self.steps += 1
        try:
            self._dispatch(action)
        except (PlaywrightError, PlaywrightTimeoutError, TimeoutError) as error:
            # Playwright errors (including errors from test doubles) are
            # represented as an unsuccessful action result. They are not a
            # product verdict and must not escape as a test FAIL.
            return ActionResult(
                action=action,
                success=False,
                error=f"{type(error).__name__}: {error}",
                steps=self.steps,
            )
        return ActionResult(action=action, success=True, error="", steps=self.steps)

    def observe(self, store: RunStore) -> Observation:
        """Capture visible page state and persist it as append-only evidence."""

        url = self.page.url
        text = self.page.locator("body").inner_text()[:20_000]
        screenshot = self.page.screenshot(full_page=True)
        return store.save_observation(url=url, text=text, screenshot=screenshot)

    def _dispatch(self, action: Action) -> None:
        if action.kind == "goto":
            destination = action.value or action.locator
            if not destination:
                raise ValueError("goto requires a URL in value or locator")
            self.page.goto(destination)
            return
        if action.kind == "click":
            self._locator(action.locator).click()
            return
        if action.kind == "fill":
            self._locator(action.locator).fill(action.value)
            return
        if action.kind == "select":
            self._locator(action.locator).select_option(action.value)
            return
        if action.kind == "scroll":
            x, y = _scroll_delta(action.value or action.locator)
            self.page.mouse.wheel(x, y)
            return
        if action.kind == "drag":
            source, destination = action.locator.split(" => ", 1)
            self._locator(source.strip()).drag_to(self._locator(destination.strip()))
            return
        raise ValueError(f"unsupported action kind: {action.kind!r}")

    def _locator(self, locator: str) -> Any:
        if locator.startswith("role="):
            role, name = _role_locator(locator)
            return self.page.get_by_role(role, name=name)
        return self.page.locator(locator)


_ROLE_LOCATOR = re.compile(r"^role=([^;\s]+);name=(.+)$")
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_SCROLL_PAIR = re.compile(rf"^\s*({_NUMBER})\s*(?:,|\s+)\s*({_NUMBER})\s*$")
_SCROLL_SINGLE = re.compile(rf"^\s*({_NUMBER})\s*$")


def _role_locator(locator: str) -> tuple[str, str]:
    match = _ROLE_LOCATOR.fullmatch(locator)
    if match is None:
        raise ValueError("role locator must use role=<role>;name=<name>")
    return match.group(1), match.group(2)


def _number(value: str) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def _scroll_delta(spec: str) -> tuple[int | float, int | float]:
    pair = _SCROLL_PAIR.fullmatch(spec)
    if pair is not None:
        return _number(pair.group(1)), _number(pair.group(2))
    single = _SCROLL_SINGLE.fullmatch(spec)
    if single is not None:
        return 0, _number(single.group(1))
    raise ValueError("scroll value must be a y amount or an x,y pair")


__all__ = ["ActionResult", "BrowserGateway", "BudgetExhausted"]
