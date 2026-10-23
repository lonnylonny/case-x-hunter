"""SD1 存储集成测试夹具：只用真实 PostgreSQL，测试各自独占一个 schema。

按任务包「测试接缝与验收」：不得用 SQLite 代替 PostgreSQL；测试清理仅限自己
创建的对象；没有连接时显式 skip，但 skip 不能作为本包验收通过证据。
"""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg import sql

from casetrace.env import load_local_env
from casetrace.storage.connection import TEST_DATABASE_URL_ENV


@pytest.fixture(autouse=True)
def forbid_live_models(monkeypatch):
    """存储回归只用模型回放；替身接入出错也不能调用付费服务。"""
    def forbidden(*args, **kwargs):
        raise AssertionError("数据库测试禁止构造真实模型客户端")
    monkeypatch.setattr("casetrace.answer.model._build_client", forbidden)


@pytest.fixture(autouse=True)
def _restore_env_after_test():
    """load_local_env 会写 os.environ，测试后恢复，避免污染其它测试模块。

    与 tests/test_env.py 的约定一致：本模块的集成测试也会加载项目根 .env，
    若不恢复，后面的模块会看到本机真实凭据键。
    """

    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def storage_connection():
    """连接真实测试库；缺少连接串或连不上时显式 skip，不静默改用其它数据库。"""

    load_local_env()
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url or not url.strip():
        pytest.skip(f"未设置 {TEST_DATABASE_URL_ENV}，跳过真实 PostgreSQL 集成测试")
    try:
        connection = psycopg.connect(url, connect_timeout=5)
    except psycopg.OperationalError as error:
        pytest.skip(f"无法连接测试数据库（{type(error).__name__}），跳过集成测试")
    connection.autocommit = True
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def scratch_schema(storage_connection):
    """每个测试独占一个唯一 schema；结束后只删除自己创建的这个 schema。"""

    name = f"casetrace_test_{uuid.uuid4().hex[:8]}"
    try:
        yield name
    finally:
        storage_connection.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name))
        )
