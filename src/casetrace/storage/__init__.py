"""M5-01 存储层：把冻结的 CaseTrace 模型存取到真实 PostgreSQL。

对外公开的存取入口：

- ``initialize_schema(connection, schema=...)`` —— 建立/补齐专用 schema；
- ``import_snapshot(connection, dataset_path, reference_path)`` —— 原子导入单快照；
- ``load_snapshot(connection)`` —— 完整读回并核对内容身份。
- ``prepare_snapshot(connection, dataset_path, reference_path)`` —— 启动前导入或保留快照。

本包不接入回答命令、不实现 FastAPI，也不改动检索与生成代码；
表、字段与约束的权威来源是 docs/data/CaseTrace_Data_Structure_V2_No_Scenario.md §5。
"""

from casetrace.storage.connection import (
    DATABASE_URL_ENV,
    TEST_DATABASE_URL_ENV,
    connect,
    resolve_database_url,
)
from casetrace.storage.content import CONTENT_DIGEST_VERSION
from casetrace.storage.schema import (
    DEFAULT_SCHEMA,
    SCHEMA_VERSION,
    schema_version,
    initialize_schema,
    list_tables,
)
from casetrace.storage.snapshot import (
    ImportResult, LoadedSnapshot, SnapshotIdentity, StoredSnapshot, VerificationReport,
    import_snapshot, load_snapshot, prepare_snapshot, verify_snapshot,
)

__all__ = [
    "CONTENT_DIGEST_VERSION",
    "DATABASE_URL_ENV",
    "DEFAULT_SCHEMA",
    "ImportResult",
    "LoadedSnapshot",
    "SCHEMA_VERSION",
    "SnapshotIdentity",
    "StoredSnapshot",
    "TEST_DATABASE_URL_ENV",
    "VerificationReport",
    "schema_version",
    "connect",
    "import_snapshot",
    "initialize_schema",
    "list_tables",
    "load_snapshot",
    "prepare_snapshot",
    "resolve_database_url",
    "verify_snapshot",
]
