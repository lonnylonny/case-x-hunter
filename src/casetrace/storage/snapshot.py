"""单快照存取：一次主数据读取、原子写入、一次读回内容检查。

实体来自关系表，元数据记录来源、可用时点和源顺序。
重复导入与 verify 复用读回检查，不另建一套数据库核查逻辑。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
from pathlib import Path
from typing import Protocol

from psycopg import Connection, sql
from psycopg.types.json import Jsonb

from casetrace.data.dataset_model import Case, CaseDetail, CaseGroup, EvidenceCheckpoint, Membership
from casetrace.data.reference import ReferenceData
from casetrace.demo import check_source_records, load_validated_dataset
from casetrace.storage.content import (
    CONTENT_DIGEST_VERSION, ENTITY_PAYLOAD_KEYS, ENTITY_TABLES, IMPORT_ORDER,
    INSERT_COLUMNS, REFERENCE_TABLES, build_content, content_digest, normalize,
)
from casetrace.storage.reference_source import TABLE_KEYS, build_reference_data, build_reference_rows
from casetrace.storage.schema import (
    DEFAULT_SCHEMA, SCHEMA_VERSION, check_schema_name, initialize_schema, schema_version,
)

SNAPSHOT_METADATA_COLUMNS = (
    "snapshot_id", "known_at", "dataset_path", "dataset_sha256", "reference_path",
    "reference_sha256", "split", "review_status", "basis", "payload", "ordering",
    "content_digest", "digest_version",
)
SNAPSHOT_METADATA_READ_COLUMNS = (*SNAPSHOT_METADATA_COLUMNS, "imported_at")

ORDERED_TABLE_KEYS = {
    "cases": ("case_id",), "case_details": ("detail_id",),
    "evidence_checkpoints": ("checkpoint_id",), "case_groups": ("group_id",),
    "case_group_memberships": ("group_id", "case_id"),
    "case_abnormal_processes": ("case_id", "process_id"),
    "detail_failure_modes": ("detail_id", "failure_mode_id"),
}
IDENTITY_FIELDS = ("snapshot_id", "known_at", "dataset_sha256", "reference_sha256", "basis")

class SnapshotIdentity(Protocol):
    """导入需要的快照身份字段；`casetrace.answer.context.CorpusSnapshot` 结构上满足它。"""

    snapshot_id: str
    known_at: date
    dataset_sha256: str
    reference_sha256: str
    basis: str


@dataclass(frozen=True)
class ImportResult:
    """一次导入或准备的结果；状态为 imported、no_op 或 preserved。"""

    status: str
    snapshot_id: str
    schema: str
    content_digest: str
    digest_version: str
    counts: dict[str, int]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StoredSnapshot:
    """库内快照的描述；哈希与可用时点依据原样保留。"""

    snapshot_id: str
    known_at: date
    dataset_path: str
    dataset_sha256: str
    reference_path: str
    reference_sha256: str
    split: str
    review_status: str
    basis: str
    content_digest: str
    digest_version: str
    imported_at: datetime


@dataclass(frozen=True)
class LoadedSnapshot:
    """一次读回的结果：五类实体、等价主数据、完整 payload 与快照描述。"""

    records: dict
    reference: ReferenceData
    payload: dict
    snapshot: StoredSnapshot
    counts: dict[str, int]


@dataclass(frozen=True)
class VerificationReport:
    """`verify_snapshot` 的结论；`checks` 逐项给出通过与否。"""

    ok: bool
    snapshot_id: str
    schema: str
    content_digest: str
    digest_version: str
    counts: dict[str, int]
    checks: dict[str, bool]
    problems: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"源文件不存在：{path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_file_identity(snapshot: SnapshotIdentity, dataset_sha256: str, reference_sha256: str) -> None:
    """源文件必须就是快照记录的那一份；不一致时明确拒绝，不导入近似内容。"""

    if dataset_sha256 != snapshot.dataset_sha256:
        raise ValueError(
            f"语料文件与已记录快照不一致 | 快照 {snapshot.dataset_sha256} | 实际 {dataset_sha256}"
        )
    if reference_sha256 != snapshot.reference_sha256:
        raise ValueError(
            f"主数据文件与已记录快照不一致 | 快照 {snapshot.reference_sha256} | 实际 {reference_sha256}"
        )


def entity_rows(records: dict) -> dict[str, list[dict]]:
    """展开 dataclass；两个多选字段单独写到关系表，其余字段保持原样。"""
    rows = {
        table: [asdict(item) for item in records[key]]
        for key, table in (
            ("cases", "cases"), ("details", "case_details"),
            ("evidences", "evidence_checkpoints"), ("groups", "case_groups"),
            ("memberships", "case_group_memberships"),
        )
    }
    rows["case_abnormal_processes"] = [
        {"case_id": row["case_id"], "process_id": process_id}
        for row in rows["cases"] for process_id in row.pop("abnormal_processes")
    ]
    rows["detail_failure_modes"] = [
        {"detail_id": row["detail_id"], "failure_mode_id": mode_id}
        for row in rows["case_details"] for mode_id in row.pop("abnormal_types")
    ]
    return rows


def build_ordering(entities: dict[str, list[dict]], route_order: dict[str, list[str]]) -> dict:
    """记录源列表与关系列表顺序；数据库表本身不保证返回顺序。

    `route_order` 是 `failure_mode_routes` 的源顺序，用于按原顺序重建
    `ReferenceData.failure_modes[*]["applicable_package"]` 这个冗余字符串。
    """

    return {
        "cases": [row["case_id"] for row in entities["cases"]],
        "case_details": [row["detail_id"] for row in entities["case_details"]],
        "evidence_checkpoints": [row["checkpoint_id"] for row in entities["evidence_checkpoints"]],
        "case_groups": [row["group_id"] for row in entities["case_groups"]],
        "case_group_memberships": [
            [row["group_id"], row["case_id"]] for row in entities["case_group_memberships"]
        ],
        "case_abnormal_processes": _child_map(
            entities["case_abnormal_processes"], "case_id", "process_id"
        ),
        "detail_failure_modes": _child_map(
            entities["detail_failure_modes"], "detail_id", "failure_mode_id"
        ),
        "failure_mode_routes": {
            mode_id: list(routes) for mode_id, routes in route_order.items()
        },
    }


def non_entity_payload(payload: dict) -> dict:
    """取出非实体 payload：split、review_status、来源、Query 示例、版本与家族信息。

    qrels 与其理由不在此列，因此不会进入数据库。
    """

    return {
        key: value for key, value in payload.items() if key not in ENTITY_PAYLOAD_KEYS
    }


def _snapshot_fields(
    snapshot: SnapshotIdentity, dataset_sha256: str, reference_sha256: str, payload: dict,
) -> dict:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "known_at": snapshot.known_at,
        "basis": snapshot.basis,
        "split": payload["split"],
        "review_status": payload["review_status"],
        "dataset_sha256": dataset_sha256,
        "reference_sha256": reference_sha256,
    }


def _insert_rows(connection: Connection, schema: str, table: str, rows: list[dict]) -> None:
    columns = INSERT_COLUMNS[table]
    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(schema, table),
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.SQL(", ").join([sql.Placeholder()] * len(columns)),
    )
    with connection.cursor() as cursor:
        cursor.executemany(
            statement, [tuple(row[column] for column in columns) for row in rows]
        )


def table_counts(connection: Connection, schema: str) -> dict[str, int]:
    return {
        table: connection.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, table))
        ).fetchone()[0]
        for table in IMPORT_ORDER
    }


def _insert_snapshot(
    connection: Connection, schema: str, snapshot: SnapshotIdentity,
    dataset_path: Path, dataset_sha256: str, reference_path: Path, reference_sha256: str,
    payload: dict, ordering: dict, digest: str,
) -> None:
    """写入快照元数据；JSONB 用 Jsonb 包装，日期、哈希与摘要按列分别保存。"""

    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(schema, "snapshot_metadata"),
        sql.SQL(", ").join(sql.Identifier(column) for column in SNAPSHOT_METADATA_COLUMNS),
        sql.SQL(", ").join([sql.Placeholder()] * len(SNAPSHOT_METADATA_COLUMNS)),
    )
    connection.execute(statement, (
        snapshot.snapshot_id, snapshot.known_at, str(dataset_path), dataset_sha256,
        str(reference_path), reference_sha256, payload["split"], payload["review_status"],
        snapshot.basis, Jsonb(payload), Jsonb(ordering), digest, CONTENT_DIGEST_VERSION,
    ))


def _read_rows(
    connection: Connection, schema: str, table: str, order_by: tuple[str, ...] = (),
) -> list[dict]:
    """按 INSERT_COLUMNS 读取整张表；读回的键与写入时的键完全一致。"""

    columns = INSERT_COLUMNS[table]
    statement = sql.SQL("SELECT {} FROM {}").format(
        sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        sql.Identifier(schema, table),
    )
    if order_by:
        statement += sql.SQL(" ORDER BY {}").format(
            sql.SQL(", ").join(sql.Identifier(column) for column in order_by)
        )
    return [dict(zip(columns, row)) for row in connection.execute(statement).fetchall()]


def _child_map(rows: list[dict], parent: str, child: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row[parent], []).append(row[child])
    return grouped


def _read_metadata(connection: Connection, schema: str) -> dict | None:
    rows = connection.execute(
        sql.SQL("SELECT {} FROM {}").format(
            sql.SQL(", ").join(map(sql.Identifier, SNAPSHOT_METADATA_READ_COLUMNS)),
            sql.Identifier(schema, "snapshot_metadata"),
        )
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("本版只支持单一快照，snapshot_metadata 不能有多行")
    return dict(zip(SNAPSHOT_METADATA_READ_COLUMNS, rows[0])) if rows else None


def _prepare_content(
    dataset_path: Path, reference_path: Path, snapshot: SnapshotIdentity,
    dataset_sha256: str, reference_sha256: str,
) -> dict:
    """同一份完整主数据生成校验投影和入库行，CR/GR/来源各执行一次。"""
    reference_rows, route_order = build_reference_rows(reference_path)
    reference = build_reference_data(reference_rows, route_order)
    records, payload, _ = load_validated_dataset(dataset_path, reference_path, reference=reference)
    check_source_records(records, payload, reference)
    entities = entity_rows(records)
    return build_content(
        snapshot_fields=_snapshot_fields(snapshot, dataset_sha256, reference_sha256, payload),
        reference=reference_rows, entities=entities, payload=non_entity_payload(payload),
        ordering=build_ordering(entities, route_order),
    )


def _content_counts(content: dict) -> dict[str, int]:
    rows = {**content["reference"], **content["entities"]}
    return {table: len(rows[table]) for table in IMPORT_ORDER}


def _restore_entities(raw_rows: dict[str, list[dict]], ordering: dict) -> dict[str, list[dict]]:
    """按源顺序取出每一行；主键唯一性由数据库保证。"""
    orders = {
        **ordering,
        "case_abnormal_processes": [
            (case_id, process_id) for case_id in ordering["cases"]
            for process_id in ordering["case_abnormal_processes"].get(case_id, [])
        ],
        "detail_failure_modes": [
            (detail_id, mode_id) for detail_id in ordering["case_details"]
            for mode_id in ordering["detail_failure_modes"].get(detail_id, [])
        ],
    }
    restored = {}
    for table, columns in ORDERED_TABLE_KEYS.items():
        by_key = {tuple(row[column] for column in columns): row for row in raw_rows[table]}
        keys = [(key,) if len(columns) == 1 else tuple(key) for key in orders[table]]
        try:
            restored[table] = [by_key.pop(key) for key in keys]
        except KeyError as error:
            raise ValueError(f"{table} 的行与源顺序记录不一致") from error
        if by_key:
            raise ValueError(f"{table} 有未出现在源顺序记录中的行")
    return restored


def _rebuild_records(entities: dict) -> dict:
    processes = _child_map(entities["case_abnormal_processes"], "case_id", "process_id")
    modes = _child_map(entities["detail_failure_modes"], "detail_id", "failure_mode_id")
    return {
        "cases": [Case(**row, abnormal_processes=processes.get(row["case_id"], []))
                  for row in entities["cases"]],
        "details": [CaseDetail(**row, abnormal_types=modes.get(row["detail_id"], []))
                    for row in entities["case_details"]],
        "evidences": [EvidenceCheckpoint(**row) for row in entities["evidence_checkpoints"]],
        "groups": [CaseGroup(**row) for row in entities["case_groups"]],
        "memberships": [Membership(**row) for row in entities["case_group_memberships"]],
    }


def _load_snapshot(connection: Connection, schema: str, meta: dict) -> LoadedSnapshot:
    """数据库内容检查集中在这里，导入 no-op 和公开读回共用。"""
    if meta["digest_version"] != CONTENT_DIGEST_VERSION:
        raise ValueError(f"库内摘要版本为 {meta['digest_version']}，当前为 {CONTENT_DIGEST_VERSION}")
    reference_rows = {
        table: _read_rows(connection, schema, table, TABLE_KEYS[table]) for table in REFERENCE_TABLES
    }
    entities = _restore_entities(
        {table: _read_rows(connection, schema, table) for table in ENTITY_TABLES}, meta["ordering"],
    )
    content = build_content(
        snapshot_fields={key: meta[key] for key in (*IDENTITY_FIELDS, "split", "review_status")},
        reference=reference_rows, entities=entities, payload=meta["payload"], ordering=meta["ordering"],
    )
    if content_digest(content) != meta["content_digest"]:
        raise ValueError("库内内容摘要不一致：数据库内容已改变，请核对数据来源")
    records = _rebuild_records(entities)
    return LoadedSnapshot(
        records=records, reference=build_reference_data(reference_rows, meta["ordering"]["failure_mode_routes"]),
        payload={**meta["payload"], **{
            key: [normalize(asdict(item)) for item in records[key]] for key in ENTITY_PAYLOAD_KEYS
        }},
        snapshot=StoredSnapshot(**{key: meta[key] for key in StoredSnapshot.__dataclass_fields__}),
        counts=_content_counts(content),
    )


def load_snapshot(connection: Connection, *, schema: str = DEFAULT_SCHEMA) -> LoadedSnapshot:
    """从关系表重建对象与源顺序，并核对一次完整内容摘要。"""
    name = check_schema_name(schema)
    meta = _read_metadata(connection, name)
    if meta is None:
        raise ValueError(f"schema {name} 没有已记录的快照；请先运行 python -m casetrace.storage import")
    return _load_snapshot(connection, name, meta)


def import_snapshot(
    connection: Connection, dataset_path: Path, reference_path: Path, *,
    snapshot: SnapshotIdentity, schema: str = DEFAULT_SCHEMA,
) -> ImportResult:
    """原子导入单快照；相同输入返回 no_op，冲突或失败保留原库。"""
    name = check_schema_name(schema)
    dataset_path, reference_path = Path(dataset_path), Path(reference_path)
    with connection.transaction():
        if schema_version(connection, schema=name) != SCHEMA_VERSION:
            raise ValueError(f"schema {name} 尚未初始化或版本不符；请先运行 python -m casetrace.storage init")
        # 锁住当前 schema 的元数据表；并发导入排队，不需要全局魔数锁。
        connection.execute(sql.SQL("LOCK TABLE {} IN EXCLUSIVE MODE").format(
            sql.Identifier(name, "snapshot_metadata"),
        ))
        dataset_sha256, reference_sha256 = file_sha256(dataset_path), file_sha256(reference_path)
        _check_file_identity(snapshot, dataset_sha256, reference_sha256)
        meta = _read_metadata(connection, name)
        if meta is not None:
            if any(meta[key] != getattr(snapshot, key) for key in IDENTITY_FIELDS):
                raise ValueError("已记录快照与本次导入不一致，拒绝并保留原库")
            try:
                loaded = _load_snapshot(connection, name, meta)
            except ValueError as error:
                raise ValueError(f"拒绝并保留原库：{error}") from error
            return ImportResult("no_op", snapshot.snapshot_id, name, loaded.snapshot.content_digest,
                                CONTENT_DIGEST_VERSION, loaded.counts)
        if any(table_counts(connection, name).values()):
            raise ValueError(f"schema {name} 已有业务数据但没有快照记录，拒绝导入")
        content = _prepare_content(dataset_path, reference_path, snapshot, dataset_sha256, reference_sha256)
        digest = content_digest(content)
        rows = {**content["reference"], **content["entities"]}
        for table in IMPORT_ORDER:
            if rows[table]:
                _insert_rows(connection, name, table, rows[table])
        _insert_snapshot(connection, name, snapshot, dataset_path, dataset_sha256,
                         reference_path, reference_sha256, content["payload"], content["ordering"], digest)
    return ImportResult("imported", snapshot.snapshot_id, name, digest,
                        CONTENT_DIGEST_VERSION, _content_counts(content))


def prepare_snapshot(
    connection: Connection, dataset_path: Path, reference_path: Path, *,
    snapshot: SnapshotIdentity, schema: str = DEFAULT_SCHEMA,
) -> ImportResult:
    """启动数据准备：仅为空库导入示例，已有快照按库内摘要核对并保留。"""
    name = check_schema_name(schema)
    with connection.transaction():
        initialize_schema(connection, schema=name)
        connection.execute(sql.SQL("LOCK TABLE {} IN EXCLUSIVE MODE").format(
            sql.Identifier(name, "snapshot_metadata"),
        ))
        meta = _read_metadata(connection, name)
        if meta is None:
            result = import_snapshot(
                connection, dataset_path, reference_path, snapshot=snapshot, schema=name,
            )
            load_snapshot(connection, schema=name)
            return result
        loaded = _load_snapshot(connection, name, meta)
        return ImportResult(
            "preserved", loaded.snapshot.snapshot_id, name,
            loaded.snapshot.content_digest, loaded.snapshot.digest_version, loaded.counts,
        )


def verify_snapshot(
    connection: Connection, *, dataset_path: Path, reference_path: Path,
    snapshot: SnapshotIdentity | None = None, schema: str = DEFAULT_SCHEMA,
) -> VerificationReport:
    """显式诊断：比较文件身份和完整内容摘要；逐实体和文档对照由集成测试覆盖。"""
    name = check_schema_name(schema)
    loaded = load_snapshot(connection, schema=name)
    dataset_sha256, reference_sha256 = file_sha256(dataset_path), file_sha256(reference_path)
    checks = {
        "file_identity": dataset_sha256 == loaded.snapshot.dataset_sha256
                         and reference_sha256 == loaded.snapshot.reference_sha256,
        "snapshot_identity": snapshot is None or all(
            getattr(snapshot, key) == getattr(loaded.snapshot, key) for key in IDENTITY_FIELDS
        ),
    }
    problems = []
    if not checks["file_identity"]:
        problems.append("源文件哈希与库内记录不一致")
    if not checks["snapshot_identity"]:
        problems.append("请求的快照身份（ID、可用时点或依据）与库内记录不一致")
    if checks["file_identity"]:
        content = _prepare_content(dataset_path, reference_path, loaded.snapshot,
                                   dataset_sha256, reference_sha256)
        checks["content"] = content_digest(content) == loaded.snapshot.content_digest
        if not checks["content"]:
            problems.append("完整内容与源文件不一致")
    return VerificationReport(
        ok=not problems, snapshot_id=loaded.snapshot.snapshot_id, schema=name,
        content_digest=loaded.snapshot.content_digest, digest_version=loaded.snapshot.digest_version,
        counts=loaded.counts, checks=checks, problems=problems,
    )
