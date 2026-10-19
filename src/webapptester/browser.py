"""The only gateway through which the tester may interact with a browser."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .schema import Action, Observation, ObservationRegion

if TYPE_CHECKING:
    from .store import RunStore


DEFAULT_ACTION_TIMEOUT_MS = 5_000
# SPA hydration can leave both the visible body and the accessibility tree
# empty for a short interval after navigation. Keep this wait independent from
# the action budget and shorter than the page-wide Playwright timeout.
OBSERVATION_READY_TIMEOUT_MS = 750
OBSERVATION_POLL_INTERVAL_MS = 50
OBSERVATION_SETTLE_MS = 500
MAX_STEPS = 35


def _regions_separate(
    first: dict[str, float] | None, second: dict[str, float] | None
) -> bool:
    """Require proven nonoverlap before using two regions independently."""

    if first is None or second is None:
        return False
    try:
        ax, ay, aw, ah = (float(first[key]) for key in ("x", "y", "width", "height"))
        bx, by, bw, bh = (float(second[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return False
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    return ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay


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

        wait_for_timeout = getattr(self.page, "wait_for_timeout", None)
        if self.steps and callable(wait_for_timeout):
            # SPA clicks can return before their route and content settle.
            wait_for_timeout(OBSERVATION_SETTLE_MS)
        for attempt in range(2):
            url = self.page.url
            text = self._body_text()
            self._capture_snapshot()
            if not self._observation_is_meaningful(text):
                text = self._wait_for_observation_ready(text)
            screenshot = self.page.screenshot(full_page=True)
            regions = self._capture_regions()
            if self.page.url == url:
                if regions:
                    return store.save_observation(
                        url=url, text=text, screenshot=screenshot, regions=regions
                    )
                # Keep lightweight gateways and legacy stores source-compatible
                # when a page exposes no capturable semantic regions.
                return store.save_observation(url=url, text=text, screenshot=screenshot)
            if attempt == 0 and callable(wait_for_timeout):
                wait_for_timeout(OBSERVATION_SETTLE_MS)
        raise RuntimeError("page URL changed during observation capture")

    def _body_text(self) -> str:
        return self.page.locator("body").inner_text()[:20_000]

    def _observation_is_meaningful(self, text: str) -> bool:
        # A real Playwright page exposes ``aria_snapshot`` even while an SPA's
        # visible body has already mounted. Keep polling until that semantic
        # tree is available so ref grounded actions do not race hydration.
        if callable(getattr(self.page, "aria_snapshot", None)):
            return bool(self.latest_snapshot.strip())
        return bool(text.strip())

    def _wait_for_observation_ready(self, text: str) -> str:
        """Give a hydrating SPA a short chance to expose visible state.

        ``wait_for_timeout`` yields to the browser event loop, allowing React
        and similar runtimes to finish committing their first visible tree.
        Lightweight test doubles may not provide it, in which case the
        original single-pass observation behavior is preserved.
        """

        wait_for_timeout = getattr(self.page, "wait_for_timeout", None)
        if not callable(wait_for_timeout):
            return text

        deadline = time.monotonic() + OBSERVATION_READY_TIMEOUT_MS / 1_000
        while not self._observation_is_meaningful(text):
            remaining_ms = int(max(0, (deadline - time.monotonic()) * 1_000))
            if remaining_ms <= 0:
                break
            try:
                wait_for_timeout(min(OBSERVATION_POLL_INTERVAL_MS, remaining_ms))
                text = self._body_text()
                self._capture_snapshot()
            except (PlaywrightError, PlaywrightTimeoutError, TimeoutError):
                # Readiness is auxiliary context. Preserve the current
                # observation contract if a page closes during the short wait.
                break
        return text

    def _capture_regions(self) -> tuple[ObservationRegion, ...]:
        """Capture separate semantic or spatial DOM regions.

        The body remains the legacy observation source.  Region captures are
        additive and are only used when a checkpoint explicitly asks for a
        scoped assertion. Some SPAs omit landmark elements, so a bounded
        fallback looks for visually separate sidebar and main containers with
        common semantic class names. A page without separable regions still
        leaves the scoped assertion UNKNOWN.
        """

        candidates = (
            ("main", "main"),
            ("aside", "aside"),
            ("role-main", '[role="main"]'),
            ("role-complementary", '[role="complementary"]'),
            ("evidence-region", "[data-evidence-region]"),
            ("heading", 'h1,h2,h3,h4,h5,h6,[role="heading"]'),
        )
        regions: list[ObservationRegion] = []
        region_boxes: dict[str, dict[str, float] | None] = {}
        seen_text: set[str] = set()
        sidebar_right_edges: list[float] = []
        sidebar_geometry_unknown = False
        main_left_edges: list[float] = []
        main_geometry_unknown = False
        for region_id, selector in candidates:
            try:
                locator = self.page.locator(selector)
                count = locator.count()
            except Exception:
                continue
            for index in range(count):
                try:
                    target = locator.nth(index) if count > 1 else locator
                    if region_id == "heading" and (
                        not target.is_visible()
                        or target.evaluate(
                            '(el) => !!el.closest("aside,nav,a,tr,[role=complementary],[role=navigation],[role=link],[role=row]")'
                        )
                        or not (box := target.bounding_box())
                        or box["width"] < 8
                        or box["height"] < 8
                    ):
                        continue
                    text = target.inner_text()[:20_000]
                except Exception:
                    continue
                if (
                    not isinstance(text, str) or not text.strip()
                    or (text in seen_text and region_id != "heading")
                ):
                    continue
                seen_text.add(text)
                suffix = f"[{index}]" if count > 1 else ""
                region = ObservationRegion(f"{region_id}{suffix}", text, selector)
                regions.append(region)
                try:
                    box = target.bounding_box()
                    region_boxes[region.id] = box if isinstance(box, dict) else None
                except (AttributeError, PlaywrightError):
                    region_boxes[region.id] = None
                if region_id in {"aside", "role-complementary"}:
                    try:
                        box = region_boxes[region.id]
                        if not isinstance(box, dict):
                            raise ValueError("missing sidebar geometry")
                        sidebar_right_edges.append(box["x"] + box["width"])
                    except (AttributeError, KeyError, TypeError, ValueError, PlaywrightError):
                        sidebar_geometry_unknown = True
                if region_id in {"main", "role-main"}:
                    try:
                        box = region_boxes[region.id]
                        if not isinstance(box, dict):
                            raise ValueError("missing main geometry")
                        main_left_edges.append(box["x"])
                    except (AttributeError, KeyError, TypeError, ValueError, PlaywrightError):
                        main_geometry_unknown = True
        present = {region.id.split("[", 1)[0] for region in regions}
        if not ({"aside", "role-complementary"} & present) and not main_geometry_unknown:
            sidebar_candidate = self._capture_spatial_region(
                "aside",
                ('[class*="sidebar" i]', '[class*="sidenav" i]'),
                right_neighbor_left=min(main_left_edges) if main_left_edges else None,
            )
            if sidebar_candidate is not None:
                sidebar, sidebar_box = sidebar_candidate
            else:
                sidebar = None
            if sidebar is not None and sidebar.text not in seen_text:
                regions.append(sidebar)
                region_boxes[sidebar.id] = sidebar_box
                seen_text.add(sidebar.text)
                sidebar_right_edges.append(sidebar_box["x"] + sidebar_box["width"])
        if not ({"main", "role-main"} & present) and not sidebar_geometry_unknown:
            main_candidate = self._capture_spatial_region(
                "main",
                (
                    '[class*="dashboard" i]',
                    '[class*="main" i]',
                    '[class*="projects" i]',
                ),
                left_neighbor_right=max(sidebar_right_edges) if sidebar_right_edges else None,
            )
            if main_candidate is not None and main_candidate[0].text not in seen_text:
                regions.append(main_candidate[0])
                region_boxes[main_candidate[0].id] = main_candidate[1]
        sidebar_regions = [
            region for region in regions
            if region.id.split("[", 1)[0] in {"aside", "role-complementary"}
        ]
        if sidebar_regions:
            # Two scoped claims must come from distinct visible areas. A
            # semantic main may contain an aside, even when both landmarks
            # exist, so semantic labels alone cannot prove separation.
            regions = [
                region for region in regions
                if region.id.split("[", 1)[0] not in {"main", "role-main"}
                or all(
                    _regions_separate(region_boxes.get(region.id), region_boxes.get(sidebar.id))
                    for sidebar in sidebar_regions
                )
            ]
        return tuple(regions)

    def _capture_spatial_region(
        self,
        region_id: str,
        selectors: tuple[str, ...],
        *,
        left_neighbor_right: float | None = None,
        right_neighbor_left: float | None = None,
    ) -> tuple[ObservationRegion, dict[str, float]] | None:
        viewport = getattr(self.page, "viewport_size", None)
        if not isinstance(viewport, dict):
            return None
        width = viewport.get("width")
        height = viewport.get("height")
        if not isinstance(width, (int, float)) or not isinstance(height, (int, float)):
            return None
        if width <= 0 or height <= 0:
            return None
        best: tuple[int, float, ObservationRegion, dict[str, float]] | None = None
        for selector in selectors:
            try:
                locator = self.page.locator(selector)
                count = min(locator.count(), 80)
            except Exception:
                continue
            for index in range(count):
                try:
                    target = locator.nth(index)
                    box = target.bounding_box()
                    if not isinstance(box, dict):
                        continue
                    x, box_width, box_height = box["x"], box["width"], box["height"]
                    # A scoped dashboard assertion must not reuse pixels
                    # covered by the selected sidebar assertion.
                    if left_neighbor_right is not None and x < left_neighbor_right:
                        continue
                    if right_neighbor_left is not None and x + box_width > right_neighbor_left:
                        continue
                    if region_id == "aside":
                        separated = (
                            x <= width * 0.15
                            and width * 0.08 <= box_width <= width * 0.45
                            and box_height >= height * 0.25
                        )
                    else:
                        separated = (
                            x >= width * 0.12
                            and box_width >= width * 0.40
                            and box_height >= height * 0.10
                        )
                    if not separated:
                        continue
                    region_text = target.inner_text()[:20_000]
                    if not isinstance(region_text, str) or not region_text.strip():
                        continue
                except Exception:
                    continue
                region = ObservationRegion(
                    region_id, region_text, f"{selector} >> nth={index}"
                )
                rank = (len(region_text), box_width * box_height)
                if best is None or rank > best[:2]:
                    best = (rank[0], rank[1], region, box)
        return (best[2], best[3]) if best is not None else None

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
_INPUT_ROLE_LINE = re.compile(
    r"^\s*-\s*(?:textbox|searchbox|combobox|spinbutton|slider)\b",
    re.IGNORECASE,
)
_REF_TOKEN = re.compile(r"\[ref=e\d+\]")
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_SCROLL_PAIR = re.compile(rf"^\s*({_NUMBER})\s*(?:,|\s+)\s*({_NUMBER})\s*$")
_SCROLL_SINGLE = re.compile(rf"^\s*({_NUMBER})\s*$")


def _role_locator(locator: str) -> tuple[str, str]:
    match = _ROLE_LOCATOR.fullmatch(locator)
    if match is None:
        raise ValueError("role locator must use role=<role>;name=<name>")
    return match.group(1), match.group(2)


def _sanitize_snapshot(snapshot: str) -> str:
    """Redact input values while retaining labels, refs, and structure."""

    sanitized: list[str] = []
    for line in snapshot.splitlines():
        value_line = _SNAPSHOT_VALUE.match(line)
        if value_line is not None:
            sanitized.append(f"{value_line.group('prefix')}<redacted>")
            continue
        if _INPUT_ROLE_LINE.match(line) is not None:
            sanitized.append(_sanitize_input_role_line(line))
            continue
        sanitized.append(line)
    return "\n".join(sanitized)


def _sanitize_input_role_line(line: str) -> str:
    """Redact the value after an input role's unquoted colon.

    Accessible names are quoted and state/ref annotations are bracketed, so a
    colon outside both forms identifies the inline control value. Preserve any
    refs that follow that value so the model can still act on the control.
    """

    colon = _unquoted_colon(line)
    if colon is None:
        return line
    prefix = line[: colon + 1]
    suffix = line[colon + 1 :]
    refs = _REF_TOKEN.findall(suffix)
    if refs:
        return f"{prefix} <redacted>{''.join(f' {ref}' for ref in refs)}"
    return f"{prefix} <redacted>"


def _unquoted_colon(line: str) -> int | None:
    in_quotes = False
    escaped = False
    bracket_depth = 0
    for index, character in enumerate(line):
        if in_quotes:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_quotes = False
            continue
        if character == '"':
            in_quotes = True
        elif character == "[":
            bracket_depth += 1
        elif character == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif character == ":" and bracket_depth == 0:
            return index
    return None


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
