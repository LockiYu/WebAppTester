import pytest

from webapptester.browser import BrowserGateway, BudgetExhausted
from webapptester.controller import verdict_from_evidence
from webapptester.schema import Action, Checkpoint, Evidence, Observation, ObservationRegion


class _FailingLocator:
    def click(self):
        raise TimeoutError("button did not become ready")


class _FakePage:
    def __init__(self):
        self.calls = []

    def set_default_timeout(self, timeout):
        self.calls.append(("set_default_timeout", timeout))

    def set_default_navigation_timeout(self, timeout):
        self.calls.append(("set_default_navigation_timeout", timeout))

    def get_by_role(self, role, name):
        self.calls.append(("get_by_role", role, name))
        return _FailingLocator()


class _WorkingLocator:
    def __init__(self, page, *, matches=1):
        self.page = page
        self.matches = matches

    def count(self):
        return self.matches

    def click(self):
        self.page.calls.append(("click",))


class _SnapshotPage:
    url = "https://example.test"

    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = []

    def locator(self, selector):
        self.calls.append(("locator", selector))
        if selector == "body":
            return _BodyLocator()
        return _WorkingLocator(self)

    def screenshot(self, *, full_page):
        self.calls.append(("screenshot", full_page))
        return b"screenshot"

    def aria_snapshot(self, *, mode):
        self.calls.append(("aria_snapshot", mode))
        return self.snapshots.pop(0)

    def get_by_ref(self, ref):
        self.calls.append(("get_by_ref", ref))
        return _WorkingLocator(self)

    def get_by_role(self, role, name):
        self.calls.append(("get_by_role", role, name))
        return _WorkingLocator(self, matches=2)


class _ChangingURLPage(_SnapshotPage):
    def __init__(self):
        super().__init__(["- link [ref=e1]", "- link [ref=e2]"])
        self.url = "https://example.test/old"
        self.screenshots = 0

    def screenshot(self, *, full_page):
        self.screenshots += 1
        if self.screenshots == 1:
            self.url = "https://example.test/new"
        return super().screenshot(full_page=full_page)


class _BodyLocator:
    def inner_text(self):
        return "Visible page text"


class _HydratingBodyLocator:
    def __init__(self, page):
        self.page = page

    def inner_text(self):
        return "Hydrated page text" if self.page.ticks >= 2 else ""


class _HydratingPage:
    url = "https://example.test"

    def __init__(self):
        self.ticks = 0
        self.calls = []

    def locator(self, selector):
        self.calls.append(("locator", selector))
        if selector == "body":
            return _HydratingBodyLocator(self)
        return _WorkingLocator(self)

    def screenshot(self, *, full_page):
        self.calls.append(("screenshot", full_page))
        return b"screenshot"

    def wait_for_timeout(self, timeout):
        self.calls.append(("wait_for_timeout", timeout))
        self.ticks += 1

    def aria_snapshot(self, *, mode):
        self.calls.append(("aria_snapshot", mode))
        if self.ticks < 2:
            return ""
        return '- button "Ready" [ref=e9]'


class _BodyReadySnapshotHydratingPage(_HydratingPage):
    def locator(self, selector):
        self.calls.append(("locator", selector))
        if selector == "body":
            return _BodyLocator()
        return _WorkingLocator(self)


class _Store:
    def save_observation(self, *, url, text, screenshot):
        return Observation(
            id="obs-1",
            url=url,
            text=text,
            screenshot="screenshot.png",
            fingerprint="fingerprint",
        )


class _RegionElement:
    def __init__(self, text, box):
        self.text = text
        self.box = box

    def inner_text(self):
        return self.text

    def bounding_box(self):
        return {"y": 0, **self.box} if self.box is not None else None


class _RegionLocator:
    def __init__(self, elements):
        self.elements = elements

    def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]

    def inner_text(self):
        return self.elements[0].inner_text()

    def bounding_box(self):
        return self.elements[0].bounding_box()


class _SpatialPage:
    viewport_size = {"width": 1280, "height": 720}

    def locator(self, selector):
        if selector == '[class*="sidebar" i]':
            return _RegionLocator(
                [
                    _RegionElement("whole page", {"x": 0, "width": 1280, "height": 720}),
                    _RegionElement("Course Test\nAdd Project", {"x": 0, "width": 230, "height": 670}),
                ]
            )
        if selector == '[class*="projects" i]':
            return _RegionLocator(
                [_RegionElement("Course Test\nOther project", {"x": 230, "width": 1050, "height": 670})]
            )
        return _RegionLocator([])


class _OverlappingSpatialPage(_SpatialPage):
    def locator(self, selector):
        if selector == '[class*="projects" i]':
            return _RegionLocator(
                [_RegionElement("Course Test\nOther project", {"x": 200, "width": 1080, "height": 670})]
            )
        return super().locator(selector)


class _SemanticSidebarOverlapPage(_OverlappingSpatialPage):
    def locator(self, selector):
        if selector == "aside":
            return _RegionLocator(
                [_RegionElement("Course Test\nSidebar", {"x": 0, "width": 500, "height": 670})]
            )
        return super().locator(selector)


class _SemanticMainOverlapPage(_SpatialPage):
    def locator(self, selector):
        if selector == "main":
            return _RegionLocator(
                [_RegionElement("Course Test\nDashboard", {"x": 200, "width": 1080, "height": 670})]
            )
        if selector == '[class*="sidebar" i]':
            return _RegionLocator(
                [_RegionElement("Course Test\nSidebar", {"x": 0, "width": 500, "height": 670})]
            )
        return super().locator(selector)


class _TwoSemanticRegionsPage(_SpatialPage):
    def __init__(self, main_box):
        self.main_box = main_box

    def locator(self, selector):
        if selector == "main":
            return _RegionLocator([_RegionElement("Course Test\nDashboard", self.main_box)])
        if selector == "aside":
            return _RegionLocator(
                [_RegionElement("Course Test\nSidebar", {"x": 0, "width": 230, "height": 670})]
            )
        return _RegionLocator([])


def test_spatial_fallback_keeps_sidebar_and_dashboard_separate():
    regions = BrowserGateway(_SpatialPage(), max_steps=2)._capture_regions()

    assert [(region.id, region.text) for region in regions] == [
        ("aside", "Course Test\nAdd Project"),
        ("main", "Course Test\nOther project"),
    ]
    assert all("nth=" in region.locator for region in regions)


def test_spatial_fallback_rejects_overlapping_dashboard_evidence():
    regions = BrowserGateway(_OverlappingSpatialPage(), max_steps=2)._capture_regions()

    assert [(region.id, region.text) for region in regions] == [
        ("aside", "Course Test\nAdd Project"),
    ]


def test_spatial_main_rejects_overlap_with_semantic_sidebar():
    regions = BrowserGateway(_SemanticSidebarOverlapPage(), max_steps=2)._capture_regions()

    assert [(region.id, region.text) for region in regions] == [
        ("aside", "Course Test\nSidebar"),
    ]


def test_spatial_sidebar_rejects_overlap_with_semantic_main():
    regions = BrowserGateway(_SemanticMainOverlapPage(), max_steps=2)._capture_regions()

    assert [(region.id, region.text) for region in regions] == [
        ("main", "Course Test\nDashboard"),
    ]


def test_semantic_main_cannot_reuse_sidebar_pixels():
    regions = BrowserGateway(
        _TwoSemanticRegionsPage({"x": 0, "width": 1280, "height": 670}), max_steps=2
    )._capture_regions()
    assert [region.id for region in regions] == ["aside"]


def test_separate_semantic_regions_remain_available():
    regions = BrowserGateway(
        _TwoSemanticRegionsPage({"x": 230, "width": 1050, "height": 670}), max_steps=2
    )._capture_regions()
    assert [region.id for region in regions] == ["main", "aside"]


def test_spatial_regions_require_project_in_both_locations():
    regions = BrowserGateway(_SpatialPage(), max_steps=2)._capture_regions()
    checkpoints = (
        Checkpoint("dashboard", "appears on dashboard", "Course Test", True, "dashboard", "Course Test"),
        Checkpoint("sidebar", "appears in sidebar", "Course Test", True, "sidebar", "Course Test"),
    )
    evidence = (
        Evidence("dashboard", "o1", "Course Test", "SAT", "main"),
        Evidence("sidebar", "o1", "Course Test", "SAT", "aside"),
    )
    both = Observation("o1", "https://example.test", "Course Test", "shot.png", "fp", regions)
    only_sidebar = Observation(
        "o1",
        "https://example.test",
        "Course Test",
        "shot.png",
        "fp",
        tuple(
            ObservationRegion(region.id, "Other project", region.locator)
            if region.id == "main" else region
            for region in regions
        ),
    )

    assert verdict_from_evidence(checkpoints, evidence, (both,)).status == "PASS"
    assert verdict_from_evidence(checkpoints, evidence, (only_sidebar,)).status == "UNKNOWN"


def test_failed_click_consumes_a_browser_step():
    page = _FakePage()
    gateway = BrowserGateway(page, max_steps=2)

    assert ("set_default_navigation_timeout", 5000) in page.calls

    result = gateway.act(Action(kind="click", locator="role=button;name=Create"))

    assert result.success is False
    assert gateway.steps == 1
    assert result.steps == 1


def test_budget_exhaustion_happens_before_second_browser_action():
    page = _FakePage()
    gateway = BrowserGateway(page, max_steps=1)
    action = Action(kind="click", locator="role=button;name=Create")

    gateway.act(action)
    calls_after_first_action = list(page.calls)

    with pytest.raises(BudgetExhausted):
        gateway.act(action)

    assert gateway.steps == 1
    assert page.calls == calls_after_first_action


def test_stop_does_not_consume_a_browser_step():
    page = _FakePage()
    gateway = BrowserGateway(page, max_steps=1)

    result = gateway.act(Action(kind="stop"))

    assert result.success is True
    assert result.error == ""
    assert gateway.steps == 0
    assert result.steps == 0


def test_observation_keeps_sanitized_snapshot_and_dispatches_latest_ref():
    secret = "do-not-leak-this-value"
    page = _SnapshotPage(
        [
            '\n'.join(
                [
                    '- textbox "Password" [ref=e1]: "do-not-leak-this-value"',
                    '  - /value: do-not-leak-this-value',
                    '- button "Save" [ref=e2]',
                ]
            ),
            '- button "Other" [ref=e3]',
        ]
    )
    gateway = BrowserGateway(page, max_steps=4)

    gateway.observe(_Store())

    assert secret not in gateway.latest_snapshot
    assert "<redacted>" in gateway.latest_snapshot
    result = gateway.act(Action(kind="click", locator="ref=e2"))
    assert result.success is True
    assert ("get_by_ref", "e2") in page.calls

    # A subsequent observation replaces the temporary allowlist.
    gateway.observe(_Store())
    stale = gateway.act(Action(kind="click", locator="ref=e2"))
    current = gateway.act(Action(kind="click", locator="ref=e3"))
    assert stale.success is False
    assert stale.error.startswith("InvalidRefError:")
    assert current.success is True


def test_observation_recaptures_when_route_changes_mid_capture():
    page = _ChangingURLPage()
    gateway = BrowserGateway(page, max_steps=2)

    observation = gateway.observe(_Store())

    assert observation.url == "https://example.test/new"
    assert page.screenshots == 2
    assert gateway.latest_snapshot == "- link [ref=e2]"


def test_observation_waits_for_hydrated_body_and_snapshot_without_spending_steps():
    page = _HydratingPage()
    gateway = BrowserGateway(page, max_steps=2)

    observation = gateway.observe(_Store())

    assert observation.text == "Hydrated page text"
    assert gateway.latest_snapshot == '- button "Ready" [ref=e9]'
    assert gateway.steps == 0
    assert ("wait_for_timeout", 50) in page.calls


def test_observation_waits_for_snapshot_when_body_mounts_first():
    page = _BodyReadySnapshotHydratingPage()
    gateway = BrowserGateway(page, max_steps=2)

    observation = gateway.observe(_Store())

    assert observation.text == "Visible page text"
    assert gateway.latest_snapshot == '- button "Ready" [ref=e9]'
    assert page.calls.count(("wait_for_timeout", 50)) == 2


def test_snapshot_redacts_inline_values_for_all_input_roles():
    snapshot = "\n".join(
        [
            '- searchbox "Search: docs": SEARCH_SECRET [ref=e3]',
            '- spinbutton "Age" [ref=e4]: 42',
            '- combobox "Country": COUNTRY_SECRET [ref=e5]',
            '- slider "Volume" [ref=e7]: SLIDER_SECRET',
            '- button "Save: now" [ref=e6]',
            '- text: Visible static text',
        ]
    )
    page = _SnapshotPage([snapshot])
    gateway = BrowserGateway(page, max_steps=1)

    gateway.observe(_Store())

    assert "SEARCH_SECRET" not in gateway.latest_snapshot
    assert "COUNTRY_SECRET" not in gateway.latest_snapshot
    assert "42" not in gateway.latest_snapshot
    assert "SLIDER_SECRET" not in gateway.latest_snapshot
    assert '- searchbox "Search: docs": <redacted> [ref=e3]' in gateway.latest_snapshot
    assert '- spinbutton "Age" [ref=e4]: <redacted>' in gateway.latest_snapshot
    assert '- combobox "Country": <redacted> [ref=e5]' in gateway.latest_snapshot
    assert '- slider "Volume" [ref=e7]: <redacted>' in gateway.latest_snapshot
    assert '- button "Save: now" [ref=e6]' in gateway.latest_snapshot
    assert "- text: Visible static text" in gateway.latest_snapshot


@pytest.mark.parametrize("locator", ["ref=e", "ref=x1", "ref=e1x", "ref="])
def test_malformed_ref_is_rejected_without_page_dispatch(locator):
    page = _SnapshotPage(["- button \"Save\" [ref=e1]"])
    gateway = BrowserGateway(page, max_steps=2)
    gateway.observe(_Store())

    result = gateway.act(Action(kind="click", locator=locator))

    assert result.success is False
    assert result.error.startswith("InvalidRefError:")
    assert not any(call[0] == "get_by_ref" for call in page.calls)


def test_duplicate_role_locator_is_classified_before_click():
    page = _SnapshotPage([])
    gateway = BrowserGateway(page, max_steps=2)

    result = gateway.act(
        Action(kind="click", locator="role=button;name=Add Project")
    )

    assert result.success is False
    assert result.error.startswith("AmbiguousLocatorError:")
    assert gateway.steps == 1
    assert ("get_by_role", "button", "Add Project") in page.calls
    assert ("click",) not in page.calls
