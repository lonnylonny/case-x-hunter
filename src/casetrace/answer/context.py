"""M4-01 证据上下文：把固定 R3 检索结果整理成可直接交给模型的历史材料。

本模块不调用模型，也不读 qrels、标注理由或候选根因库：
输入是当前 Query、known_at 与一份记录在案的语料快照，输出分为模型上下文与运行元数据。
语料可选择文件（dev-v3 JSON + 主数据 Excel）或已读回的数据库快照；两条路线共用
同一次 R3 检索与同一套元数据，区别只在「谁提供 records / reference / payload」。
检索方案固定为 M3-07 选定配置，这里不改检索参数、排序、评估默认值或旧产物。
上下文只含命中的 Case；历史原因挂 Case、检查结果挂 Case + checkpoint_id，不互相推导。
"""

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date
import hashlib
from pathlib import Path
import re

from casetrace.data.dataset_model import Case, CaseDetail, EvidenceCheckpoint
from casetrace.data.reference import ReferenceData
from casetrace.demo import check_source_records, load_validated_dataset
from casetrace.evaluation.runner import build_retriever_from_records
from casetrace.retrieval.base import SearchHit
from casetrace.storage.schema import DEFAULT_SCHEMA
from casetrace.storage.snapshot import LoadedSnapshot

# 数据源标识：写进运行记录的 corpus.data_source；旧记录缺少该字段时按文件来源理解。
DATA_SOURCE_FILE = "file"
DATA_SOURCE_POSTGRES = "postgres"

# 库内快照必须与本次请求一致的身份字段；内容检查由导入与 load_snapshot 负责。
SNAPSHOT_IDENTITY_FIELDS = (
    "snapshot_id", "known_at", "dataset_sha256", "reference_sha256", "basis",
)

# M3-07 选定的固定检索方案：BM25 + Query 过滤（H1 否定小句 + H2 标识/标签词）。
RETRIEVAL_METHOD = "bm25_drop_negation_labels"
# M4 默认阅读前 4 条；前 4 条是候选，不是四条已确认相关案例。
DEFAULT_TOP_K = 4

# 来源记录里允许进入模型上下文的字段：合成候选出处、原因措施原文、结案状态；
# 上传的 records 仅由 CSV 适配器组装记录 ID、报告出处、文件名与行号。
# 其余字段（generation_note、rationale、review_status）属生成／审阅元数据，不进入证据上下文；
# 依据见 docs/design/behavior-contracts.md §3；下方常量列出允许进入上下文的字段。
SOURCE_FIELDS = ("sheet", "failure_mode_id", "root_cause", "corrective_action", "closure_status", "records")


@dataclass(frozen=True)
class CorpusSnapshot:
    """一份记录在案的语料可用性快照：身份、时点、输入文件哈希与依据。

    可用时点来自用户设定的模拟约定，不能由 detection_time 推导；
    本版只支持记录在案的快照，不建设通用的历史版本系统。
    """

    snapshot_id: str
    known_at: date
    dataset_sha256: str
    reference_sha256: str
    basis: str


class SnapshotError(ValueError):
    """已知的快照身份或时点前提不满足；仍兼容 CLI 对 ValueError 的处理。"""


# dev-v3 已记录的完整可用快照：九条 Case 在 2026-09-15 前已结案且完整可用。
# 依据见 data/evaluation/dev-v3/README.md「快照约定与边界」；哈希与语料文件原字节一致。
DEV_V3_SNAPSHOT = CorpusSnapshot(
    snapshot_id="dev-v3-2026-09-15",
    known_at=date(2026, 9, 15),
    dataset_sha256="0a29041a4e6ed11a5b31ff85a74fa8bed9b79afeb1d35158ea9430f6e5af8f0b",
    reference_sha256="f5001bddd3dff43342a2569ceda836ddb4f5c0caca942c1c4d043d99a9131c85",
    basis=(
        "data/dev/demo-v3.json 的九条 Case 被设定为在 2026-09-15 之前已结案且完整可用，"
        "沿用 dev-v2 的模拟快照时点；这是模拟约定，不代表已实现通用结案／可用时间过滤。"
    ),
)

# 保留在运行元数据里的已知限制，避免下游把候选读成结论。
RUN_NOTES = (
    "前 4 条是检索候选，不是已确认相关案例；不得为证据不足的候选编造相关理由。",
    "检索分数只是排序依据，不是相关概率，也不可跨方法比较。",
    "源 Case 的审阅状态仍是 draft，本阶段不把它升级为正式事实，也不产生新的 Ground Truth。",
)


@dataclass(frozen=True)
class AnswerRunInputs:
    """一次回答运行的全部输入：检索已完成，等待组装证据上下文。"""

    query: str
    known_at: date
    hits: list[SearchHit]
    records: dict
    reference: ReferenceData
    sources: dict


@dataclass(frozen=True)
class AnswerRun:
    """一次回答运行的输入与运行元数据；模型上下文由输入另行组装。"""

    inputs: AnswerRunInputs
    run_metadata: dict


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_query_and_top_k(query: str, known_at: date, top_k: int) -> None:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("Query | text | 必须是非空字符串")
    if not isinstance(known_at, date):
        raise ValueError("Query | known_at | 必须是 date")
    if type(top_k) is not int or top_k <= 0:
        raise ValueError(f"top_k | 必须是正整数（拒绝 bool 与小数）；实际 {top_k!r}")


def _snapshot_metadata(recorded) -> dict:
    """快照描述字段：`CorpusSnapshot` 与库内 `StoredSnapshot` 都提供这五个字段。

    数据库路线传入库内实际读回值，不把文件路径或连接信息混进快照身份。
    """

    return {
        "snapshot_id": recorded.snapshot_id,
        "known_at": recorded.known_at.isoformat(),
        "basis": recorded.basis,
        "dataset_sha256": recorded.dataset_sha256,
        "reference_sha256": recorded.reference_sha256,
    }


def _check_known_at(known_at: date, snapshot: CorpusSnapshot) -> None:
    """Query 时点必须就是记录在案的可用时点；本版没有通用历史时点过滤。"""

    if known_at != snapshot.known_at:
        raise SnapshotError(
            f"快照 | known_at {known_at.isoformat()} 不被当前记录的可用性快照支持："
            f"只有 {snapshot.snapshot_id}（{snapshot.known_at.isoformat()}）；"
            "本版不实现通用历史时点过滤"
        )


def _check_detection_times(records: dict, snapshot: CorpusSnapshot) -> None:
    """时点一致性：语料最晚的发现日期不能晚于快照可用时点。"""

    latest = max((detail.detection_time for detail in records["details"]), default=None)
    if latest is not None and latest > snapshot.known_at:
        raise SnapshotError(
            f"时点一致性 | 语料中最晚的发现日期 {latest.isoformat()} 晚于 "
            f"{snapshot.snapshot_id} 的可用时点 {snapshot.known_at.isoformat()}；"
            "当前快照约定语料在该时点前完整可用，本版不实现通用时间过滤"
        )


def _check_snapshot(
    snapshot: CorpusSnapshot, known_at: date, dataset_path: Path, reference_path: Path,
    records: dict, payload: dict,
) -> None:
    """文件路线：检查输入是否落在记录在案的快照前提内；不满足就明确拒绝。"""

    _check_known_at(known_at, snapshot)
    actual_dataset = _sha256_file(dataset_path)
    if actual_dataset != snapshot.dataset_sha256:
        raise ValueError(
            f"快照 | {dataset_path} | SHA-256 与 {snapshot.snapshot_id} 记录的语料不一致："
            f"记录 {snapshot.dataset_sha256}，实际 {actual_dataset}；该语料没有对应的可用性依据"
        )
    actual_reference = _sha256_file(reference_path)
    if actual_reference != snapshot.reference_sha256:
        raise ValueError(
            f"主数据 | {reference_path} | SHA-256 与 {snapshot.snapshot_id} 记录的文件不一致："
            f"记录 {snapshot.reference_sha256}，实际 {actual_reference}；"
            "该主数据没有对应的快照依据"
        )
    review_status = payload.get("review_status")
    if not isinstance(review_status, str) or not review_status.strip():
        raise ValueError("Dataset | review_status | 必须是非空字符串")
    if not records["details"]:
        raise ValueError("Dataset | details | 不能为空")
    _check_detection_times(records, snapshot)


def _check_loaded_snapshot(
    loaded: LoadedSnapshot, snapshot: CorpusSnapshot, known_at: date,
) -> None:
    """数据库路线：只核对库内描述是否对应本次请求，并保留时点前提检查。

    完整内容、CR/GR 与来源核对已由导入和 `load_snapshot` 承担，这里不重跑、
    不重复计算摘要，也不调用 `verify_snapshot`。
    """

    stored = loaded.snapshot
    mismatches = [
        f"{field}: 请求 {getattr(snapshot, field)!r}，库内 {getattr(stored, field)!r}"
        for field in SNAPSHOT_IDENTITY_FIELDS
        if getattr(stored, field) != getattr(snapshot, field)
    ]
    if mismatches:
        raise SnapshotError(
            "快照 | 库内描述与本次请求的快照不一致，拒绝回答：" + "；".join(mismatches)
        )
    _check_known_at(known_at, snapshot)
    _check_detection_times(loaded.records, snapshot)


def prepare_answer_run(
    query: str, known_at: date, *, dataset_path: Path | None = None,
    reference_path: Path | None = None,
    snapshot: CorpusSnapshot | None = None, top_k: int = DEFAULT_TOP_K,
    loaded_snapshot: LoadedSnapshot | None = None, db_schema: str = DEFAULT_SCHEMA,
) -> AnswerRun:
    """应用准备入口：核对输入与快照前提 → 建一次 R3 索引 → 检索 → 运行元数据。

    数据源由调用方（CLI / API 边界）选定；选定后两条路线共用同一次 R3 检索、
    同一套排名与元数据组装：

    - 给出已读回的 `loaded_snapshot`（数据库路线）时使用它的对象与元数据，
      不打开任何文件，`dataset_path` / `reference_path` 可以不传；
    - 否则走原有文件路线，必须同时给出两份路径，并执行现有文件身份、
      CR/GR、来源与内容时点检查。

    不要求调用方提供 qrels；标签、理由与候选根因库不参与检索，也不进入元数据。
    返回的原始排名就是交给上下文组装的顺序；这里不排序、不筛选、不补名次。
    """

    _check_query_and_top_k(query, known_at, top_k)
    if snapshot is None:
        if loaded_snapshot is not None and loaded_snapshot.snapshot.split == "user_upload":
            snapshot = CorpusSnapshot(**{
                key: getattr(loaded_snapshot.snapshot, key) for key in SNAPSHOT_IDENTITY_FIELDS
            })
        else:
            snapshot = DEV_V3_SNAPSHOT
    if loaded_snapshot is None:
        if dataset_path is None or reference_path is None:
            raise ValueError(
                "文件数据源必须同时给出 --data 与 --reference；"
                "数据库数据源请传入已读回的 loaded_snapshot"
            )
        dataset_path, reference_path = Path(dataset_path), Path(reference_path)
        records, payload, reference = load_validated_dataset(dataset_path, reference_path)
        check_source_records(records, payload, reference)
        _check_snapshot(snapshot, known_at, dataset_path, reference_path, records, payload)
        snapshot_metadata = _snapshot_metadata(snapshot)
        route_metadata = {
            "data_source": DATA_SOURCE_FILE,
            "dataset_path": str(dataset_path),
            "reference_path": str(reference_path),
        }
    else:
        # 内容与来源检查已随导入和读回完成；这里只核对库内描述是否对应本次请求。
        _check_loaded_snapshot(loaded_snapshot, snapshot, known_at)
        records = loaded_snapshot.records
        reference = loaded_snapshot.reference
        payload = loaded_snapshot.payload
        snapshot_metadata = _snapshot_metadata(loaded_snapshot.snapshot)
        route_metadata = {
            "data_source": DATA_SOURCE_POSTGRES,
            "schema": db_schema,
            "content_digest": loaded_snapshot.snapshot.content_digest,
            "digest_version": loaded_snapshot.snapshot.digest_version,
            # 这里保存的是原始导入路径，表示来源位置；本次没有读取这两个文件。
            "dataset_path": loaded_snapshot.snapshot.dataset_path,
            "reference_path": loaded_snapshot.snapshot.reference_path,
        }

    corpus = {
        **route_metadata,
        # 两条路线共有的语料描述；draft 状态不因换数据源而升级。
        "case_count": len(records["cases"]),
        "review_status": payload["review_status"],
        "source_cases_are_draft": True,
    }

    retriever = build_retriever_from_records(records, reference, method=RETRIEVAL_METHOD)
    hits = retriever.search(query, top_k=top_k)

    metadata = {
        "snapshot": snapshot_metadata,
        "corpus": corpus,
        "retrieval": {
            "requested_method": RETRIEVAL_METHOD,
            "top_k": top_k,
            "description": retriever.describe(),
        },
        # 原始排名：顺序即名次，分数只是排序依据，不是相关概率。
        "ranking": [
            {
                "rank": rank,
                "case_id": hit.case_id,
                "score": hit.score,
                "matched_terms": hit.matched_terms,
            }
            for rank, hit in enumerate(hits, start=1)
        ],
        "notes": list(RUN_NOTES),
    }
    inputs = AnswerRunInputs(
        query=query, known_at=known_at, hits=hits, records=records,
        reference=reference, sources=payload["sources"],
    )
    return AnswerRun(inputs=inputs, run_metadata=metadata)


@dataclass(frozen=True)
class CaseEvidence:
    """模型上下文里的一条命中 Case：历史原文、子记录、来源与主数据背景。"""

    rank: int
    case_id: str
    case: Case
    details: list[CaseDetail]
    evidences: list[EvidenceCheckpoint]
    source: dict
    # 主数据里可追溯的名称与关系；由 product_backgrounds 读取，不从路线或分组推断。
    background: list[dict]
    # 已确认异常工序的 ID 与名称；不把发现阶段或候选工序混入此处。
    processes: list[dict]


@dataclass(frozen=True)
class EvidenceContext:
    """交给模型的证据上下文：完整 Query 与按原排名排列的命中 Case。"""

    query: str
    cases: list[CaseEvidence]
    # 仅查 Query 提到的已知产品 ID；“提到”不等于当前产品（保留 Query 的否定与归属）。
    query_product_mentions: list[dict] = field(default_factory=list)


def query_product_mentions(reference: ReferenceData, query: str) -> list[dict]:
    """显式 ID 查主数据，不抽取 Incident 字段、不读候选根因库或 qrels。"""
    mentioned = dict.fromkeys(re.findall(r"[A-Za-z0-9_]+", query))
    result = []
    for identity in mentioned:
        product = reference.products.get(identity)
        if product is not None:
            result.append({
                "query_mention": identity,
                **product,
                "product_family": reference.product_families[product["product_family_id"]],
                "source": "reference.products/product_families",
            })
    return result


def product_backgrounds(reference: ReferenceData, details: list[CaseDetail]) -> list[dict]:
    """按 Detail 出现顺序列出产品、客户、产品族与路线名称；同一产品只出现一次。"""

    backgrounds, seen = [], set()
    for detail in details:
        if detail.product_id in seen:
            continue
        seen.add(detail.product_id)
        product = reference.products.get(detail.product_id)
        if product is None:
            raise ValueError(f"主数据缺少产品：{detail.product_id}")
        backgrounds.append({
            "product_id": product["product_id"],
            "product_name": product["product_name"],
            "product_family": reference.product_families[product["product_family_id"]],
            "package_route": product["package_route"],
            "customer_id": reference.product_customers[detail.product_id],
        })
    return backgrounds


def process_backgrounds(reference: ReferenceData, case: Case) -> list[dict]:
    """按 process_id 排序列出 Case 已确认异常工序的可追溯 ID 与名称。"""

    return [
        {"process_id": key, "process_name": reference.processes[key]}
        for key in sorted(case.abnormal_processes)
    ]


def _to_jsonable(value):
    """把上下文转成可 json.dumps 的结构：dataclass 逐字段展开，日期转 ISO 文本。"""

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _to_jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"无法序列化到 JSON 的值：{type(value).__name__}")


def context_to_payload(context: EvidenceContext) -> dict:
    """模型上下文的通用序列化；内容与顺序完全由 EvidenceContext 决定。"""

    return _to_jsonable(context)


def build_evidence_context(
    query: str, hits: list[SearchHit], records: dict, reference: ReferenceData,
    sources: dict,
) -> EvidenceContext:
    """把检索命中组装成模型上下文：只含命中 Case 的原文、允许字段的来源与主数据背景。

    行为契约（测试按这些行为检查，不绑定内部写法）：

    1. 只处理 `hits` 中的 Case，顺序与 `hits` 一致，`rank` 从 1 开始连续；
    2. 每条 Case 保留历史原文：`Case` 字段、该 Case 的 `Detail` 与 `Evidence`；
    3. 来源记录只保留 `SOURCE_FIELDS` 允许的字段，生成／审阅元数据不进入上下文
       （draft 状态记在运行元数据里）；检查结果保留 `checkpoint_id`；
    4. 不读 qrels、标注理由或候选根因库，也不把 `score` 当相关概率放进上下文；
    5. `hits` 为空 → `cases=[]`；未知或重复的命中 `case_id` 明确报错，不静默跳过。

    主数据的名称与关系用 `product_backgrounds` 与 `process_backgrounds` 读取，
    `context_to_payload` 负责通用序列化。
    """

    case_by_id = {item.case_id: item for item in records["cases"]}
    collected: list[CaseEvidence] = []
    seen: set[str] = set()

    for rank, hit in enumerate(hits, start=1):
        if hit.case_id not in case_by_id:
            raise ValueError(f"命中 Case 不在已校验语料中：{hit.case_id}")
        if hit.case_id in seen:
            raise ValueError(f"命中 Case 重复：{hit.case_id}")
        seen.add(hit.case_id)

        case = case_by_id[hit.case_id]
        details = [item for item in records["details"] if item.case_id == hit.case_id]
        evidences = [item for item in records["evidences"] if item.case_id == hit.case_id]
        raw_source = sources[hit.case_id]
        # 按 SOURCE_FIELDS 固定字段顺序：数据库读回的字典键顺序可能与文件不同，
        # 这里统一一次，保证两条数据源路线发出的模型消息逐字一致。
        source = {key: raw_source[key] for key in SOURCE_FIELDS if key in raw_source}

        collected.append(CaseEvidence(
            rank=rank,
            case_id=hit.case_id,
            case=case,
            details=details,
            evidences=evidences,
            source=source,
            background=product_backgrounds(reference, details),
            processes=process_backgrounds(reference, case),
        ))

    return EvidenceContext(query=query, cases=collected,
                           query_product_mentions=query_product_mentions(reference, query)
                           if hits else [])
