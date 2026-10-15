#!/usr/bin/env python3
"""Create a private Playwright storage state for the isolated 4gaBoards app."""

from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / "runs" / "4gaboards-local" / "instance.env"
DEFAULT_STORAGE_PATH = ROOT / "runs" / "4gaboards-local" / "auth.json"
DEFAULT_LOGIN_URL = "http://127.0.0.1:3001/login"
REQUIRED_ENV_KEYS = (
    "DEFAULT_ADMIN_EMAIL",
    "DEFAULT_ADMIN_PASSWORD",
)


class BootstrapError(RuntimeError):
    """Raised when the local authentication fixture cannot be bootstrapped."""


def read_local_env(path: Path | str) -> dict[str, str]:
    """Read the local fixture env file without logging any values."""

    env_path = Path(path)
    try:
        if not env_path.is_file() or not stat.S_ISREG(env_path.stat().st_mode):
            raise BootstrapError("fixture env file is missing or invalid")
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise BootstrapError("fixture env file is unreadable") from error

    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if not separator or not key or any(character.isspace() for character in key):
            raise BootstrapError("fixture env file is malformed")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value:
            raise BootstrapError("fixture env file is incomplete")
        values[key] = value

    if any(key not in values for key in REQUIRED_ENV_KEYS):
        raise BootstrapError("fixture env file is incomplete")
    return values


def _launch_chromium(chromium: Any) -> Any:
    """Prefer Playwright's managed Chromium, with installed Chrome fallback."""

    try:
        return chromium.launch(headless=True)
    except PlaywrightError as bundled_error:
        try:
            return chromium.launch(channel="chrome", headless=True)
        except PlaywrightError:
            raise bundled_error


def _first_login_field(page: Any, selectors: tuple[str, ...]) -> Any:
    for selector in selectors:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator.first
    raise BootstrapError("login form field is unavailable")


def _submit_login(page: Any) -> None:
    button = page.get_by_role("button", name=re.compile(r"(?:sign\s*in|log\s*in|login)", re.I))
    if button.count() > 0:
        button.first.click()
        return
    fallback = page.locator('button[type="submit"], input[type="submit"]')
    if fallback.count() > 0:
        fallback.first.click()
        return
    raise BootstrapError("login form submit control is unavailable")


def _wait_for_dashboard(page: Any) -> None:
    """Wait for a visible dashboard marker after login."""

    try:
        page.wait_for_function(
            "() => document.body && document.body.innerText.toLowerCase().includes('dashboard')",
            timeout=15_000,
        )
        if "dashboard" in page.locator("body").inner_text().lower():
            return
    except (PlaywrightError, PlaywrightTimeoutError):
        pass

    markers = (
        page.get_by_role("button", name=re.compile(r"add\s+project", re.I)),
        page.get_by_role("link", name=re.compile(r"add\s+project", re.I)),
        page.get_by_text(re.compile("dashboard", re.I)),
    )
    for marker in markers:
        try:
            if marker.count() > 0:
                marker.first.wait_for(state="visible", timeout=15_000)
                return
        except (PlaywrightError, PlaywrightTimeoutError):
            continue
    raise BootstrapError("authenticated dashboard was not confirmed")


def _write_storage_state(context: Any, destination: Path) -> None:
    """Write state atomically and retain restrictive permissions throughout."""

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        temporary_path = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        descriptor = None
        context.storage_state(path=str(temporary_path))
        os.chmod(temporary_path, 0o600)
        temporary_path.replace(destination)
        os.chmod(destination, 0o600)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def bootstrap_auth(
    *,
    env_path: Path | str = DEFAULT_ENV_PATH,
    login_url: str = DEFAULT_LOGIN_URL,
    storage_path: Path | str = DEFAULT_STORAGE_PATH,
) -> Path:
    """Log in in a private context and save its authenticated storage state."""

    values = read_local_env(env_path)
    destination = Path(storage_path)
    try:
        with sync_playwright() as playwright:
            browser = _launch_chromium(playwright.chromium)
            context = browser.new_context()
            try:
                page = context.new_page()
                page.goto(login_url)
                username = _first_login_field(
                    page,
                    (
                        'input[name="username"]',
                        'input[name="emailOrUsername"]',
                        'input[autocomplete="username"]',
                        'input[type="email"]',
                        'input[name="email"]',
                        'input[type="text"]',
                    ),
                )
                password = _first_login_field(
                    page,
                    (
                        'input[name="password"]',
                        'input[autocomplete="current-password"]',
                        'input[type="password"]',
                    ),
                )
                # The login field accepts email or username.  The generated
                # email is the stable identifier accepted by every fixture
                # locale; keep the username as a selector fallback only.
                username.fill(values["DEFAULT_ADMIN_EMAIL"])
                password.fill(values["DEFAULT_ADMIN_PASSWORD"])
                _submit_login(page)
                _wait_for_dashboard(page)
                _write_storage_state(context, destination)
            finally:
                context.close()
                browser.close()
    except BootstrapError:
        raise
    except (OSError, PlaywrightError, PlaywrightTimeoutError, RuntimeError) as error:
        raise BootstrapError("4gaBoards authentication bootstrap failed") from error
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--login-url", default=DEFAULT_LOGIN_URL)
    parser.add_argument("--storage-state", type=Path, default=DEFAULT_STORAGE_PATH)
    args = parser.parse_args(argv)
    try:
        bootstrap_auth(
            env_path=args.env_file,
            login_url=args.login_url,
            storage_path=args.storage_state,
        )
    except BootstrapError:
        print("Authentication bootstrap failed; no secret values were printed.", file=os.sys.stderr)
        return 2
    print("Authentication state saved with private permissions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
