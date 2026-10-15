#!/usr/bin/env python3
"""Create private first-run credentials for the isolated 4gaBoards fixture."""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path


ENV_RELATIVE_PATH = Path("runs") / "4gaboards-local" / "instance.env"
ENV_VARIABLES = (
    "POSTGRES_PASSWORD",
    "SECRET_KEY",
    "DEFAULT_ADMIN_USERNAME",
    "DEFAULT_ADMIN_EMAIL",
    "DEFAULT_ADMIN_NAME",
    "DEFAULT_ADMIN_PASSWORD",
)
DOCKER_COMMAND = (
    "docker compose --env-file runs/4gaboards-local/instance.env "
    "-p webapptester-4ga -f examples/4gaboards/compose.yaml up -d"
)


def _new_values() -> dict[str, str]:
    """Return credentials without logging or exposing them to callers."""
    identifier = secrets.token_urlsafe(12)
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
        "SECRET_KEY": secrets.token_urlsafe(48),
        "DEFAULT_ADMIN_USERNAME": f"admin_{identifier}",
        "DEFAULT_ADMIN_EMAIL": f"admin-{identifier}@local.invalid",
        "DEFAULT_ADMIN_NAME": f"Local Admin {identifier}",
        "DEFAULT_ADMIN_PASSWORD": secrets.token_urlsafe(32),
    }


def _env_contents(values: dict[str, str]) -> str:
    return "".join(f"{name}={values[name]}\n" for name in ENV_VARIABLES)


def create_env_file(root: Path | str | None = None) -> Path:
    """Create the instance env file once and return its path.

    Creation uses ``O_EXCL`` so concurrent setup commands cannot replace an
    existing credential file. Repeated invocations retain the original bytes.
    """
    project_root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    env_path = project_root / ENV_RELATIVE_PATH
    env_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            env_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        os.chmod(env_path, 0o600)
        return env_path

    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_env_contents(_new_values()))
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
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    create_env_file(args.root)
    print(DOCKER_COMMAND)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
