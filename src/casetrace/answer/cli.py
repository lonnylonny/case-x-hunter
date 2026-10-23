"""M4-03 `casetrace answer`：把检索、证据上下文、生成与引用守卫接成一条运行入口。

一次运行的状态互斥且明确，「失败」与「没有候选」不是同一件事：

- `ok`：检索有候选，模型返回的回答通过结构与引用守卫；
- `no_hits`：本次检索没有候选，不构造模型，直接给出具体缺口（不是运行故障）；
- `model_failed`：凭据／配置缺失或调用失败（超时、权限、网络、返回不可用）；
- `format_failed`：返回内容不符合回答契约；
- `citation_failed`：回答里的引用无法在本次上下文中定位。

只有 `ok` 会把回答正文写到标准输出；失败状态保留运行记录与错误说明用于排查，
但不冒充成功。引用守卫只确认引用能定位，不判断引用是否支持它所在的那句话。

本模块复用 M4-01／M4-02 的应用函数：不重新检索、不按 qrels 筛选候选、
不做自动重试或改写，也不把运行故障写成「没有相关案例」。

数据源在 CLI 边界选择：`file`（默认，沿用 `--data` / `--reference`）或 `postgres`
（一次短连接读回已记录快照后立即关闭）。选定后两条路线复用同一回答核心，
CLI 只负责连接、参数与输出，不复制检索或生成逻辑。
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
import json
from pathlib import Path
import sys

import psycopg

from casetrace.answer.context import (
    DATA_SOURCE_FILE,
    DATA_SOURCE_POSTGRES,
    DEFAULT_TOP_K,
    DEV_V3_SNAPSHOT,
    AnswerRun,
    CorpusSnapshot,
    EvidenceContext,
    build_evidence_context,
    context_to_payload,
    prepare_answer_run,
)
from casetrace.answer.generation import (
    AnswerFormatError,
    GenerationResult,
    generate_grounded_answer,
)
from casetrace.answer.model import (
    ChatModel,
    DeepSeekChatModel,
    ModelConfigError,
    ModelError,
    TokenUsage,
)
from casetrace.answer.prompt import PROMPT_VERSION, render_system_prompt
from casetrace.answer.provenance import implementation_identity, text_sha256
from casetrace.answer.validation import CitationError, CitationReport, ensure_answer_citations
from casetrace.evaluation.runner import write_report
from casetrace.storage import DEFAULT_SCHEMA, LoadedSnapshot, connect, load_snapshot

RECORD_VERSION = "m4-04-answer-run-2"
PROVIDER = "deepseek"

STATUS_OK = "ok"
STATUS_NO_HITS = "no_hits"
STATUS_MODEL_FAILED = "model_failed"
STATUS_FORMAT_FAILED = "format_failed"
STATUS_CITATION_FAILED = "citation_failed"

EXIT_OK = 0
EXIT_USAGE = 2
# 退出码：0 表示本次运行正常结束（含「没有候选」这种明确结果），其余按失败原因区分。
EXIT_CODES = {
    STATUS_OK: EXIT_OK,
    STATUS_NO_HITS: EXIT_OK,
    STATUS_MODEL_FAILED: 3,
    STATUS_FORMAT_FAILED: 4,
    STATUS_CITATION_FAILED: 5,
}

# 与 model.DeepSeekChatModel.complete 固定发送的参数一致；写进记录便于核对当时配置。
REQUEST_RESPONSE_FORMAT = "json_object"
REQUEST_THINKING = "disabled"

# 空命中时给出的具体缺口：说明缺的是候选，而不是模型或运行出了问题。
NO_CANDIDATE_INSUFFICIENCY = (
    "本次检索没有返回候选历史案例，无法据此整理相关理由、历史原因或历史检查结果；"
    "这不是模型或运行故障，也不表示已回答「没有相关案例」，"
    "需要补充或改写当前已知信息后重跑。"
)

# M4-03 在 M4-01 的运行提示之外补充的限制；两者都会写进运行记录的 limits。
RUN_LIMITS = (
    "引用校验只确认引用能否在本次上下文中定位，不代表引用支持了它所在的那句话；"
    "语义支持必须另行人工审阅。",
    "回答是历史参考，不判断当前 Incident 的最终 Root Cause。",
    "运行记录不含凭据；本次回答不构成新的 Ground Truth，也不升级 draft Case。",
)


def build_model(model_name: str) -> ChatModel:
    """构造真实模型；真实路径固定 DeepSeek 非思考模式，测试可替换该工厂。"""

    # v2 复制历史改善措施与来源，给完整 JSON 留出空间；不增加重试。
    return DeepSeekChatModel(model=model_name, max_tokens=4096)


def load_database_snapshot(schema: str) -> LoadedSnapshot:
    """数据库路线：一次短连接读回已记录快照，读完立即关闭。

    初始化与导入由 M5-01 的 `python -m casetrace.storage` 负责；回答入口不自动
    建表、不导入，也不在失败时退回文件。连接与驱动错误统一按「回答前提不满足」
    上报：沿用 `ValueError`，不建立新的错误层级或重试机制，消息不回显连接串。
    """

    try:
        with connect() as connection:
            return load_snapshot(connection, schema=schema)
    except (OSError, RuntimeError, psycopg.Error) as error:
        raise ValueError(
            f"数据库路线不可用（{type(error).__name__}）："
            "请检查 CASETRACE_DATABASE_URL、数据库连接及初始化状态"
        ) from error


def _data_source_label(corpus: dict) -> str:
    """运行记录里的实际数据源；旧记录缺少 `data_source` 时按文件来源理解。"""

    if corpus.get("data_source", DATA_SOURCE_FILE) == DATA_SOURCE_POSTGRES:
        return f"PostgreSQL schema={corpus.get('schema', '')}"
    return "文件"


@dataclass(frozen=True)
class AnswerOutcome:
    """一次回答运行的结果：状态、可复查的运行记录与给终端的一行说明。"""

    status: str
    record: dict
    message: str

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]


def _base_record(run: AnswerRun, context: EvidenceContext, query_id: str | None) -> dict:
    """运行记录的固定部分：数据身份、快照、检索配置、候选排名与实际发送的证据。"""

    metadata = run.run_metadata
    return {
        "record_version": RECORD_VERSION,
        "status": None,
        "query": {
            "query_id": query_id,
            "text": run.inputs.query,
            "known_at": run.inputs.known_at.isoformat(),
        },
        "snapshot": metadata["snapshot"],
        "corpus": metadata["corpus"],
        "retrieval": metadata["retrieval"],
        "ranking": metadata["ranking"],
        "context": context_to_payload(context),
        "implementation": implementation_identity(),
        "request": None,
        "response": {
            "provider": PROVIDER,
            "model": None,
            "called_model": False,
            "elapsed_seconds": 0.0,
            "usage": TokenUsage().to_dict(),
        },
        "citations": CitationReport(checked=0, issues=()).to_payload(),
        "answer": None,
        "answer_text": None,
        "error": None,
        "limits": [*RUN_LIMITS, *metadata["notes"]],
    }


def _request_payload(model: ChatModel) -> dict:
    """本次请求参数：模型别名、长度、温度与固定的 JSON Output／非思考开关。"""

    return {
        "provider": PROVIDER,
        "model_alias": getattr(model, "model_id", None),
        "prompt_version": PROMPT_VERSION,
        "max_tokens": getattr(model, "max_tokens", None),
        "temperature": getattr(model, "temperature", None),
        "response_format": REQUEST_RESPONSE_FORMAT,
        "thinking": REQUEST_THINKING,
    }


def _response_payload(result: GenerationResult) -> dict:
    """供应商实际返回的身份与用量：模型身份以响应字段为准，不用请求别名。"""

    return {
        "provider": PROVIDER,
        "model": result.model_id,
        "called_model": result.called_model,
        "elapsed_seconds": result.elapsed_seconds,
        "usage": result.usage.to_dict(),
    }


def _failed(record: dict, status: str, error: Exception, *,
            called_model: bool, message: str) -> AnswerOutcome:
    """把失败写进运行记录：状态、错误类型与说明都保留，但不当成成功回答。"""

    record["status"] = status
    record["error"] = {"type": type(error).__name__, "message": str(error)}
    record["response"]["called_model"] = called_model
    return AnswerOutcome(
        status=status, record=record,
        message=f"{message}：{type(error).__name__}: {error}",
    )


def run_answer_question(
    query: str, known_at: date, *, dataset_path: Path | None = None,
    reference_path: Path | None = None,
    model_factory: Callable[[], ChatModel], query_id: str | None = None,
    snapshot: CorpusSnapshot | None = None, top_k: int = DEFAULT_TOP_K,
    loaded_snapshot: "LoadedSnapshot | None" = None, db_schema: str = DEFAULT_SCHEMA,
) -> AnswerOutcome:
    """跑一次完整回答：R3 检索 → 证据上下文 → 生成 → 引用守卫。

    语料可选文件（两份路径）或已读回的 `loaded_snapshot`（数据库路线，`db_schema`
    只用于记录实际来源）；两条路线在检索之后共用同一上下文、生成与引用守卫。
    不读 qrels，也不因失败改写回答、补造事实或更换模型：每种失败都有独立状态。
    输入、快照或语料不满足前提时抛 `ValueError`（由 CLI 当作输入错误处理）。
    """

    run = prepare_answer_run(
        query, known_at, dataset_path=dataset_path, reference_path=reference_path,
        snapshot=snapshot, top_k=top_k, loaded_snapshot=loaded_snapshot,
        db_schema=db_schema,
    )
    inputs = run.inputs
    context = build_evidence_context(
        inputs.query, inputs.hits, inputs.records, inputs.reference, inputs.sources,
    )
    record = _base_record(run, context, query_id)

    if not context.cases:
        # 没有候选可核对：本次不构造模型，也不把「没有候选」写成模型或运行故障。
        record["status"] = STATUS_NO_HITS
        record["answer"] = {
            "case_answers": [], "skipped_candidates": [], "current_gaps": [],
            "insufficiency": NO_CANDIDATE_INSUFFICIENCY,
        }
        return AnswerOutcome(
            status=STATUS_NO_HITS, record=record,
            message="本次检索没有返回候选历史案例，未调用模型；这不是运行故障，"
                    "也不表示已确认「没有相关案例」。",
        )

    try:
        model = model_factory()
    except ModelConfigError as error:
        return _failed(record, STATUS_MODEL_FAILED, error, called_model=False,
                       message="模型凭据或客户端配置缺失，本次没有发生调用")
    record["request"] = _request_payload(model)

    def remember_messages(messages):
        record["request"]["sent_messages"] = [asdict(message) for message in messages]
        record["request"]["rendered_prompt_sha256"] = text_sha256(messages[0].content)

    try:
        result = generate_grounded_answer(inputs.query, context, model,
                                          on_messages=remember_messages)
    except ModelError as error:
        # 模型层错误（超时、权限、网络、返回不可用）统一按调用失败处理。
        return _failed(record, STATUS_MODEL_FAILED, error, called_model=True,
                       message="模型调用失败（超时／权限／网络或返回不可用）")
    except AnswerFormatError as error:
        # 模型已经返回了文本与响应元数据，只是结构不合法：保留这些证据供排查，
        # 而不是让它们随异常丢失；但本次仍不作为成功回答输出。
        record["answer_text"] = error.raw_response
        if error.model_id is not None:
            record["response"]["model"] = error.model_id
        if error.usage is not None:
            record["response"]["usage"] = error.usage.to_dict()
        if error.elapsed_seconds is not None:
            record["response"]["elapsed_seconds"] = error.elapsed_seconds
        return _failed(record, STATUS_FORMAT_FAILED, error, called_model=True,
                       message="模型返回内容不符合回答契约，本次没有可用的结构化回答")

    record["response"] = _response_payload(result)
    record["answer"] = asdict(result.answer)
    record["answer_text"] = result.raw_response
    try:
        report = ensure_answer_citations(result.answer, context, require_relevance_source=True)
    except CitationError as error:
        record["citations"] = error.report.to_payload()
        return _failed(record, STATUS_CITATION_FAILED, error, called_model=True,
                       message="回答里有引用无法在本次上下文中定位，本次回答按失败处理，"
                               "不作为成功输出")
    record["citations"] = report.to_payload()
    record["status"] = STATUS_OK
    return AnswerOutcome(
        status=STATUS_OK, record=record,
        message=f"引用校验通过：检查 {report.checked} 条引用，未发现无法定位的引用；"
                "引用存在不等于引用支持原句，语义支持仍需人工审阅。",
    )


def format_answer_text(outcome: AnswerOutcome) -> str:
    """把运行记录渲染成可阅读文本；内容与 JSON 记录同源，不额外补事实。"""

    record = outcome.record
    query = record["query"]
    snapshot = record["snapshot"]
    corpus = record["corpus"]
    retrieval = record["retrieval"]
    label = f"{query['query_id']}｜" if query.get("query_id") else ""
    ranking = " > ".join(
        f"{item['rank']}.{item['case_id']}" for item in record["ranking"]
    ) or "（本次没有候选）"
    lines = [
        f"Query {label}{query['known_at']}：{query['text']}",
        f"数据源：{_data_source_label(corpus)}｜快照：{snapshot['snapshot_id']}｜"
        f"语料 {corpus['case_count']} 条｜审阅状态 {corpus['review_status']}（来源 Case 为 draft）",
        f"检索：{retrieval['requested_method']}（top_k={retrieval['top_k']}）｜"
        f"候选排名：{ranking}",
        "",
    ]
    answer = record["answer"] or {}
    for position, item in enumerate(answer.get("case_answers", []), start=1):
        lines.append(f"{position}. {item['case_id']}")
        lines.append(f"   相关理由：{item['relevance_reason']}")
        lines.append(f"   当前 Query 依据：{'；'.join(item['query_facts'])}")
        lines.append(f"   历史依据：{'；'.join(item['case_facts'])}")
        lines.append(f"   历史原因（该 Case 的记录）：{item['historical_root_cause'] or '（无记录）'}")
        lines.append("   历史改善措施（该 Case 的记录，不是当前建议）："
                     f"{item.get('historical_corrective_action') or '（无记录）'}")
        for evidence in item["historical_evidences"]:
            lines.append(f"   历史检查 {evidence['checkpoint_id']}：{evidence['result']}")
        sources = "、".join(
            f"{source['case_id']}:{source['field']}" for source in item["sources"]
        )
        lines.append(f"   来源：{sources}")
        lines.append("")
    for candidate in answer.get("skipped_candidates", []):
        lines.append(f"跳过候选 {candidate['case_id']}：{candidate['reason']}")
    if answer.get("skipped_candidates"):
        lines.append("")
    if answer.get("current_gaps"):
        lines.append("当前仍缺少的信息：")
        lines.extend(f"- {item}" for item in answer["current_gaps"])
        lines.append("")
    if answer.get("insufficiency"):
        lines.append(f"不足说明：{answer['insufficiency']}")
        lines.append("")
    citations = record["citations"]
    lines.append(
        f"引用校验：检查 {citations['checked']} 条，问题 {citations['issue_count']} 条"
        "（只确认引用可定位，不代表引用支持原句）"
    )
    lines.extend(
        f"- [{issue['kind']}] {issue['location']} {issue['detail']}"
        for issue in citations["issues"]
    )
    lines.append(f"运行状态：{record['status']}")
    lines.append("")
    lines.append("限制与边界：")
    lines.extend(f"- {item}" for item in record["limits"])
    return "\n".join(lines)


def resolve_query(
    args, *, loaded_snapshot: LoadedSnapshot | None = None,
) -> tuple[str, date]:
    """确定原始 Query 与 known_at：显式给出，或从选定路线按 query_id 读出。

    数据库路线使用已读回的 payload，不再次打开文件；示例查询必须被显式选取：
    这里不提供默认示例，也不从历史 Case 反推当前事实。
    """

    if args.query_id and args.query:
        raise ValueError("--query-id 与 --query 只能给一个：示例查询与显式输入不能混用")
    if args.query_id:
        if loaded_snapshot is None:
            payload = json.loads(Path(args.data).read_text(encoding="utf-8"))
            described = f"语料 {args.data}"
        else:
            payload = loaded_snapshot.payload
            described = f"数据库快照 {loaded_snapshot.snapshot.snapshot_id}"
        if not isinstance(payload, dict):
            raise ValueError(f"{described} 不是包含 queries 的 JSON 对象")
        queries = payload.get("queries", [])
        for query in queries:
            if query["query_id"] == args.query_id:
                return query["text"], date.fromisoformat(query["known_at"])
        raise ValueError(
            f"{described} 中没有 Query {args.query_id}；"
            f"可选：{[item['query_id'] for item in queries]}"
        )
    if not args.query:
        raise ValueError(
            "需要 --query 与 --known-at 提供当前已知信息，或用 --query-id 选取语料中的示例查询"
        )
    if args.known_at is None:
        raise ValueError("--query 必须与 --known-at 一起给出：不替本次运行补造当前时点")
    return args.query, args.known_at


def _report_inputs(query: str, known_at: date, args, *, loaded_snapshot=None) -> int:
    """`--check-only`：只检查输入与证据上下文，不构造模型、不写运行记录。"""

    try:
        run = prepare_answer_run(
            query, known_at, dataset_path=args.data, reference_path=args.reference,
            top_k=args.top_k, loaded_snapshot=loaded_snapshot, db_schema=args.db_schema,
        )
        context = build_evidence_context(
            run.inputs.query, run.inputs.hits, run.inputs.records,
            run.inputs.reference, run.inputs.sources,
        )
    except (ValueError, OSError) as error:
        print(f"运行前提不满足：{error}", file=sys.stderr)
        return EXIT_USAGE
    ranking = " > ".join(
        f"{item['rank']}.{item['case_id']}" for item in run.run_metadata["ranking"]
    ) or "（无候选）"
    print(f"Query（{known_at.isoformat()}）：{query}")
    print(f"数据源：{_data_source_label(run.run_metadata['corpus'])}｜"
          f"快照：{run.run_metadata['snapshot']['snapshot_id']}｜"
          f"语料 {run.run_metadata['corpus']['case_count']} 条")
    print(f"检索：{run.run_metadata['retrieval']['requested_method']}"
          f"（top_k={run.run_metadata['retrieval']['top_k']}）｜候选排名：{ranking}")
    print("上下文 Case：", "、".join(case.case_id for case in context.cases) or "（无）")
    print("上下文检查：", "、".join(
        f"{case.case_id}:{item.checkpoint_id}" for case in context.cases for item in case.evidences
    ) or "（无）")
    print(f"提示词版本：{PROMPT_VERSION}｜system 提示 {len(render_system_prompt())} 字符｜"
          f"证据 JSON {len(json.dumps(context_to_payload(context), ensure_ascii=False))} 字符")
    print("--check-only：未调用模型，未写运行记录。")
    return EXIT_OK


def run_answer_command(args) -> int:
    """`casetrace answer` 的参数层：选定数据源、解析输入、跑一次回答、按需保存记录。

    数据库路线在这里读回一次快照，示例查询解析、准备与 check-only 都复用同一对象，
    不重复读库；连接或读回失败按「回答前提不满足」退出，不调用模型、不写记录。
    """

    loaded_snapshot = None
    if args.data_source == DATA_SOURCE_POSTGRES:
        try:
            loaded_snapshot = load_database_snapshot(args.db_schema)
        except (ValueError, OSError) as error:
            print(f"运行前提不满足：{error}", file=sys.stderr)
            return EXIT_USAGE
    try:
        query, known_at = resolve_query(args, loaded_snapshot=loaded_snapshot)
    except (ValueError, OSError) as error:
        print(f"输入错误：{error}", file=sys.stderr)
        return EXIT_USAGE
    if args.check_only:
        return _report_inputs(query, known_at, args, loaded_snapshot=loaded_snapshot)

    try:
        outcome = run_answer_question(
            query, known_at, dataset_path=args.data, reference_path=args.reference,
            model_factory=lambda: build_model(args.model), query_id=args.query_id,
            top_k=args.top_k, loaded_snapshot=loaded_snapshot, db_schema=args.db_schema,
        )
    except (ValueError, OSError) as error:
        # 快照、输入或数据校验不通过：本次没有产生回答，因此不写运行记录。
        print(f"运行前提不满足：{error}", file=sys.stderr)
        return EXIT_USAGE

    written = None
    if args.output:
        try:
            written = write_report(outcome.record, args.output)
        except OSError as error:
            print(f"写入运行记录失败：{error}", file=sys.stderr)
            return EXIT_USAGE

    succeeded = outcome.status in (STATUS_OK, STATUS_NO_HITS)
    if args.json:
        print(json.dumps(outcome.record, ensure_ascii=False, indent=2))
    elif succeeded:
        print(format_answer_text(outcome))
    if not succeeded:
        # 失败状态不把回答正文写到标准输出，避免被下游当成成功回答。
        print(f"运行状态：{outcome.status}｜{outcome.message}", file=sys.stderr)
    if written:
        # JSON 模式下 stdout 必须是单一合法 JSON，落盘路径提示移到 stderr，
        # 不污染下游 `json.loads(stdout)`。
        if args.json:
            print(f"运行记录：{written}", file=sys.stderr)
        else:
            print(f"运行记录：{written}", file=sys.stdout if succeeded else sys.stderr)
    return outcome.exit_code
