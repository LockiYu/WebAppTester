import pytest

from webapptester.browser import BrowserGateway, BudgetExhausted
from webapptester.schema import Action


class _FailingLocator:
    def click(self):
        raise TimeoutError("button did not become ready")


class _FakePage:
    def __init__(self):
        self.calls = []

    def set_default_timeout(self, timeout):
        self.calls.append(("set_default_timeout", timeout))

    def get_by_role(self, role, name):
        self.calls.append(("get_by_role", role, name))
        return _FailingLocator()


def test_failed_click_consumes_a_browser_step():
    page = _FakePage()
    gateway = BrowserGateway(page, max_steps=2)

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
