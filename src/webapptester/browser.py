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


class AmbiguousLocatorError(RuntimeError):
    """Raised when a role locator would act on more than one element."""


class InvalidRefError(ValueError):
    """Raised when an action refers to a missing or malformed AI snapshot ref."""


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
        # AI snapshot refs are intentionally scoped to the most recent
        # observation.  Keep only the sanitized snapshot and its ref names;
        # the raw Playwright response is never retained.
        self.latest_snapshot = ""
        self._latest_refs: frozenset[str] = frozenset()
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
        except (
            PlaywrightError,
            PlaywrightTimeoutError,
            TimeoutError,
            AmbiguousLocatorError,
            InvalidRefError,
        ) as error:
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
        self._capture_snapshot()
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
        if locator.startswith("ref="):
            return self._ref_locator(locator)
        if locator.startswith("role="):
            role, name = _role_locator(locator)
            result = self.page.get_by_role(role, name=name)
            # Test doubles may expose only get_by_role; real Playwright
            # locators support count(), which lets us classify duplicate role
            # matches before click/fill strictness raises a generic error.
            count = getattr(result, "count", None)
            if callable(count) and count() > 1:
                raise AmbiguousLocatorError("role locator matched multiple elements")
            return result
        return self.page.locator(locator)

    def _ref_locator(self, locator: str) -> Any:
        match = _REF_LOCATOR.fullmatch(locator)
        if match is None:
            raise InvalidRefError("ref locator must use ref=eN")
        ref = match.group(1)
        if ref not in self._latest_refs:
            raise InvalidRefError("ref is not present in the latest observation")
        get_by_ref = getattr(self.page, "get_by_ref", None)
        if not callable(get_by_ref):
            raise InvalidRefError("page does not support AI snapshot refs")
        return get_by_ref(ref)

    def _capture_snapshot(self) -> None:
        """Capture a redacted AI snapshot and replace the current ref set."""

        self.latest_snapshot = ""
        self._latest_refs = frozenset()
        aria_snapshot = getattr(self.page, "aria_snapshot", None)
        if not callable(aria_snapshot):
            return
        try:
            raw_snapshot = aria_snapshot(mode="ai")
        except (PlaywrightError, PlaywrightTimeoutError, TimeoutError):
            # The snapshot is auxiliary model context. A snapshot failure must
            # not change the existing visible observation/evidence contract.
            return
        if not isinstance(raw_snapshot, str):
            return
        self.latest_snapshot = _sanitize_snapshot(raw_snapshot)
        self._latest_refs = frozenset(_REF.findall(raw_snapshot))


_ROLE_LOCATOR = re.compile(r"^role=([^;\s]+);name=(.+)$")
_REF_LOCATOR = re.compile(r"^ref=(e\d+)$")
_REF = re.compile(r"\[ref=(e\d+)\]")
_SNAPSHOT_VALUE = re.compile(r"^(?P<prefix>\s*-\s*/value:\s*).*$", re.IGNORECASE)
_TEXTBOX_VALUE_AFTER_REF = re.compile(
    r"^(?P<prefix>\s*-\s*textbox\b.*?\[ref=e\d+\])\s*:\s*.*$",
    re.IGNORECASE,
)
_TEXTBOX_VALUE_BEFORE_REF = re.compile(
    r"^(?P<prefix>\s*-\s*textbox\b.*?):\s*.*?(?P<ref>\s+\[ref=e\d+\])\s*$",
    re.IGNORECASE,
)
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_SCROLL_PAIR = re.compile(rf"^\s*({_NUMBER})\s*(?:,|\s+)\s*({_NUMBER})\s*$")
_SCROLL_SINGLE = re.compile(rf"^\s*({_NUMBER})\s*$")


def _role_locator(locator: str) -> tuple[str, str]:
    match = _ROLE_LOCATOR.fullmatch(locator)
    if match is None:
        raise ValueError("role locator must use role=<role>;name=<name>")
    return match.group(1), match.group(2)


def _sanitize_snapshot(snapshot: str) -> str:
    """Redact textbox values while retaining labels, refs, and structure."""

    sanitized: list[str] = []
    for line in snapshot.splitlines():
        value_line = _SNAPSHOT_VALUE.match(line)
        if value_line is not None:
            sanitized.append(f"{value_line.group('prefix')}<redacted>")
            continue
        inline_value = _TEXTBOX_VALUE_AFTER_REF.match(line)
        if inline_value is not None:
            sanitized.append(f"{inline_value.group('prefix')}: <redacted>")
            continue
        inline_value = _TEXTBOX_VALUE_BEFORE_REF.match(line)
        if inline_value is not None:
            sanitized.append(
                f"{inline_value.group('prefix')}: <redacted>{inline_value.group('ref')}"
            )
            continue
        sanitized.append(line)
    return "\n".join(sanitized)


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


__all__ = [
    "ActionResult",
    "AmbiguousLocatorError",
    "BrowserGateway",
    "BudgetExhausted",
    "InvalidRefError",
]
