"""M5-03 API 测试夹具：把文件对象装进公开的 `LoadedSnapshot`，模拟一次数据库读回。

离线接口测试用合成语料，不连接数据库；真实 PostgreSQL 五 Query 对照放在
`tests/storage/test_api_integration.py`（SD3）。身份字段统一用记录在案的
`DEV_V3_SNAPSHOT`，因为服务端固定请求该快照。
"""

from __future__ import annotations

from datetime import date, datetime
import json

import pytest

from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.demo import load_validated_dataset
from casetrace.storage import LoadedSnapshot, StoredSnapshot

SNAPSHOT_KNOWN_AT = date(2026, 9, 15)


@pytest.fixture(autouse=True)
def offline_runtime_settings(monkeypatch):
    """离线 HTTP 用例用默认设置；真实持久化在 storage/test_settings.py 验证。"""
    from casetrace import api
    from casetrace.settings import DEFAULT_SETTINGS
    monkeypatch.setattr(api, "read_runtime_settings", lambda schema: DEFAULT_SETTINGS)


@pytest.fixture
def api_dataset(tmp_path):
    """两条最小 Case（同产品、同 Failure Mode），带 split／review_status 供元数据组装。"""

    spec = (
        ("C1", "焊线脱落，表面污染", "表面污染", "改善清洁"),
        ("C2", "焊线脱落，参数异常", "参数异常", "优化参数"),
    )
    payload = dict(
        cases=[], details=[], evidences=[], groups=[], memberships=[], sources={},
        split="development", review_status="draft_pending_human_review",
        queries=[{"query_id": "Q-API", "text": "焊线脱落",
                  "known_at": SNAPSHOT_KNOWN_AT.isoformat()}],
    )
    for index, (case_id, description, root_cause, action) in enumerate(spec, start=1):
        payload["cases"].append(dict(
            case_id=case_id, abnormal_description=description, root_cause=root_cause,
            corrective_action=action, investigation_others=None, abnormal_processes=["P004"],
        ))
        payload["details"].append(dict(
            detail_id=f"D{index}", case_id=case_id, product_id="P1", customer_lot="CL1",
            production_lot="PL1", production_time=date(2026, 6, 1).isoformat(),
            detection_stage="OQC", detection_time=date(2026, 6, index).isoformat(),
            abnormal_types=["00001"], affected_qty=100, disposition="报废",
        ))
        payload["evidences"].append(dict(
            checkpoint_id=f"E{index}", case_id=case_id, checkpoint_type="QC", custom_name=None,
            result="观察到表面污染", relevance="related",
        ))
        payload["sources"][case_id] = dict(
            sheet="failure_modes", failure_mode_id="00001", root_cause=root_cause,
            corrective_action=action, closure_status="confirmed",
        )
    path = tmp_path / "api-demo.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, payload


@pytest.fixture
def loaded_snapshot(api_dataset, reference_path):
    """一次「数据库读回」的结果对象；库内描述与 `DEV_V3_SNAPSHOT` 一致。"""

    dataset_path, payload = api_dataset
    records, payload, reference = load_validated_dataset(dataset_path, reference_path)
    return LoadedSnapshot(
        records=records, reference=reference, payload=payload,
        snapshot=StoredSnapshot(
            snapshot_id=DEV_V3_SNAPSHOT.snapshot_id,
            known_at=DEV_V3_SNAPSHOT.known_at,
            dataset_path=str(dataset_path),
            dataset_sha256=DEV_V3_SNAPSHOT.dataset_sha256,
            reference_path=str(reference_path),
            reference_sha256=DEV_V3_SNAPSHOT.reference_sha256,
            split=payload["split"], review_status=payload["review_status"],
            basis=DEV_V3_SNAPSHOT.basis,
            content_digest="test-content-digest", digest_version="content-digest-v2",
            imported_at=datetime(2026, 10, 5, 12, 0, 0),
        ),
        counts={"cases": len(records["cases"])},
    )
