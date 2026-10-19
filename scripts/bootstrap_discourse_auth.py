#!/usr/bin/env python3
"""Save a private browser session for the isolated local Discourse fixture."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from webapptester.cli import _launch_chromium


ROOT = Path(__file__).resolve().parents[1]
ADMIN_ENV = ROOT / "runs" / "discourse-local" / "admin.env"
AUTH_STATE = ROOT / "runs" / "discourse-local" / "auth.json"
LOCAL_URL = "http://127.0.0.1:4203/"


def bootstrap_auth() -> Path:
    if not stat.S_ISREG(ADMIN_ENV.lstat().st_mode):
        raise ValueError("Discourse admin credential path is not a regular file")
    values = dict(line.split("=", 1) for line in ADMIN_ENV.read_text().splitlines())
    email = values["DISCOURSE_ADMIN_EMAIL"]
    password = values["DISCOURSE_ADMIN_PASSWORD"]
    if not email or not password:
        raise ValueError("Discourse admin credentials are incomplete")
    if (AUTH_STATE.exists() or AUTH_STATE.is_symlink()) and not stat.S_ISREG(
        AUTH_STATE.lstat().st_mode
    ):
        raise ValueError("Discourse auth state path is not a regular file")

    with sync_playwright() as playwright:
        browser = _launch_chromium(playwright.chromium)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(LOCAL_URL, wait_until="domcontentloaded", timeout=30_000)
            if not page.url.startswith(LOCAL_URL):
                raise RuntimeError("Discourse login left the local fixture origin")
            page.get_by_role("button", name="Log In", exact=True).click(timeout=10_000)
            page.locator("#login-account-name").fill(email, timeout=10_000)
            page.locator("#login-account-password").fill(password, timeout=10_000)
            page.locator("#login-button").click(timeout=10_000)
            page.locator("#current-user").wait_for(state="visible", timeout=20_000)
            if not page.url.startswith(LOCAL_URL):
                raise RuntimeError("Discourse login left the local fixture origin")

            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".auth.", suffix=".tmp", dir=AUTH_STATE.parent
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                os.close(descriptor)
                context.storage_state(path=str(temporary))
                os.chmod(temporary, 0o600)
                temporary.replace(AUTH_STATE)
            finally:
                if temporary.exists():
                    temporary.unlink()
            os.chmod(AUTH_STATE, 0o600)
        finally:
            browser.close()
    return AUTH_STATE


if __name__ == "__main__":
    bootstrap_auth()
    print("Private Discourse browser login state saved.")
