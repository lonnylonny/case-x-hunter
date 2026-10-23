"""通过 shell 入口与外部 Docker stub 验证启动结果，不需启动 Docker daemon。"""

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["bash", "powershell"])
def demo_launcher(tmp_path, request):
    if request.param == "powershell":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            pytest.skip("PowerShell 未安装，未实测")
        command = [powershell, "-NoProfile", "-File", str(ROOT / "scripts/start-demo.ps1")]
    else:
        command = ["bash", str(ROOT / "scripts/start-demo.sh")]
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
if [[ " $* " == *" config --environment "* ]]; then
    cat "$DOCKER_ENVIRONMENT"
fi
if [[ -n "${DOCKER_FAIL:-}" && " $* " == *" $DOCKER_FAIL "* ]]; then
    exit 17
fi
""")
    docker.chmod(0o755)
    calls = tmp_path / "calls"
    effective = tmp_path / "effective.env"
    effective.write_text("CASETRACE_POSTGRES_ADMIN_PASSWORD=admin-secret\n"
                         "CASETRACE_POSTGRES_PASSWORD=app-secret\n"
                         "CASETRACE_API_PORT=8123\n")
    env_file = tmp_path / "demo.env"
    base_env = {key: value for key, value in os.environ.items()
                if not key.startswith(("CASETRACE_", "COMPOSE_"))}
    base_env.update(PATH=f"{binary}:{os.environ['PATH']}", DOCKER_CALLS=str(calls),
                    DOCKER_ENVIRONMENT=str(effective))

    def launch(*, failure=""):
        return subprocess.run(
            [*command, str(env_file)],
            cwd=tmp_path, env=base_env | {"DOCKER_FAIL": failure}, text=True,
            capture_output=True,
        )

    return launch, env_file, effective, calls


def test_new_configuration_needs_no_host_python_and_prints_effective_port(demo_launcher):
    launch, env_file, _, calls = demo_launcher
    result = launch()
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
                  if line and not line.startswith("#"))
    admin = values["CASETRACE_POSTGRES_ADMIN_PASSWORD"]
    app = values["CASETRACE_POSTGRES_PASSWORD"]
    assert re.fullmatch("[0-9a-f]{48}", admin)
    assert re.fullmatch("[0-9a-f]{48}", app)
    assert admin != app
    assert values["DEEPSEEK_API_KEY"] == ""
    assert "http://127.0.0.1:8123" in result.stdout
    assert admin not in result.stdout + result.stderr
    assert app not in result.stdout + result.stderr
    assert "run --rm --no-deps prepare" in calls.read_text()
    assert "up -d --wait api" in calls.read_text()


def test_existing_configuration_is_preserved_and_never_executed(demo_launcher, tmp_path):
    launch, env_file, _, _ = demo_launcher
    original = "DEEPSEEK_API_KEY=private-model-key\nMALICIOUS=$(touch NEVER_EXECUTE)\n"
    env_file.write_text(original)
    result = launch()
    assert result.returncode == 0, result.stderr
    assert env_file.read_text() == original
    assert not (tmp_path / "NEVER_EXECUTE").exists()
    assert "private-model-key" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure, hint", [
    ("version", "Compose"), ("info", "daemon"), ("config", "CASETRACE_POSTGRES_PASSWORD"),
    ("build", "构建"), ("postgres", "PostgreSQL"), ("prepare", "数据准备"),
    ("api", "API"),
])
def test_external_failure_returns_nonzero_and_never_reports_success(demo_launcher, failure, hint):
    launch, _, _, _ = demo_launcher
    result = launch(failure=failure)
    assert result.returncode != 0
    assert hint in result.stderr
    assert "启动完成" not in result.stdout
    assert "诊断命令" in result.stderr
    assert "--tail" in result.stderr


@pytest.mark.parametrize("line, key", [
    ("CASETRACE_POSTGRES_PASSWORD=replace_with_random_app_password", "CASETRACE_POSTGRES_PASSWORD"),
    ("CASETRACE_POSTGRES_ADMIN_PASSWORD=", "CASETRACE_POSTGRES_ADMIN_PASSWORD"),
    ("CASETRACE_API_PORT=70000", "CASETRACE_API_PORT"),
])
def test_invalid_effective_configuration_fails_before_build(demo_launcher, line, key):
    launch, _, effective, calls = demo_launcher
    effective.write_text(effective.read_text() + line + "\n")
    result = launch()
    assert result.returncode != 0
    assert key in result.stderr
    assert " build api" not in calls.read_text()
    assert "app-secret" not in result.stderr
    assert "admin-secret" not in result.stderr
