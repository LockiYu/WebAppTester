#!/usr/bin/env python3
"""Create private database credentials for an isolated local Admidio fixture."""

from __future__ import annotations

import argparse
import os
import secrets
import stat
from pathlib import Path


ENV_RELATIVE_PATH = Path("runs") / "admidio-local" / "instance.env"
DOCKER_COMMAND = (
    "docker compose --env-file runs/admidio-local/instance.env "
    "-p webapptester-admidio -f examples/admidio/compose.yaml up -d"
)


def create_env_file(root: Path | str | None = None) -> Path:
    """Create credentials once without replacing or printing existing values."""

    project_root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    env_path = project_root / ENV_RELATIVE_PATH
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if not stat.S_ISREG(env_path.lstat().st_mode):
            raise ValueError("existing Admidio credential path is not a regular file")
        os.chmod(env_path, 0o600)
        return env_path
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(f"ADMIDIO_DB_ROOT_PASSWORD={secrets.token_urlsafe(32)}\n")
            stream.write(f"ADMIDIO_DB_PASSWORD={secrets.token_urlsafe(32)}\n")
        os.chmod(env_path, 0o600)
    except BaseException:
        try:
            os.unlink(env_path)
        except OSError:
            pass
        raise
    return env_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    create_env_file(args.root)
    print(DOCKER_COMMAND)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
