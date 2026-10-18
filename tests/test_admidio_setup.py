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


def _native_module(monkeypatch):
    scripts = Path(__file__).parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    path = scripts / "setup_admidio_native.py"
    spec = importlib.util.spec_from_file_location("setup_admidio_native", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_setup_refuses_partial_existing_database(tmp_path: Path, monkeypatch) -> None:
    module = _native_module(monkeypatch)
    app = tmp_path / "runs" / "admidio-local" / "app" / "vendor"
    app.mkdir(parents=True)
    (app / "autoload.php").write_text("<?php")
    queries: list[str] = []

    def fake_mysql(query: str, *, input_text: str | None = None) -> str:
        queries.append(query)
        return module.DB_NAME if len(queries) == 1 else ""

    monkeypatch.setattr(module, "_mysql", fake_mysql)
    with pytest.raises(RuntimeError, match="inconsistent existing state"):
        module.prepare_native(tmp_path)
    assert len(queries) == 2
    assert not (tmp_path / "runs" / "admidio-local" / "admin.env").exists()


def test_native_setup_rejects_admin_symlink_before_database_work(tmp_path: Path, monkeypatch) -> None:
    module = _native_module(monkeypatch)
    app = tmp_path / "runs" / "admidio-local" / "app" / "vendor"
    app.mkdir(parents=True)
    (app / "autoload.php").write_text("<?php")
    target = tmp_path / "unrelated.txt"
    target.write_text("keep")
    (tmp_path / "runs" / "admidio-local" / "admin.env").symlink_to(target)
    monkeypatch.setattr(module, "_mysql", lambda *args, **kwargs: pytest.fail("database was touched"))

    with pytest.raises(ValueError, match="not a regular file"):
        module.prepare_native(tmp_path)
    assert target.read_text() == "keep"


def test_auth_bootstrap_rejects_state_symlink_before_browser(tmp_path: Path, monkeypatch) -> None:
    path = Path(__file__).parents[1] / "scripts" / "bootstrap_admidio_auth.py"
    spec = importlib.util.spec_from_file_location("bootstrap_admidio_auth", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    admin = tmp_path / "admin.env"
    admin.write_text("ADMIDIO_ADMIN_USER=test\nADMIDIO_ADMIN_PASSWORD=private\n")
    target = tmp_path / "unrelated.txt"
    target.write_text("keep")
    state = tmp_path / "auth.json"
    state.symlink_to(target)
    monkeypatch.setattr(module, "ADMIN_ENV", admin)
    monkeypatch.setattr(module, "AUTH_STATE", state)

    with pytest.raises(ValueError, match="not a regular file"):
        module.bootstrap_auth()
    assert target.read_text() == "keep"
