#!/usr/bin/env python3
"""Save private Playwright login state for the isolated local Admidio fixture."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
ADMIN_ENV = ROOT / "runs" / "admidio-local" / "admin.env"
AUTH_STATE = ROOT / "runs" / "admidio-local" / "auth.json"
LOCAL_URL = "http://127.0.0.1:3102/"


def bootstrap_auth() -> Path:
    if not stat.S_ISREG(ADMIN_ENV.lstat().st_mode):
        raise ValueError("Admidio admin credential path is not a regular file")
    values = dict(line.split("=", 1) for line in ADMIN_ENV.read_text().splitlines())
    username = values["ADMIDIO_ADMIN_USER"]
    password = values["ADMIDIO_ADMIN_PASSWORD"]
    if not username or not password:
        raise ValueError("Admidio admin credentials are incomplete")
    if (AUTH_STATE.exists() or AUTH_STATE.is_symlink()) and not stat.S_ISREG(
        AUTH_STATE.lstat().st_mode
    ):
        raise ValueError("Admidio auth state path is not a regular file")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(LOCAL_URL, wait_until="domcontentloaded")
            if not page.url.startswith("http://127.0.0.1:3102/"):
                raise RuntimeError("Admidio login page left the local fixture origin")
            page.locator("#plg_usr_login_name").fill(username)
            page.locator("#plg_usr_password").fill(password)
            page.locator("#plg_btn_login").click()
            page.get_by_text("Logged in as").wait_for(timeout=10_000)
            if not page.url.startswith("http://127.0.0.1:3102/"):
                raise RuntimeError("Admidio login left the local fixture origin")
            if "ADMINISTRATION" not in page.locator("body").inner_text():
                raise RuntimeError("Admidio administrator access was not confirmed")

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
    print("Private Admidio browser login state saved.")
