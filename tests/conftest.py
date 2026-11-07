"""演示集成测试使用最小人工主数据，不依赖真实 Excel 或 Locked Test。"""

from datetime import date
import json

from openpyxl import Workbook
import pytest


@pytest.fixture
def reference_path(tmp_path):
    tables = {
        "products": [
            ("product_id", "product_name", "product_family_id", "package_route"),
            ("P1", "演示产品", "PF1", "LF_WB"),
        ],
        "product_families": [("product_family_id", "product_family"), ("PF1", "演示产品族")],
        "failure_modes": [
            ("failure_mode_id", "failure_mode", "applicable_package", "possible_root_causes", "corrective_actions"),
            ("00001", "wire lift", "LF_WB", "表面污染; 参数异常", "改善清洁; 优化参数"),
        ],
        "customer_product_map": [("product_id", "customer_id"), ("P1", "CUS1")],
        "customers": [("customer_id", "customer_name"), ("CUS1", "演示客户")],
        "package_routes": [("package_route",), ("LF_WB",)],
        "process_master": [
            ("process_id", "process"), ("P004", "Wire Bond"), ("P007", "Molding"),
            ("P013", "Storage & Transportation"),
        ],
        "package_process_map": [
            ("package_route", "process_id", "process"),
            ("LF_WB", "P004", "Wire Bond"), ("LF_WB", "P007", "Molding"),
            ("LF_WB", "P013", "Storage & Transportation"),
        ],
    }
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in tables.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    path = tmp_path / "reference.xlsx"
    workbook.save(path)
    workbook.close()
    return path


@pytest.fixture
def demo_path(tmp_path):
    payload = dict(cases=[], details=[], evidences=[], groups=[], memberships=[], sources={},
                   queries=[{"text": "焊线脱落"}])
    for number in [1, 2]:
        case_id = f"C{number}"
        payload["cases"].append(dict(
            case_id=case_id, abnormal_description="焊线脱落", root_cause="表面污染",
            corrective_action="改善清洁", investigation_others=None,
            abnormal_processes=["P004"],
        ))
        payload["details"].append(dict(
            detail_id=f"D{number}", case_id=case_id, product_id="P1", customer_lot="CL1",
            production_lot="PL1", production_time=date(2026, 6, 1).isoformat(),
            detection_stage="OQC", detection_time=date(2026, 6, number + 1).isoformat(),
            abnormal_types=["00001"], affected_qty=100, disposition="报废",
        ))
        payload["evidences"].append(dict(
            checkpoint_id=f"E{number}", case_id=case_id, checkpoint_type="QC", custom_name=None,
            result="观察到表面污染", relevance="related",
        ))
        payload["sources"][case_id] = dict(
            failure_mode_id="00001", root_cause="表面污染", corrective_action="改善清洁",
            closure_status="confirmed",
        )
    path = tmp_path / "demo.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# --- 独立合成 Locked Test 夹具（M6-03 SD4）-----------------------------------
# 只在本夹具内构造最小 Locked Test 输入；不读取真实 data/locked-test/ 数据，
# 也不为真实 Locked Test 生成排名或成绩。相对路径刻意与真实文件名不同，
# 避免测试意外解析到仓库里的正式语料。

LOCKED_REFERENCE_RELATIVE = "data/reference/synthetic-locked-reference.xlsx"
LOCKED_CORPUS_RELATIVE = "data/locked-test/synthetic-locked-corpus.json"
LOCKED_QRELS_RELATIVE = "data/evaluation/locked-test-v1/synthetic-qrels.json"


def _sha256(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def locked_corpus_payload() -> dict:
    """最小合法 Locked Test 语料：2 Case / 2 Detail / 2 Evidence / 1 Query。

    两个 Detail 复用同一 production_lot，满足 GR-04 的“复用批号 1～2 个”。
    """
    snapshot = {
        "snapshot_id": "locked-test-v1-2026-08-31",
        "complete_available_on": "2026-08-31",
        "basis": "测试夹具：人为合成快照约定，不代表真实结案时间。",
    }
    cases, details, evidences, sources = [], [], [], {}
    for index, case_id in enumerate(("SC001", "SC002"), start=1):
        cases.append(dict(
            case_id=case_id, abnormal_description="焊线脱落", root_cause="表面污染",
            corrective_action="改善清洁", investigation_others=None, abnormal_processes=["P004"],
        ))
        details.append(dict(
            detail_id=f"SD00{index}", case_id=case_id, product_id="P1", customer_lot="CL1",
            production_lot="PL1", production_time="2026-06-01", detection_stage="OQC",
            detection_time=f"2026-06-0{index + 2}", abnormal_types=["00001"],
            affected_qty=100, disposition="报废",
        ))
        evidences.append(dict(
            checkpoint_id=f"SE00{index}", case_id=case_id, checkpoint_type="QC", custom_name=None,
            result="观察到表面污染", relevance="related",
        ))
        sources[case_id] = dict(
            failure_mode_id="00001", root_cause="表面污染", corrective_action="改善清洁",
            closure_status="confirmed", review_status="draft_pending_human_review",
        )
    return {
        "split": "locked_test",
        "review_status": "draft_pending_human_review",
        "cases": cases, "details": details, "evidences": evidences,
        "groups": [], "memberships": [], "sources": sources,
        "queries": [{"query_id": "LQ001", "known_at": "2026-09-30", "text": "焊线脱落，原因尚未确认。"}],
        "corpus_version": "synthetic-locked-fixture",
        "availability_snapshot": snapshot,
    }


def locked_qrels_payload(layout) -> dict:
    """与夹具语料匹配的已确认 qrels；默认 2 对完整配对。"""
    return {
        "qrels_version": "locked-test-qrels-v1",
        "split": "locked_test",
        "review_status": "human_confirmed",
        "confirmed_on": "2026-10-08",
        "sources": {
            "dataset": {"path": LOCKED_CORPUS_RELATIVE, "sha256": _sha256(layout.corpus_path)},
            "reference": {"path": LOCKED_REFERENCE_RELATIVE,
                          "sha256": _sha256(layout.reference_path)},
        },
        "judgments": [
            {"query_id": "LQ001", "case_id": "SC001", "relevance": 1, "rationale": "同异常"},
            {"query_id": "LQ001", "case_id": "SC002", "relevance": 0, "rationale": "不同异常"},
        ],
    }


class LockedTestLayout:
    """合成 Locked Test 输入的可写夹具；只落在 tmp_path 下。"""

    def __init__(self, root, reference_source):
        from pathlib import Path

        self.root = Path(root)
        self.reference_source = reference_source
        self.reference_path = self.root / LOCKED_REFERENCE_RELATIVE
        self.corpus_path = self.root / LOCKED_CORPUS_RELATIVE
        self.qrels_path = self.root / LOCKED_QRELS_RELATIVE

    def _write(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))

    def reset(self):
        import shutil

        self.reference_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.reference_source, self.reference_path)
        self.write_corpus()
        self.write_qrels()
        return self

    def write_corpus(self, payload=None):
        return self._write(self.corpus_path, payload or locked_corpus_payload())

    def write_qrels(self, qrels=None):
        return self._write(self.qrels_path, qrels or locked_qrels_payload(self))


@pytest.fixture
def locked_test_layout(tmp_path, reference_path):
    """独立合成 Locked Test 夹具：主数据副本 + 合法语料 + 已确认 qrels。"""
    return LockedTestLayout(tmp_path / "locked-project", reference_path).reset()
