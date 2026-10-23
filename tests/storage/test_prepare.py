"""启动数据准备：空库导入，后续只验证并保留当前快照。"""

from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import threading

import psycopg
import pytest
from psycopg import sql

from casetrace.storage.__main__ import build_parser, main
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.storage.snapshot import load_snapshot, prepare_snapshot
from casetrace.storage import import_snapshot, initialize_schema
from casetrace.storage.connection import resolve_database_url
from casetrace.storage.schema import schema_version


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "data/dev/demo-v3.json"
REFERENCE = PROJECT_ROOT / (
    "data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx"
)


def test_prepare_command_is_available_for_container_startup():
    args = build_parser().parse_args(["prepare", "--schema", "demo_startup"])

    assert args.command == "prepare"
    assert args.schema == "demo_startup"


def test_prepare_initializes_and_imports_example(storage_connection, scratch_schema):
    result = prepare_snapshot(
        storage_connection, DATASET, REFERENCE,
        snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema,
    )

    loaded = load_snapshot(storage_connection, schema=scratch_schema)
    assert result.status == "imported"
    assert loaded.snapshot.snapshot_id == "dev-v3-2026-09-15"
    assert [case.case_id for case in loaded.records["cases"]] == [
        "C001", "C002", "C003", "C004", "C005", "C006", "C007", "C008", "C009",
    ]


def test_prepare_command_reports_ready_snapshot(storage_connection, scratch_schema, capsys):
    result = main(["prepare", "--test", "--schema", scratch_schema,
                   "--dataset", str(DATASET), "--reference", str(REFERENCE)])

    captured = capsys.readouterr()
    assert result == 0
    assert "imported" in captured.out
    assert load_snapshot(storage_connection, schema=scratch_schema).snapshot.snapshot_id == "dev-v3-2026-09-15"


def test_prepare_preserves_existing_snapshot_without_reading_example(
    storage_connection, scratch_schema, tmp_path,
):
    prepare_snapshot(storage_connection, DATASET, REFERENCE,
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    before = load_snapshot(storage_connection, schema=scratch_schema)

    result = prepare_snapshot(
        storage_connection, tmp_path / "missing.json", tmp_path / "missing.xlsx",
        snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema,
    )

    after = load_snapshot(storage_connection, schema=scratch_schema)
    assert result.status == "preserved"
    assert after == before


def test_prepare_preserves_valid_non_default_snapshot(
    storage_connection, scratch_schema, tmp_path,
):
    source = {
        "sheet": "failure_modes", "failure_mode_id": "00010",
        "root_cause": "die pad或lead/substrate bond finger污染/氧化",
        "corrective_action": "加强wafer pad和载体bond finger来料/清洁管控",
        "closure_status": "confirmed",
    }
    payload = {
        "split": "development", "review_status": "draft_pending_human_review",
        "cases": [{
            "case_id": f"C90{number}", "abnormal_description": "合成焊线脱落案例",
            "root_cause": f"结案确认：{source['root_cause']}。",
            "corrective_action": f"{source['corrective_action']}。",
            "investigation_others": None, "abnormal_processes": ["P004"],
        } for number in (1, 2)],
        "details": [{
            "detail_id": f"D90{number}", "case_id": f"C90{number}",
            "product_id": "PROD_001", "customer_lot": "SYN_CL_901",
            "production_lot": "SYN_PL_901", "production_time": "2026-06-01",
            "detection_stage": "OQC", "detection_time": "2026-06-03",
            "abnormal_types": ["00010"], "affected_qty": 30,
            "disposition": "纳入处置范围的产品隔离。",
        } for number in (1, 2)],
        "evidences": [{
            "checkpoint_id": f"E90{number}", "case_id": f"C90{number}",
            "checkpoint_type": "QC", "custom_name": None,
            "result": "观察到焊点剥离", "relevance": "related",
        } for number in (1, 2)],
        "groups": [], "memberships": [],
        "sources": {"C901": source, "C902": source},
        "queries": [{"query_id": "Q901", "known_at": "2026-09-15", "text": "焊线脱落"}],
    }
    dataset = tmp_path / "synthetic.json"
    dataset.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    snapshot = SimpleNamespace(
        snapshot_id="synthetic-startup", known_at=DEV_V3_SNAPSHOT.known_at,
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
        reference_sha256=DEV_V3_SNAPSHOT.reference_sha256, basis="仅用于启动保留测试",
    )
    initialize_schema(storage_connection, schema=scratch_schema)
    import_snapshot(storage_connection, dataset, REFERENCE,
                    snapshot=snapshot, schema=scratch_schema)
    before = load_snapshot(storage_connection, schema=scratch_schema)

    result = prepare_snapshot(storage_connection, DATASET, REFERENCE,
                              snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)

    after = load_snapshot(storage_connection, schema=scratch_schema)
    assert result.status == "preserved"
    assert result.snapshot_id == "synthetic-startup"
    assert [case.case_id for case in after.records["cases"]] == ["C901", "C902"]
    assert after == before


def test_prepare_refuses_orphaned_data(storage_connection, scratch_schema):
    initialize_schema(storage_connection, schema=scratch_schema)
    storage_connection.execute(
        sql.SQL("INSERT INTO {} (customer_id, customer_name) VALUES ('CUS_ORPHAN', '手工客户')")
        .format(sql.Identifier(scratch_schema, "customers")),
    )

    with pytest.raises(ValueError, match="已有业务数据但没有快照记录"):
        prepare_snapshot(storage_connection, DATASET, REFERENCE,
                         snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)

    with pytest.raises(ValueError, match="没有已记录的快照"):
        load_snapshot(storage_connection, schema=scratch_schema)
    # 失败后仍然是孤立数据，后续导入也不得把它当成空库覆盖。
    with pytest.raises(ValueError, match="已有业务数据但没有快照记录"):
        import_snapshot(storage_connection, DATASET, REFERENCE,
                        snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)


def test_prepare_refuses_corrupt_snapshot_without_repairing(storage_connection, scratch_schema):
    prepare_snapshot(storage_connection, DATASET, REFERENCE,
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    storage_connection.execute(
        sql.SQL("UPDATE {} SET root_cause = '被改写的原因' WHERE case_id = 'C001'")
        .format(sql.Identifier(scratch_schema, "cases")),
    )

    with pytest.raises(ValueError, match="库内内容摘要不一致"):
        prepare_snapshot(storage_connection, DATASET, REFERENCE,
                         snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)

    with pytest.raises(ValueError, match="库内内容摘要不一致"):
        load_snapshot(storage_connection, schema=scratch_schema)


def test_prepare_refuses_incompatible_schema(storage_connection, scratch_schema):
    initialize_schema(storage_connection, schema=scratch_schema)
    storage_connection.execute(
        sql.SQL("UPDATE {} SET version = '999'")
        .format(sql.Identifier(scratch_schema, "schema_version")),
    )

    with pytest.raises(RuntimeError, match="结构版本为 999"):
        prepare_snapshot(storage_connection, DATASET, REFERENCE,
                         snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)

    assert schema_version(storage_connection, schema=scratch_schema) == "999"


def test_prepare_rolls_back_failed_import_and_can_retry(storage_connection, scratch_schema):
    initialize_schema(storage_connection, schema=scratch_schema)
    storage_connection.execute(sql.SQL("""
        CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '模拟数据库拒绝写入' USING ERRCODE = '23514';
        END;
        $$
    """).format(sql.Identifier(scratch_schema, "reject_demo_details")))
    storage_connection.execute(sql.SQL("""
        CREATE TRIGGER reject_demo_details BEFORE INSERT ON {}
        FOR EACH ROW EXECUTE FUNCTION {}()
    """).format(sql.Identifier(scratch_schema, "case_details"),
                sql.Identifier(scratch_schema, "reject_demo_details")))

    with pytest.raises(psycopg.errors.CheckViolation, match="模拟数据库拒绝写入"):
        prepare_snapshot(storage_connection, DATASET, REFERENCE,
                         snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)

    with pytest.raises(ValueError, match="没有已记录的快照"):
        load_snapshot(storage_connection, schema=scratch_schema)
    storage_connection.execute(
        sql.SQL("DROP TRIGGER reject_demo_details ON {}")
        .format(sql.Identifier(scratch_schema, "case_details")),
    )
    result = prepare_snapshot(storage_connection, DATASET, REFERENCE,
                              snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    assert result.status == "imported"
    assert len(load_snapshot(storage_connection, schema=scratch_schema).records["cases"]) == 9


def test_concurrent_prepare_initializes_and_imports_once(storage_connection, scratch_schema):
    barrier = threading.Barrier(2)
    statuses = []
    errors = []
    url = resolve_database_url(test=True)

    def worker():
        try:
            with psycopg.connect(url, connect_timeout=5, autocommit=True) as connection:
                barrier.wait(timeout=10)
                result = prepare_snapshot(connection, DATASET, REFERENCE,
                                          snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
                statuses.append(result.status)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(statuses) == ["imported", "preserved"]
    assert len(load_snapshot(storage_connection, schema=scratch_schema).records["cases"]) == 9
