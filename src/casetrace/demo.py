"""开发样例 → 数据校验 → 历史文本 → BM25；语义审阅和 Ground Truth 独立进行。"""

from datetime import date
import json
from pathlib import Path

from casetrace.data.dataset_model import Case, CaseDetail, CaseGroup, EvidenceCheckpoint, Membership
from casetrace.data.reference import ReferenceData, load_reference
from casetrace.data.validators import check_confirmed_evidence, validate_generation, validate_relations
from casetrace.retrieval.bm25 import BM25Retriever


def load_demo(path: Path) -> tuple[dict, dict]:
    """只将五类实体交给 Validator；查询、来源和审查元数据留在实体之外。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("开发样例的顶层必须是 JSON 对象")
    records = {}
    for name, model in (
        ("cases", Case), ("details", CaseDetail), ("evidences", EvidenceCheckpoint),
        ("groups", CaseGroup), ("memberships", Membership),
    ):
        if not isinstance(payload.get(name), list):
            raise ValueError(f"开发样例的 {name} 必须是列表")
        records[name] = []
        for row in payload[name]:
            if not isinstance(row, dict):
                raise ValueError(f"{name} 中的每条记录必须是 JSON 对象")
            values = dict(row)
            if name == "cases" and "abnormal_processes" not in values:
                raise ValueError(f"Case[{values.get('case_id', '?')}] | abnormal_processes | 缺少必填字段")
            if name == "details":
                for field in ("production_time", "detection_time"):
                    values[field] = date.fromisoformat(values[field])
            records[name].append(model(**values))
    return records, payload


def build_documents(records: dict, reference: ReferenceData) -> dict[str, str]:
    """按 Case 建索引；只拼接历史记录，不拼接查询、候选库全文或审查元数据。

    历史结案原因可用于检索。Current Incident 的未知原因不能被补入查询。
    内部 Case/Detail/Evidence ID 不作检索词，只保留在结果中用于追溯。
    """
    parts = {
        case.case_id: [case.abnormal_description, case.root_cause,
                       case.corrective_action, case.investigation_others or "",
                       *(text for process_id in sorted(case.abnormal_processes)
                         for text in (process_id, reference.processes[process_id]))]
        for case in records["cases"]
    }
    for detail in records["details"]:
        product = reference.products[detail.product_id]
        parts[detail.case_id].extend([
            detail.product_id, product["product_name"], product["package_route"],
            detail.production_lot, detail.customer_lot, detail.detection_stage,
            detail.production_time.isoformat(), detail.detection_time.isoformat(),
            detail.disposition,
            *(reference.failure_modes[key]["failure_mode"] for key in detail.abnormal_types),
        ])
    for evidence in records["evidences"]:
        parts[evidence.case_id].extend([
            evidence.checkpoint_type, evidence.custom_name or "", evidence.result,
        ])
    return {case_id: "\n".join(texts) for case_id, texts in parts.items()}


def load_validated_dataset(
    data_path: Path, reference_path: Path, *, reference: ReferenceData | None = None,
) -> tuple[dict, dict, ReferenceData]:
    """读取开发样例与主数据，执行已实现的确定性校验。

    返回实体记录、原始 payload 和主数据；不检查来源记录，也不构建检索文本。
    demo 与评估共用这条路径，保证两者看到的是同一份已校验语料。
    存储导入可传入已经读取的主数据，避免再次打开同一份 Excel。
    """
    records, payload = load_demo(data_path)
    if reference is None:
        reference = load_reference(reference_path)
    errors = validate_relations(**records, **reference.validator_maps())
    if not errors:
        errors = validate_generation(**records)
    if errors:
        raise ValueError("数据集未通过确定性校验：\n" + "\n".join(errors))
    return records, payload, reference


def check_source_records(records: dict, payload: dict, reference: ReferenceData) -> None:
    """来源记录仅用于核对已选择的候选，不能进入检索文本成为额外的匹配线索。"""
    errors = check_confirmed_evidence(records["evidences"], {
        case.case_id: payload["sources"][case.case_id].get("closure_status")
        for case in records["cases"]
    })
    if errors:
        raise ValueError("\n".join(errors))
    for case in records["cases"]:
        source = payload["sources"][case.case_id]
        case_modes = {mode_id for detail in records["details"] if detail.case_id == case.case_id
                      for mode_id in detail.abnormal_types}
        if source["failure_mode_id"] not in case_modes:
            raise ValueError(f"{case.case_id} 的来源未对应本 Case 选择的 Failure Mode")
        mode = reference.failure_modes[source["failure_mode_id"]]
        for field, column in (("root_cause", "possible_root_causes"),
                              ("corrective_action", "corrective_actions")):
            selected = source[field]
            if selected not in [item.strip() for item in mode[column].split(";")]:
                raise ValueError(f"{case.case_id} 的 {field} 候选已不在主数据中")
            if selected not in getattr(case, field):
                raise ValueError(f"{case.case_id} 的 {field} 与演示来源记录不一致")


def run_demo(data_path: Path, reference_path: Path, *, query: str | None, top_k: int) -> dict:
    records, payload, reference = load_validated_dataset(data_path, reference_path)
    check_source_records(records, payload, reference)

    if query is None:
        query = payload["queries"][0]["text"]
    retriever = BM25Retriever(build_documents(records, reference))
    cases = {case.case_id: case for case in records["cases"]}
    results = []
    for hit in retriever.search(query, top_k=top_k):
        case = cases[hit.case_id]
        results.append({
            "case_id": hit.case_id, "score": hit.score, "matched_terms": hit.matched_terms,
            "abnormal_description": case.abnormal_description, "root_cause": case.root_cause,
            "abnormal_processes": sorted(case.abnormal_processes),
            "abnormal_process_names": [reference.processes[key] for key in sorted(case.abnormal_processes)],
            "evidences": [
                {"checkpoint_id": item.checkpoint_id, "result": item.result}
                for item in records["evidences"] if item.case_id == hit.case_id
            ],
        })
    return {
        "query": query, "case_count": len(cases), "results": results,
        "review_status": "development_draft_pending_human_review",
    }
