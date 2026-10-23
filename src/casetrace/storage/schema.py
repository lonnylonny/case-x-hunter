"""用一份固定 SQL 建立 CaseTrace 专用 schema，不提供通用迁移框架。"""

from __future__ import annotations

import re
from pathlib import Path

from psycopg import Connection, sql

# 保留已经导入的开发/测试库的结构标记；新库直接建立完整结构。
SCHEMA_VERSION = "002"
SCHEMA_SQL_PATH = Path(__file__).with_name("sql") / "schema.sql"
DEFAULT_SCHEMA = "casetrace"

# 冻结结构 §5 的 15 张表（顺序与文档一致，供核对与报告使用）。
FROZEN_TABLES = (
    "customers", "product_families", "package_routes", "products",
    "failure_modes", "failure_mode_routes", "processes", "package_process_map",
    "cases", "case_abnormal_processes", "case_details", "detail_failure_modes",
    "evidence_checkpoints", "case_groups", "case_group_memberships",
)
# 辅助表：结构版本标记与快照元数据（任务包 §6 允许一张辅助快照元数据表）。
AUXILIARY_TABLES = ("schema_version", "snapshot_metadata")

_SCHEMA_NAME_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def check_schema_name(name: str) -> str:
    """校验专用 schema 名；非法名字直接拒绝，保持命令行和测试使用同一命名约定。"""

    if not isinstance(name, str) or not _SCHEMA_NAME_PATTERN.match(name):
        raise ValueError(
            f"schema 名必须是 1-63 位小写字母/数字/下划线且不以数字开头：{name!r}"
        )
    return name


def schema_version(connection: Connection, *, schema: str = DEFAULT_SCHEMA) -> str | None:
    """读取当前结构标记；尚未初始化时返回 None。"""
    name = check_schema_name(schema)
    exists = connection.execute(
        "SELECT to_regclass(%s)", (f"{name}.schema_version",),
    ).fetchone()[0]
    if exists is None:
        return None
    return connection.execute(
        sql.SQL("SELECT max(version) FROM {}").format(sql.Identifier(name, "schema_version"))
    ).fetchone()[0]


def list_tables(connection: Connection, *, schema: str = DEFAULT_SCHEMA) -> list[str]:
    """列出专用 schema 中的表，供命令输出和集成测试使用。"""
    name = check_schema_name(schema)
    return [row[0] for row in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = %s ORDER BY tablename", (name,),
    ).fetchall()]


def initialize_schema(connection: Connection, *, schema: str = DEFAULT_SCHEMA) -> dict:
    """原子建表；重复执行保留数据，结构不匹配时明确报错。"""
    name = check_schema_name(schema)
    with connection.transaction():
        # 空实例还没有元数据表可锁；同一 schema 的首次初始化需在建表前排队。
        # 事务锁在 prepare 的外层事务结束后释放，覆盖初始化到导入的整个边界。
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"casetrace.initialize_schema:{name}",),
        )
        existing = schema_version(connection, schema=name)
        if existing is not None and existing != SCHEMA_VERSION:
            raise RuntimeError(
                f"schema {name} 的结构版本为 {existing}，当前需要 {SCHEMA_VERSION}；"
                "请使用匹配的代码或在独立 schema 中重新初始化"
            )
        connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(name)))
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))
        connection.execute(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        connection.execute(
            "INSERT INTO schema_version (version, description) VALUES (%s, %s) "
            "ON CONFLICT (version) DO NOTHING",
            (SCHEMA_VERSION, "M5-01 完整存取结构"),
        )
    return {"schema": name, "version": SCHEMA_VERSION,
            "tables": list_tables(connection, schema=name)}
