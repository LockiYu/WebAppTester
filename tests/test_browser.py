import pytest

from webapptester.browser import BrowserGateway, BudgetExhausted
from webapptester.schema import Action, Observation


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


class _BodyLocator:
    def inner_text(self):
        return "Visible page text"


class _Store:
    def save_observation(self, *, url, text, screenshot):
        return Observation(
            id="obs-1",
            url=url,
            text=text,
            screenshot="screenshot.png",
            fingerprint="fingerprint",
        )


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


def test_snapshot_redacts_inline_values_for_all_input_roles():
    snapshot = "\n".join(
        [
            '- searchbox "Search: docs": SEARCH_SECRET [ref=e3]',
            '- spinbutton "Age" [ref=e4]: 42',
            '- combobox "Country": COUNTRY_SECRET [ref=e5]',
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
    assert '- searchbox "Search: docs": <redacted> [ref=e3]' in gateway.latest_snapshot
    assert '- spinbutton "Age" [ref=e4]: <redacted>' in gateway.latest_snapshot
    assert '- combobox "Country": <redacted> [ref=e5]' in gateway.latest_snapshot
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
