"""M5-01 数据库连接：连接串只从环境变量或项目根 .env 读取。

原则与 casetrace.env 一致：凭据不写入源码、日志或运行产物；错误信息只报
环境变量名，不回显连接串。本模块不做连接池或异步改造——当前九案展示范围
用同步短连接足够，不为生产并发提前拆层。
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from urllib.parse import quote

import psycopg

from casetrace.env import load_local_env

DATABASE_URL_ENV = "CASETRACE_DATABASE_URL"
TEST_DATABASE_URL_ENV = "CASETRACE_TEST_DATABASE_URL"


def resolve_database_url(
    *, test: bool = False, env: Mapping[str, str] | None = None,
) -> str:
    """宿主取显式 URL；容器由单一应用密码构造内部地址。

    `env` 只供测试注入；为 None 时先调用 `load_local_env()`（不覆盖已存在的
    环境变量），再读 `os.environ`。取不到就明确报错，不提供默认凭据。
    `CASETRACE_CONTAINER=1` 时忽略宿主及旧容器 URL；测试始终使用显式测试 URL。
    """

    if env is None:
        load_local_env()
        source: Mapping[str, str] = os.environ
    else:
        source = env
    if not test and source.get("CASETRACE_CONTAINER") == "1":
        password = source.get("CASETRACE_POSTGRES_PASSWORD")
        if not isinstance(password, str) or not password.strip():
            raise RuntimeError("缺少容器数据库密码：请设置 CASETRACE_POSTGRES_PASSWORD")
        return f"postgresql://casetrace:{quote(password, safe='')}@postgres:5432/casetrace"
    key = TEST_DATABASE_URL_ENV if test else DATABASE_URL_ENV
    url = source.get(key)
    if not isinstance(url, str) or not url.strip():
        raise RuntimeError(f"缺少数据库连接串：请设置环境变量 {key}（或写入项目根 .env）")
    return url


@contextmanager
def connect(
    *, test: bool = False, autocommit: bool = False,
    env: Mapping[str, str] | None = None,
) -> Iterator[psycopg.Connection]:
    """按环境变量建立连接；正常退出提交，异常退出回滚。

    psycopg 的连接上下文管理器已在退出时提交/回滚，这里只是补上连接串解析
    与可选的 autocommit（schema 初始化与测试用）。
    """

    url = resolve_database_url(test=test, env=env)
    with psycopg.connect(url, connect_timeout=10) as connection:
        connection.autocommit = autocommit
        yield connection
