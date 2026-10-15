import importlib.util
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT_PATH = ROOT / "scripts" / "setup_4gaboards_local.py"
COMPOSE_PATH = ROOT / "examples" / "4gaboards" / "compose.yaml"
REQUIRED_VARIABLES = {
    "POSTGRES_PASSWORD",
    "SECRET_KEY",
    "DEFAULT_ADMIN_USERNAME",
    "DEFAULT_ADMIN_EMAIL",
    "DEFAULT_ADMIN_NAME",
    "DEFAULT_ADMIN_PASSWORD",
}


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("setup_4gaboards_local", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def test_setup_creates_private_env_with_required_nonempty_values(tmp_path: Path) -> None:
    module = _load_setup_module()

    env_path = module.create_env_file(tmp_path)

    assert env_path == tmp_path / "runs" / "4gaboards-local" / "instance.env"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    values = _parse_env(env_path)
    assert set(values) == REQUIRED_VARIABLES
    assert all(values.values())


def test_setup_preserves_existing_env_values_on_repeat(tmp_path: Path) -> None:
    module = _load_setup_module()

    env_path = module.create_env_file(tmp_path)
    original = env_path.read_bytes()

    repeated_path = module.create_env_file(tmp_path)

    assert repeated_path == env_path
    assert env_path.read_bytes() == original
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_setup_command_does_not_print_secret_values(tmp_path: Path) -> None:
    env_path = tmp_path / "runs" / "4gaboards-local" / "instance.env"
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    values = _parse_env(env_path)
    output = result.stdout + result.stderr
    assert output.strip() == (
        "docker compose --env-file runs/4gaboards-local/instance.env "
        "-p webapptester-4ga -f examples/4gaboards/compose.yaml up -d"
    )
    assert all(value not in output for value in values.values())


def test_compose_uses_isolated_loopback_port_and_distinct_volumes() -> None:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "image: public.ecr.aws/docker/library/postgres:16-alpine" in compose_text
    assert "image: ghcr.io/rargames/4gaboards:3.2.16" in compose_text
    assert '"127.0.0.1:3001:1337"' in compose_text
    assert "127.0.0.1:3000" not in compose_text

    service_block, volumes_block = compose_text.split("\nvolumes:\n", 1)
    declared_volumes = set(re.findall(r"^  ([a-z0-9_]+):$", volumes_block, re.MULTILINE))
    service_volumes = set(re.findall(r"^      - ([a-z0-9_]+):", service_block, re.MULTILINE))
    assert declared_volumes
    assert service_volumes <= declared_volumes
    assert len(service_volumes) >= 2


def test_compose_reads_secrets_from_environment_interpolation() -> None:
    compose_text = COMPOSE_PATH.read_text(encoding="utf-8")

    for variable in REQUIRED_VARIABLES:
        assert f"${{{variable}" in compose_text
    assert "notpassword" not in compose_text
    assert "notsecretkey" not in compose_text
    assert compose_text.count("healthcheck:") >= 2
