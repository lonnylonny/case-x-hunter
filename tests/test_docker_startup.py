"""用 Compose 自身解析配置，检查启动依赖及单一数据库凭据接线。"""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_api_waits_for_data_preparation_with_one_password(tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("未安装 Docker CLI，跳过 Compose 配置检查")
    env_file = tmp_path / "compose.env"
    env_file.write_text(
        "CASETRACE_POSTGRES_ADMIN_PASSWORD=synthetic-admin\n"
        "CASETRACE_POSTGRES_PASSWORD=synthetic-app\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        ["docker", "compose", "--env-file", str(env_file), "-f", str(ROOT / "compose.yaml"),
         "config", "--format", "json"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    services = json.loads(result.stdout)["services"]
    assert services["api"]["depends_on"]["prepare"]["condition"] == "service_completed_successfully"
    assert services["prepare"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["prepare"]["command"] == ["python", "-m", "casetrace.storage", "prepare"]
    for name in ("prepare", "api"):
        assert services[name]["environment"]["CASETRACE_CONTAINER"] == "1"
        assert services[name]["environment"]["CASETRACE_POSTGRES_PASSWORD"] == "synthetic-app"
        assert "CASETRACE_DATABASE_URL" not in services[name]["environment"]
        assert services[name]["image"] == "casetrace-api:dev"
