"""数据库连接来源通过公开解析入口验证，不建立网络连接。"""

import pytest

from casetrace.storage.connection import resolve_database_url


def test_container_uses_single_password_and_encodes_reserved_characters():
    assert resolve_database_url(env={
        "CASETRACE_CONTAINER": "1",
        "CASETRACE_POSTGRES_PASSWORD": "a@b:c/d?#%雪",
        "CASETRACE_DATABASE_URL": "postgresql://stale-host/development",
        "CASETRACE_CONTAINER_DATABASE_URL": "postgresql://stale-container/old",
    }) == "postgresql://casetrace:a%40b%3Ac%2Fd%3F%23%25%E9%9B%AA@postgres:5432/casetrace"


def test_container_requires_app_password_without_echoing_credentials():
    with pytest.raises(RuntimeError, match="CASETRACE_POSTGRES_PASSWORD") as error:
        resolve_database_url(env={
            "CASETRACE_CONTAINER": "1",
            "CASETRACE_DATABASE_URL": "postgresql://secret-password@old/database",
        })
    assert "secret-password" not in str(error.value)


def test_test_database_keeps_explicit_host_url_even_in_container():
    assert resolve_database_url(test=True, env={
        "CASETRACE_CONTAINER": "1",
        "CASETRACE_TEST_DATABASE_URL": "postgresql://test@127.0.0.1/tests",
    }) == "postgresql://test@127.0.0.1/tests"
