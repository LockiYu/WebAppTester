import importlib.util
import stat
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "setup_admidio_local.py"


def _setup_module():
    spec = importlib.util.spec_from_file_location("setup_admidio_local", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_admidio_setup_creates_private_distinct_credentials(tmp_path: Path) -> None:
    env_path = _setup_module().create_env_file(tmp_path)
    values = dict(line.split("=", 1) for line in env_path.read_text().splitlines())

    assert set(values) == {"ADMIDIO_DB_ROOT_PASSWORD", "ADMIDIO_DB_PASSWORD"}
    assert values["ADMIDIO_DB_ROOT_PASSWORD"] != values["ADMIDIO_DB_PASSWORD"]
    assert all(len(value) >= 32 for value in values.values())
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_admidio_setup_does_not_replace_credentials(tmp_path: Path) -> None:
    module = _setup_module()
    env_path = module.create_env_file(tmp_path)
    original = env_path.read_bytes()

    assert module.create_env_file(tmp_path) == env_path
    assert env_path.read_bytes() == original


def test_admidio_setup_rejects_existing_symlink(tmp_path: Path) -> None:
    env_path = tmp_path / "runs" / "admidio-local" / "instance.env"
    env_path.parent.mkdir(parents=True)
    target = tmp_path / "unrelated.txt"
    target.write_text("keep")
    env_path.symlink_to(target)

    with pytest.raises(ValueError, match="not a regular file"):
        _setup_module().create_env_file(tmp_path)
    assert target.read_text() == "keep"
