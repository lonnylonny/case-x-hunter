"""独立于资料快照的实例设置；读写失败由 HTTP 边界明确报告。"""

from psycopg import Connection, sql

from casetrace.settings import RuntimeSettings
from casetrace.storage.schema import DEFAULT_SCHEMA, check_schema_name


def load_settings(connection: Connection, *, schema: str = DEFAULT_SCHEMA) -> RuntimeSettings:
    table = sql.Identifier(check_schema_name(schema), "runtime_settings")
    row = connection.execute(sql.SQL(
        "SELECT top_k, max_tokens FROM {} WHERE singleton = TRUE"
    ).format(table)).fetchone()
    if row is None:
        raise RuntimeError("实例设置尚未初始化，请运行 storage prepare")
    return RuntimeSettings(top_k=row[0], max_tokens=row[1])


def save_settings(connection: Connection, settings: RuntimeSettings, *,
                  schema: str = DEFAULT_SCHEMA) -> RuntimeSettings:
    table = sql.Identifier(check_schema_name(schema), "runtime_settings")
    row = connection.execute(sql.SQL(
        "UPDATE {} SET top_k = %s, max_tokens = %s WHERE singleton = TRUE "
        "RETURNING top_k, max_tokens"
    ).format(table), (settings.top_k, settings.max_tokens)).fetchone()
    if row is None:
        raise RuntimeError("实例设置尚未初始化，请运行 storage prepare")
    return RuntimeSettings(top_k=row[0], max_tokens=row[1])
