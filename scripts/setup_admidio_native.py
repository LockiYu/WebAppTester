#!/usr/bin/env python3
"""Prepare an isolated native Admidio database when Docker Hub is unavailable.

Run this only after downloading and extracting the official 5.0.2 ZIP into
``runs/admidio-local/app``. It never prints credentials or modifies other
databases. The Admidio web installer still completes the schema installation.
"""

from __future__ import annotations

import os
import secrets
import stat
import subprocess
from pathlib import Path

from setup_admidio_local import create_env_file


ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "webapptester_admidio"
DB_USER = "wat_admidio"


def _read_password(path: Path) -> str:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Admidio credential path is not a regular file")
    values = dict(line.split("=", 1) for line in path.read_text().splitlines())
    password = values["ADMIDIO_DB_PASSWORD"]
    if not password or not all(character.isalnum() or character in "-_" for character in password):
        raise ValueError("Admidio database password has unsupported characters")
    return password


def _mysql(query: str, *, input_text: str | None = None) -> str:
    command = ["mysql", "--batch", "--skip-column-names"]
    if input_text is None:
        command.extend(("-e", query))
    result = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def prepare_native(root: Path = ROOT) -> None:
    root = Path(root)
    app = root / "runs" / "admidio-local" / "app"
    if not (app / "vendor" / "autoload.php").is_file():
        raise FileNotFoundError("extract the official Admidio 5.0.2 ZIP into runs/admidio-local/app first")
    env_path = create_env_file(root)
    password = _read_password(env_path)
    admin_env = root / "runs" / "admidio-local" / "admin.env"
    if (admin_env.exists() or admin_env.is_symlink()) and not stat.S_ISREG(
        admin_env.lstat().st_mode
    ):
        raise ValueError("Admidio admin credential path is not a regular file")

    database_exists = _mysql(
        f"SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='{DB_NAME}'"
    ) == DB_NAME
    user_exists = _mysql(
        f"SELECT User FROM mysql.user WHERE User='{DB_USER}' AND Host='127.0.0.1'"
    ) == DB_USER
    if database_exists != user_exists:
        raise RuntimeError("dedicated Admidio database and user have inconsistent existing state")
    if database_exists:
        check = subprocess.run(
            [
                "mysql", "--host=127.0.0.1", f"--user={DB_USER}",
                f"--database={DB_NAME}", "--batch", "--skip-column-names", "-e", "SELECT 1",
            ],
            env={**os.environ, "MYSQL_PWD": password},
            text=True,
            capture_output=True,
            check=False,
        )
        if check.returncode != 0 or check.stdout.strip() != "1":
            raise RuntimeError("existing Admidio database user does not match the private credential file")
    else:
        sql = (
            f"CREATE DATABASE {DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"CREATE USER '{DB_USER}'@'127.0.0.1' IDENTIFIED BY '{password}';\n"
            f"GRANT ALL PRIVILEGES ON {DB_NAME}.* TO '{DB_USER}'@'127.0.0.1';\n"
        )
        _mysql("", input_text=sql)

    try:
        descriptor = os.open(admin_env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not stat.S_ISREG(admin_env.lstat().st_mode):
            raise ValueError("Admidio admin credential path is not a regular file")
        os.chmod(admin_env, 0o600)
    else:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("ADMIDIO_ADMIN_USER=wat_admin\n")
            stream.write(f"ADMIDIO_ADMIN_PASSWORD=WaT!7{secrets.token_urlsafe(24)}\n")
        os.chmod(admin_env, 0o600)


if __name__ == "__main__":
    prepare_native()
    print("Native Admidio database and private credentials are ready.")
    print("Start: /opt/homebrew/opt/php@8.3/bin/php -S 127.0.0.1:3102 -t runs/admidio-local/app")
