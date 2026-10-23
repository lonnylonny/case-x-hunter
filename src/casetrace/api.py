"""M5-03 FastAPI：把已验收的回答核心接到本机 HTTP 边界。

本模块只做 HTTP 适配：请求字段校验、服务端连接配置、JSON 输出与状态映射。
检索、证据上下文、生成与引用守卫仍在 `answer` 核心（`run_answer_question`）里，
API 不复制业务流程；03 的 CSV 整体替换交给 storage，原始资料与 qrels 不被改写。

M6-02 在同一应用上增加 `GET /` 单页与 `/static/` 资源（`src/casetrace/web/`）：
页面按模块位置定位，读取时不连接数据库、不构造模型，浏览器用相对地址同源请求 `/answer`。
展示升级 02 增加 `/search` 复用检索及上下文核心，不触发生成；`/model-status`
只返回配置布尔值，不构造客户端、不连接数据库。

HTTP 边界（docs/development/api.md）：

- 服务固定走 PostgreSQL：连接沿用 `CASETRACE_DATABASE_URL` / 项目根 `.env`，
  schema 由应用工厂配置；客户端只提交问题，不提交连接、凭据、路径、schema、
  模型选择或数据源。
- 应用创建、模块导入与 `GET /health` 都不连接数据库、不构造模型；模型只在核心
  确需生成时才由注入的工厂构造。
- `POST /answer` 的请求体只有 `query`、`known_at`、`top_k` 三个字段，额外字段拒绝。

接口说明见 docs/development/api.md；真实 PostgreSQL 回放见 tests/storage/。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
import os
from pathlib import Path
import re
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
import psycopg
from starlette.concurrency import run_in_threadpool

from casetrace.answer.cli import (
    STATUS_CITATION_FAILED,
    STATUS_FORMAT_FAILED,
    STATUS_MODEL_FAILED,
    STATUS_NO_HITS,
    STATUS_OK,
    AnswerOutcome,
    build_model,
    load_database_snapshot,
    run_answer_question,
)
from casetrace.answer.context import (
    DEFAULT_TOP_K, DEV_V3_SNAPSHOT, SnapshotError,
    build_evidence_context, context_to_payload, prepare_answer_run,
)
from casetrace.answer.model import API_KEY_ENV, DEFAULT_MODEL, ChatModel, ModelConfigError
from casetrace.env import load_local_env
from casetrace.storage.schema import DEFAULT_SCHEMA
from casetrace.data.csv_import import CsvImportError, MAX_UPLOAD_BYTES, PLACEHOLDERS, csv_bytes
from casetrace.data.constants import CHECKPOINT_TYPES, DETECTION_STAGES, RELEVANCE_VALUES
from casetrace.storage.connection import connect
from casetrace.storage.snapshot import LoadedSnapshot, replace_csv_snapshot
from casetrace.settings import RuntimeSettings
from casetrace.storage.settings import load_settings, save_settings

# 模型工厂：核心在确需生成时才调用它；应用创建、health 与无效请求都不构造模型。
ModelFactory = Callable[[], ChatModel]

# `known_at` 只接受严格的 YYYY-MM-DD 文本；Pydantic 的 date 类型本身也会接受
# datetime 字符串与 Unix 时间戳，这里显式收窄到约定格式，避免把时间戳当日期。
_DATE_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}")

API_TITLE = "CaseTrace Answer API"
API_DESCRIPTION = (
    "把当前问题交给固定 R3 检索 + Grounded Answer 核心，返回可复查的完整运行记录。"
    "本机学习展示用；回答是历史参考，不判断当前 Incident 的最终 Root Cause。"
)

# M6-02 单页与静态资源：按本模块位置定位，因此与 uvicorn 的启动目录无关。
# 页面只由浏览器用相对地址 `/answer` 发起同源请求，不引入前端构建链或运行依赖。
WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"
STATIC_URL = "/static"


class HealthResponse(BaseModel):
    """`GET /health` 的响应；只证明 HTTP 进程能响应，不代表数据库或模型可用。"""

    status: str = "ok"


class ModelStatusResponse(BaseModel):
    """只报告是否有凭据或注入模型；不构造客户端、不验证服务可用性。"""

    configured: bool


class AnswerResponse(BaseModel):
    """检索与回答共用的响应外壳。

    顶层 `status` 与 `record["status"]` 一致；回答沿用核心完整运行记录，检索
    返回排名与证据且 answer=null。数据库前提失败时 `record` 为 null。
    """

    status: str
    message: str
    record: dict | None = None


class AnswerRequest(BaseModel):
    """`POST /answer` 的请求体：只有三个字段，额外字段一律拒绝（422）。

    `query` 保留原始文本，不改写、截断或从历史反填；`known_at` 必须与当前数据集
    的登记日期一致，读库后核对；`top_k` 是 1–10 的严格整数，省略时使用实例设置。
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{
            "query": "焊线脱落，已排除运输碰伤",
            "known_at": DEV_V3_SNAPSHOT.known_at.isoformat(), "top_k": DEFAULT_TOP_K,
        }]},
    )

    query: str = Field(
        min_length=1,
        description="当前问题原文；原样传给核心，不改写、不截断",
    )
    known_at: date = Field(
        description="语料可用时点 YYYY-MM-DD；须与 GET /dataset 的当前登记日期一致",
    )
    top_k: StrictInt | None = Field(
        default=None, gt=0, le=10,
        description="阅读前 k 条候选；省略时使用已保存的实例设置（初始为 4），显式值须为 1–10 的整数",
    )

    @field_validator("top_k", mode="before")
    @classmethod
    def _reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("top_k 请省略或填写 1–10 的整数，不接受 null")
        return value

    @field_validator("query")
    @classmethod
    def _reject_blank_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能是空白字符串")
        return value

    @field_validator("known_at", mode="before")
    @classmethod
    def _require_date_value(cls, value: object) -> object:
        # 只接受 YYYY-MM-DD 文本或真正的 date；datetime 与时间戳在这里拒绝。
        if isinstance(value, datetime):
            raise ValueError("known_at 只接受日期，不接受 datetime")
        if isinstance(value, date):
            return value
        if isinstance(value, str) and _DATE_TEXT.fullmatch(value):
            return value
        raise ValueError("known_at 必须是 YYYY-MM-DD 日期字符串")


class DatasetUploadMetadata(BaseModel):
    """随原始 CSV 上传的查询参数；不接受路径、schema、凭据或审阅状态。"""

    model_config = ConfigDict(extra="forbid")
    dataset_name: str = Field(min_length=1, max_length=120)
    known_at: date
    basis: str = Field(min_length=1, max_length=2000)
    filename: str = Field(min_length=1, max_length=255)

    @field_validator("known_at", mode="before")
    @classmethod
    def require_date(cls, value):
        return AnswerRequest._require_date_value(value)

    @field_validator("dataset_name", "basis", "filename")
    @classmethod
    def require_specific_text(cls, value: str) -> str:
        if not value.strip() or value.strip().casefold() in PLACEHOLDERS or "\x00" in value:
            raise ValueError("必须填写具体内容，不接受空白或占位")
        return value

    @field_validator("filename")
    @classmethod
    def require_csv_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or not value.lower().endswith(".csv"):
            raise ValueError("必须是 CSV 文件名，不能包含目录")
        return value


def dataset_info(loaded: LoadedSnapshot) -> dict:
    return {
        "name": loaded.payload.get("dataset_name", "内置合成示例 dev-v3"
                                   if loaded.snapshot.snapshot_id == DEV_V3_SNAPSHOT.snapshot_id
                                   else loaded.snapshot.snapshot_id),
        "snapshot_id": loaded.snapshot.snapshot_id,
        "case_count": len(loaded.records["cases"]),
        "detail_count": len(loaded.records["details"]),
        "evidence_count": len(loaded.records["evidences"]),
        "known_at": loaded.snapshot.known_at.isoformat(), "basis": loaded.snapshot.basis,
        "updated_at": loaded.snapshot.imported_at.isoformat(),
        "review_status": loaded.snapshot.review_status,
        "dataset_sha256": loaded.snapshot.dataset_sha256,
        "content_digest": loaded.snapshot.content_digest,
        "is_upload": loaded.snapshot.split == "user_upload",
    }


def unsupported_date(request: AnswerRequest, loaded: LoadedSnapshot) -> JSONResponse | None:
    if request.known_at != loaded.snapshot.known_at:
        return JSONResponse(status_code=422, content={"detail": [{
            "loc": ["body", "known_at"],
            "msg": f"当前数据集仅支持 known_at={loaded.snapshot.known_at.isoformat()}；请刷新数据状态。",
            "type": "value_error",
        }]})
    return None


# HTTP 边界状态：`service_unavailable`/`internal_error` 只用于网络边界，
# 不加入 `AnswerOutcome` 或 CLI 退出码，核心业务状态保持原样。
SERVICE_UNAVAILABLE = "service_unavailable"
INTERNAL_ERROR = "internal_error"

# 数据库前提失败（配置／连接／未导入／内容或快照不符）统一给安全、可操作的说明，
# 不回显连接串或原始异常。
DB_PREREQUISITE_MESSAGE = (
    "数据库路线暂不可用：请确认 CASETRACE_DATABASE_URL、数据库连接与初始化／导入状态后重试。"
)
UNEXPECTED_MESSAGE = "服务端发生未预期错误，本次请求未完成；请稍后重试。"
MODEL_CONFIG_MESSAGE = "服务端模型凭据或客户端配置不可用，本次没有发生模型调用。"
MODEL_FAILURE_MESSAGE = "模型调用失败（超时／权限／网络或返回不可用）；原始异常文本未在此返回。"
FORMAT_FAILURE_MESSAGE = "模型返回内容不符合回答契约；原始异常文本未在此返回。"
CITATION_FAILURE_MESSAGE = "回答里的引用无法在本次上下文中定位；原始异常文本未在此返回。"

# 示例只展示响应外壳与部分记录字段；运行时仍返回完整核心记录。
ANSWER_RESPONSES = {
    200: {
        "description": "成功或空命中；示例中的 record 省略其余运行记录字段",
        "content": {"application/json": {"examples": {
            "ok": {"summary": "成功（记录简写）", "value": {
                "status": STATUS_OK, "message": "引用校验通过；回答仅供历史参考",
                "record": {"status": STATUS_OK},
            }},
            "no_hits": {"summary": "空命中，不调用模型（记录简写）", "value": {
                "status": STATUS_NO_HITS, "message": "没有候选历史案例，未调用模型",
                "record": {
                    "status": STATUS_NO_HITS, "ranking": [], "answer_text": None,
                    "answer": {"case_answers": [], "insufficiency": "缺少可用历史案例证据"},
                },
            }},
        }}},
    },
    422: {"description": "请求校验失败：不读数据库、不构造模型"},
    502: {"model": AnswerResponse,
          "description": "模型调用、返回格式或引用校验失败：保留核心状态与失败证据"},
    503: {
        "model": AnswerResponse,
        "description": "模型配置不可用（model_failed），或数据库/快照前提失败"
                       "（service_unavailable、record=null）；示例中的 record 省略其余字段",
        "content": {"application/json": {"example": {
            "status": STATUS_MODEL_FAILED, "message": MODEL_CONFIG_MESSAGE,
            "record": {
                "status": STATUS_MODEL_FAILED,
                "error": {"type": "ModelConfigError", "message": MODEL_CONFIG_MESSAGE},
                "response": {"called_model": False},
            },
        }}},
    },
    500: {"model": AnswerResponse,
          "description": "未预期异常：通用安全说明，不暴露 traceback 或原始异常文本"},
}

# 核心失败状态 → HTTP 状态与固定安全说明；说明与状态保持一致。
FAILURE_HTTP_STATUS = {
    STATUS_MODEL_FAILED: 502,
    STATUS_FORMAT_FAILED: 502,
    STATUS_CITATION_FAILED: 502,
}
FAILURE_SAFE_MESSAGE = {
    STATUS_MODEL_FAILED: MODEL_FAILURE_MESSAGE,
    STATUS_FORMAT_FAILED: FORMAT_FAILURE_MESSAGE,
    STATUS_CITATION_FAILED: CITATION_FAILURE_MESSAGE,
}


def _json_response(status_code: int, status: str, message: str,
                   record: dict | None) -> JSONResponse:
    """统一的响应外壳：顶层 status／message 与 record 内的状态一一对应。"""

    return JSONResponse(
        status_code=status_code,
        content={"status": status, "message": message, "record": record},
    )


def _redacted_record(record: dict, message: str) -> dict:
    """返回记录副本并把 `error.message` 换成固定安全说明。

    模型失败记录含 SDK 异常原文；这里只改副本，不修改核心对象、不改变既有 CLI 行为，
    错误类型、调用标记与其余证据（ranking／context／messages／answer_text 等）原样保留。
    """

    safe = dict(record)
    error = safe.get("error")
    if isinstance(error, dict):
        safe["error"] = {**error, "message": message}
    return safe


def _outcome_response(outcome: AnswerOutcome) -> JSONResponse:
    """把 `AnswerOutcome` 映射成 JSON 与 HTTP 状态。

    - `ok`／`no_hits`：200，返回完整记录；空命中不构造模型。
    - `ModelConfigError` 导致的 `model_failed`：503，服务端模型配置不可用。
    - 其余 `model_failed`／`format_failed`／`citation_failed`：502，保留核心状态与证据。
    - 未知状态：不伪装成业务状态，按未预期异常处理。
    """

    if outcome.status in (STATUS_OK, STATUS_NO_HITS):
        return _json_response(200, outcome.status, outcome.message, outcome.record)
    if outcome.status not in FAILURE_SAFE_MESSAGE:
        return _json_response(500, INTERNAL_ERROR, UNEXPECTED_MESSAGE, None)
    error_type = (outcome.record.get("error") or {}).get("type")
    if outcome.status == STATUS_MODEL_FAILED and error_type == ModelConfigError.__name__:
        return _json_response(503, outcome.status, MODEL_CONFIG_MESSAGE,
                              _redacted_record(outcome.record, MODEL_CONFIG_MESSAGE))
    message = FAILURE_SAFE_MESSAGE[outcome.status]
    return _json_response(FAILURE_HTTP_STATUS[outcome.status], outcome.status, message,
                          _redacted_record(outcome.record, message))


def read_runtime_settings(schema: str) -> RuntimeSettings:
    with connect() as connection:
        return load_settings(connection, schema=schema)


def create_app(
    *, db_schema: str = DEFAULT_SCHEMA, model_factory: ModelFactory | None = None,
) -> FastAPI:
    """构造一个 FastAPI 应用；不连接数据库、不构造模型。

    `db_schema` 由服务端配置（客户端不可提交）；`model_factory` 是可调用对象，
    核心在确需生成时才调用它，默认复用 `build_model(DEFAULT_MODEL)`。
    """

    app = FastAPI(title=API_TITLE, version="0.1.0", description=API_DESCRIPTION)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # 保留字段定位与原因，不把意外提交的凭据通过默认 input/ctx 字段回显。
        return JSONResponse(status_code=422, content={"detail": [
            {key: item[key] for key in ("loc", "msg", "type")}
            for item in error.errors()
        ]})
    # 配置只挂在 app.state：写到哪、路由就从哪读，避免出现第二份会漂移的来源。
    app.state.db_schema = db_schema
    default_model_factory = lambda: build_model(DEFAULT_MODEL)
    app.state.model_factory = model_factory or default_model_factory
    app.state.model_factory_supplied = model_factory is not None

    # 静态资源目录按模块位置定位；挂载只登记目录，不读库、不构造模型。
    app.mount(STATIC_URL, StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """单页入口：返回本模块旁的 HTML 文件，不连接数据库、不构造模型。"""

        return FileResponse(INDEX_HTML, media_type="text/html")

    @app.get("/health", response_model=HealthResponse, summary="进程存活检查")
    def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/dataset/template", summary="下载唯一 CSV 模板")
    def dataset_template() -> Response:
        return Response(csv_bytes(), media_type="text/csv; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="casetrace-template.csv"',
        })

    @app.get("/dataset/example", summary="下载完整合成示例（1 Case、2 Detail、3 Evidence）")
    def dataset_example() -> FileResponse:
        return FileResponse(WEB_DIR / "import-example.csv", media_type="text/csv; charset=utf-8",
                            filename="casetrace-example.csv")

    @app.get("/settings", include_in_schema=False)
    def settings() -> FileResponse:
        return FileResponse(WEB_DIR / "settings.html", media_type="text/html")

    @app.get("/dataset", summary="当前数据集状态")
    def current_dataset() -> JSONResponse:
        try:
            return JSONResponse(dataset_info(load_database_snapshot(app.state.db_schema)))
        except (ValueError, OSError):
            return _json_response(503, SERVICE_UNAVAILABLE, DB_PREREQUISITE_MESSAGE, None)
        except Exception:
            return _json_response(500, INTERNAL_ERROR, UNEXPECTED_MESSAGE, None)

    @app.get("/dataset/reference", summary="当前合法主数据与枚举")
    def dataset_reference() -> JSONResponse:
        try:
            loaded = load_database_snapshot(app.state.db_schema)
            reference = loaded.reference
            return JSONResponse({
                "products": [{**item, "customer_id": reference.product_customers[key]}
                             for key, item in reference.products.items()],
                "failure_modes": [{"failure_mode_id": key, "failure_mode": item["failure_mode"],
                                   "applicable_package": item["applicable_package"]}
                                  for key, item in reference.failure_modes.items()],
                "processes": reference.processes,
                "route_processes": {key: sorted(value) for key, value in reference.route_processes.items()},
                "detection_stages": DETECTION_STAGES, "checkpoint_types": CHECKPOINT_TYPES,
                "relevance_values": RELEVANCE_VALUES,
            })
        except (ValueError, OSError):
            return _json_response(503, SERVICE_UNAVAILABLE, DB_PREREQUISITE_MESSAGE, None)
        except Exception:
            return _json_response(500, INTERNAL_ERROR, UNEXPECTED_MESSAGE, None)

    def perform_upload(raw: bytes, metadata: DatasetUploadMetadata) -> JSONResponse:
        try:
            with connect() as connection:
                loaded = replace_csv_snapshot(connection, raw, schema=app.state.db_schema,
                                              **metadata.model_dump())
            return JSONResponse({"status": "imported", "message": "当前数据集已整体替换。",
                                 "dataset": dataset_info(loaded)})
        except CsvImportError as error:
            return JSONResponse(status_code=422, content={
                "status": "invalid_dataset", "message": "未导入，原数据保持可查询。",
                "errors": error.errors,
            })
        except (ValueError, OSError, RuntimeError, psycopg.Error):
            return _json_response(503, SERVICE_UNAVAILABLE,
                                  "替换未完成，原数据未被本次上传修改；请检查数据库状态。", None)
        except Exception:
            return _json_response(500, INTERNAL_ERROR, UNEXPECTED_MESSAGE, None)

    @app.post("/dataset", summary="上传唯一 CSV 格式并原子替换当前数据集")
    async def upload_dataset(request: Request,
                             metadata: Annotated[DatasetUploadMetadata, Query()]) -> JSONResponse:
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_UPLOAD_BYTES:
                return JSONResponse(status_code=413, content={
                    "status": "invalid_dataset", "message": "文件超过 5 MiB，未导入。",
                })
            raw.extend(chunk)
        return await run_in_threadpool(perform_upload, bytes(raw), metadata)

    @app.get("/model-status", response_model=ModelStatusResponse, summary="模型配置状态（不调用模型）")
    def model_status() -> ModelStatusResponse:
        if app.state.model_factory_supplied:
            return ModelStatusResponse(configured=True)
        load_local_env()
        return ModelStatusResponse(configured=bool(os.environ.get(API_KEY_ENV, "").strip()))

    @app.get("/runtime-settings", response_model=RuntimeSettings)
    def runtime_settings() -> RuntimeSettings | JSONResponse:
        try:
            return read_runtime_settings(app.state.db_schema)
        except (ValueError, OSError, RuntimeError, psycopg.Error):
            return _json_response(503, SERVICE_UNAVAILABLE,
                                  "无法读取实例设置；请检查数据库并运行启动准备。", None)

    @app.put("/runtime-settings", response_model=RuntimeSettings)
    def update_runtime_settings(settings: RuntimeSettings) -> RuntimeSettings | JSONResponse:
        try:
            with connect() as connection:
                saved = save_settings(connection, settings, schema=app.state.db_schema)
            return saved
        except (ValueError, OSError, RuntimeError, psycopg.Error):
            return _json_response(503, SERVICE_UNAVAILABLE,
                                  "无法确认设置已保存；请检查数据库并重新读取设置。", None)

    @app.post(
        "/search", response_model=AnswerResponse, summary="检索历史案例与证据，不调用模型",
        responses={503: {"model": AnswerResponse, "description": "数据库或快照前提不可用"},
                   500: {"model": AnswerResponse, "description": "未预期错误"}},
    )
    def search(request: AnswerRequest) -> JSONResponse:
        try:
            schema = app.state.db_schema
            try:
                settings = read_runtime_settings(schema)
            except (ValueError, OSError, RuntimeError, psycopg.Error):
                return _json_response(503, SERVICE_UNAVAILABLE, "无法读取实例设置，请检查数据库并运行启动准备。", None)
            top_k = request.top_k if request.top_k is not None else settings.top_k
            try:
                loaded = load_database_snapshot(schema)
            except (ValueError, OSError):
                return _json_response(503, SERVICE_UNAVAILABLE, DB_PREREQUISITE_MESSAGE, None)
            date_error = unsupported_date(request, loaded)
            if date_error is not None:
                return date_error
            run = prepare_answer_run(
                request.query, request.known_at, top_k=top_k,
                loaded_snapshot=loaded, db_schema=schema,
            )
            inputs = run.inputs
            context = build_evidence_context(
                inputs.query, inputs.hits, inputs.records, inputs.reference, inputs.sources,
            )
            status = "retrieved" if context.cases else STATUS_NO_HITS
            record = {
                "record_version": "search-run-1", "status": status,
                "query": {"text": request.query, "known_at": request.known_at.isoformat()},
                **run.run_metadata,
                "context": context_to_payload(context),
                "response": {"called_model": False}, "answer": None,
            }
            message = (
                "已检索历史候选及证据原文，未调用模型；候选不等于已确认相关案例。"
                if context.cases else "没有检索候选，未调用模型；这不表示资料库不存在相关案例。"
            )
            return _json_response(200, status, message, record)
        except SnapshotError:
            return _json_response(503, SERVICE_UNAVAILABLE, DB_PREREQUISITE_MESSAGE, None)
        except Exception:
            return _json_response(500, INTERNAL_ERROR, UNEXPECTED_MESSAGE, None)

    @app.post(
        "/answer", response_model=AnswerResponse, responses=ANSWER_RESPONSES,
        summary="回答一个历史案例检索问题",
    )
    def answer(request: AnswerRequest) -> JSONResponse:
        # 请求已通过 Pydantic 校验；这里固定走 PostgreSQL，一次短连接读回快照，
        # 关闭连接后再把同一对象与原始 query／日期／top_k 交给回答核心。
        try:
            schema = app.state.db_schema
            try:
                settings = read_runtime_settings(schema)
            except (ValueError, OSError, RuntimeError, psycopg.Error):
                return _json_response(503, SERVICE_UNAVAILABLE, "无法读取实例设置，请检查数据库并运行启动准备。", None)
            top_k = request.top_k if request.top_k is not None else settings.top_k
            # 配置为本次请求的不可变快照；只在有候选、确需生成时构造模型。
            model_factory = app.state.model_factory if app.state.model_factory is not default_model_factory else (
                lambda: build_model(DEFAULT_MODEL, max_tokens=settings.max_tokens)
            )
            try:
                loaded = load_database_snapshot(schema)
            except (ValueError, OSError):
                # 只在读库入口捕获配置、连接、未导入或内容检查失败。
                return _json_response(503, SERVICE_UNAVAILABLE, DB_PREREQUISITE_MESSAGE, None)
            date_error = unsupported_date(request, loaded)
            if date_error is not None:
                return date_error
            outcome = run_answer_question(
                request.query, request.known_at,
                model_factory=model_factory, query_id=None, top_k=top_k,
                loaded_snapshot=loaded, db_schema=schema,
            )
            return _outcome_response(outcome)
        except SnapshotError:
            # 核心明确拒绝快照身份或时点；其它 ValueError/OSError 留给 500 兜底。
            return _json_response(503, SERVICE_UNAVAILABLE, DB_PREREQUISITE_MESSAGE, None)
        except Exception:
            # 兜底：不暴露 traceback 或原始异常文本，也不伪装成业务状态。
            return _json_response(500, INTERNAL_ERROR, UNEXPECTED_MESSAGE, None)

    return app


app = create_app()
