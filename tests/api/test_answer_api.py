"""M5-03 SD1：HTTP 契约测试——无数据库、无模型的 health 与请求校验。

这些用例只验证接口增量：应用创建、`/health`、OpenAPI 与无效请求的 422。
用例通过替身把「连接数据库」和「构造模型」都变成失败，因此任何一次意外调用
都会被直接暴露；回答核心的业务细节仍由既有 core 测试覆盖，这里不重复。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
import json

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from casetrace import api
from casetrace.api import AnswerRequest, app, create_app
from casetrace.answer.cli import (
    STATUS_CITATION_FAILED,
    STATUS_FORMAT_FAILED,
    STATUS_MODEL_FAILED,
    STATUS_NO_HITS,
    STATUS_OK,
)
from casetrace.answer.model import (
    ModelCallError,
    ModelConfigError,
    OfflineChatModel,
    TokenUsage,
)
from casetrace.storage.schema import DEFAULT_SCHEMA

KNOWN_AT = "2026-09-15"
# 原始 query 含否定小句与背景信息：核心必须原样收到它，不改写也不裁剪。
QUERY = "焊线脱落，已排除运输碰伤"
NO_HIT_QUERY = "zzz 完全没有词项重合的查询 qqqq"


def _forbidden_model_factory():
    """被调用即说明无效请求或 health 意外触发了模型构造。"""

    raise AssertionError("本用例不应构造模型")


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """任何数据库连接都视为失败：本模块的用例都不应读库。"""

    def fail(*args, **kwargs):
        raise AssertionError("本用例不应连接数据库")

    monkeypatch.setattr(psycopg, "connect", fail)


@pytest.fixture
def client():
    return TestClient(create_app(model_factory=_forbidden_model_factory))


def test_health_ok_without_dependencies(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_default_app_uses_expected_schema_and_lazy_factory():
    # 默认应用与模块级 app 都是「配置就绪但未构造模型」的状态。
    assert isinstance(app.state.db_schema, str)
    assert app.state.db_schema == DEFAULT_SCHEMA
    assert callable(app.state.model_factory)


def test_docs_and_openapi_are_available(client):
    schema = client.get("/openapi.json").json()
    post = schema["paths"]["/answer"]["post"]

    assert client.get("/docs").status_code == 200
    assert "get" in schema["paths"]["/health"]
    for code in ("200", "422", "502", "503", "500"):
        assert code in post["responses"]


def test_openapi_documents_request_contract(client):
    request_schema = client.get("/openapi.json").json()["components"]["schemas"]["AnswerRequest"]

    assert request_schema["additionalProperties"] is False
    assert set(request_schema["required"]) == {"query", "known_at"}
    assert request_schema["properties"]["known_at"]["format"] == "date"
    assert request_schema["properties"]["top_k"].get("default") is None
    integer = request_schema["properties"]["top_k"]["anyOf"][0]
    assert integer["exclusiveMinimum"] == 0
    assert integer["maximum"] == 10


def test_openapi_has_request_and_response_examples(client):
    schema = client.get("/openapi.json").json()
    request = schema["components"]["schemas"]["AnswerRequest"]
    assert AnswerRequest(**request["examples"][0]).top_k == 4
    responses = schema["paths"]["/answer"]["post"]["responses"]
    examples = responses["200"]["content"]["application/json"]["examples"]
    assert examples["ok"]["value"]["status"] == STATUS_OK
    assert examples["no_hits"]["value"]["record"]["answer"]["insufficiency"]
    failure = responses["503"]["content"]["application/json"]["example"]
    assert failure["status"] == failure["record"]["status"] == STATUS_MODEL_FAILED
    assert failure["record"]["error"]["type"] == "ModelConfigError"
    assert failure["record"]["response"]["called_model"] is False


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"json": {"known_at": "2026-09-15"}}, id="missing-query"),
        pytest.param({"json": {"query": "焊线脱落"}}, id="missing-known-at"),
        pytest.param({"json": {"query": "", "known_at": "2026-09-15"}}, id="empty-query"),
        pytest.param({"json": {"query": "   ", "known_at": "2026-09-15"}}, id="blank-query"),
        pytest.param({"json": {"query": 123, "known_at": "2026-09-15"}}, id="non-string-query"),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": "2026/09/15"}}, id="wrong-date-format",
        ),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": "2026-09-15T00:00:00"}},
            id="datetime-known-at",
        ),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": 1757894400}}, id="timestamp-known-at",
        ),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": "2026-09-15", "top_k": 0}},
            id="top-k-zero",
        ),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": "2026-09-15", "top_k": -1}},
            id="top-k-negative",
        ),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": "2026-09-15", "top_k": 4.0}},
            id="top-k-float",
        ),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": "2026-09-15", "top_k": "4"}},
            id="top-k-string",
        ),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": "2026-09-15", "top_k": True}},
            id="top-k-bool",
        ),
        pytest.param(
            {"json": {"query": "焊线脱落", "known_at": "2026-09-15", "extra": 1}},
            id="extra-field",
        ),
        pytest.param(
            {"content": b"{not json", "headers": {"Content-Type": "application/json"}},
            id="invalid-json",
        ),
    ],
)
def test_invalid_request_is_422_without_dependencies(client, kwargs):
    response = client.post("/answer", **kwargs)

    assert response.status_code == 422
    # 使用框架的 422 detail，不另建一套错误框架。
    assert response.json()["detail"]


def test_request_defaults_and_query_original_text_preserved():
    request = AnswerRequest(query="  焊线脱落，已排除运输碰伤  ", known_at="2026-09-15")

    assert request.top_k is None  # 省略时在请求开始读取已保存设置
    # 不改写、不截断：前后空白也不能被裁掉。
    assert request.query == "  焊线脱落，已排除运输碰伤  "


def test_request_accepts_real_date_but_rejects_datetime():
    # 字段声明为 date，程序内构造也应接受真正的 date；datetime 明确拒绝。
    request = AnswerRequest(query="焊线脱落", known_at=date(2026, 9, 15))
    assert request.known_at == date(2026, 9, 15)

    with pytest.raises(ValidationError):
        AnswerRequest(query="焊线脱落", known_at=datetime(2026, 9, 15, 0, 0))


# ── SD2：接到现有核心（一次读回、同一核心、状态与安全映射） ───────────────

def answer_text(case_id="C1") -> str:
    """符合回答契约的最小回答；引用本次上下文里真实存在的 Case 与 checkpoint。"""

    checkpoint = f"E{case_id[1:]}"
    return json.dumps({
        "case_answers": [{
            "case_id": case_id,
            "relevance_reason": "同产品且异常描述相同",
            "query_facts": ["涉及产品 P1"],
            "case_facts": ["焊线脱落，表面污染"],
            "historical_root_cause": "表面污染",
            "historical_corrective_action": None,
            "historical_evidences": [{"checkpoint_id": checkpoint, "result": "观察到表面污染"}],
            "sources": [
                {"case_id": case_id, "field": "abnormal_description"},
                {"case_id": case_id, "field": f"checkpoint:{checkpoint}"},
            ],
        }],
        "skipped_candidates": [],
        "current_gaps": [],
        "insufficiency": None,
    }, ensure_ascii=False)


class CannedModel(OfflineChatModel):
    """离线替身：回放固定回答，并带真实适配器才有的请求参数属性。"""

    def __init__(self, text, *, model_id="offline-stub"):
        super().__init__(text, model_id=model_id,
                         usage=TokenUsage(prompt_tokens=120, completion_tokens=60,
                                          total_tokens=180))
        self.max_tokens = 2048
        self.temperature = 0.0


class FailingModel:
    """调用直接失败的替身；异常文本里的凭据片段不得出现在响应里。"""

    model_id = "failing-stub"

    def __init__(self, message):
        self._message = message

    def complete(self, messages, *, max_tokens=None, temperature=None):
        raise ModelCallError(self._message)


@pytest.fixture
def snapshot_loader(monkeypatch, loaded_snapshot):
    """替换服务端快照读回入口，并记录每次请求传入的 schema（验证一次读取）。"""

    calls = []

    def loader(schema):
        calls.append(schema)
        return loaded_snapshot

    monkeypatch.setattr(api, "load_database_snapshot", loader)
    return calls


@pytest.fixture
def make_client():
    def _make(model_factory):
        return TestClient(create_app(model_factory=model_factory))

    return _make


def _post(client, query=QUERY, **extra):
    return client.post("/answer", json={"query": query, "known_at": KNOWN_AT, **extra})


def test_app_state_schema_is_the_one_used_by_the_route(snapshot_loader):
    # app.state 是唯一配置来源：工厂传入的 schema 必须原样到达快照读回。
    client = TestClient(create_app(db_schema="casetrace_alt",
                                   model_factory=lambda: CannedModel(answer_text("C1"))))

    response = _post(client)

    assert response.status_code == 200
    assert snapshot_loader == ["casetrace_alt"]
    assert response.json()["record"]["corpus"]["schema"] == "casetrace_alt"


def test_ok_returns_record_and_original_query(make_client, snapshot_loader):
    model = CannedModel(answer_text("C1"))

    response = _post(make_client(lambda: model))

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == STATUS_OK
    assert body["record"]["status"] == STATUS_OK
    assert body["record"]["answer"]["case_answers"]
    assert body["record"]["citations"]["issue_count"] == 0
    assert body["record"]["corpus"]["data_source"] == "postgres"
    # 一次读回、用服务端配置的 schema；原始 query 与默认 top_k 原样交给核心。
    assert snapshot_loader == [DEFAULT_SCHEMA]
    assert QUERY in model.sent_text()
    assert body["record"]["retrieval"]["top_k"] == 4
    assert "postgresql://" not in response.text


def test_no_hits_returns_200_without_model(make_client, snapshot_loader):
    def explode():
        raise AssertionError("空命中不应构造模型")

    response = _post(make_client(explode), query=NO_HIT_QUERY)

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == STATUS_NO_HITS
    assert body["record"]["status"] == STATUS_NO_HITS
    assert body["record"]["answer"]["insufficiency"]


def test_model_config_failure_is_503(make_client, snapshot_loader):
    fragment = "sk-FICTIONAL-SECRET-1"

    def factory():
        raise ModelConfigError(f"缺少 DeepSeek 凭据：{fragment}")

    response = _post(make_client(factory))

    body = response.json()
    assert response.status_code == 503
    assert body["status"] == STATUS_MODEL_FAILED
    assert body["record"]["status"] == STATUS_MODEL_FAILED
    assert body["record"]["error"]["type"] == "ModelConfigError"
    assert body["record"]["response"]["called_model"] is False
    assert body["record"]["error"]["message"] == body["message"]
    assert fragment not in response.text


def test_model_call_failure_is_502_and_redacted(make_client, snapshot_loader):
    fragment = "sk-FICTIONAL-SECRET-2"
    model = FailingModel(f"DeepSeek 调用失败：ConnectError: Authorization: Bearer {fragment}")

    response = _post(make_client(lambda: model))

    body = response.json()
    assert response.status_code == 502
    assert body["status"] == STATUS_MODEL_FAILED
    assert body["record"]["status"] == STATUS_MODEL_FAILED
    assert body["record"]["error"]["type"] == "ModelCallError"
    assert body["record"]["response"]["called_model"] is True
    assert body["record"]["ranking"]  # 其余失败证据保留
    assert body["record"]["error"]["message"] == body["message"]
    assert fragment not in response.text
    assert body["status"] != STATUS_NO_HITS


def test_format_failure_is_502_and_keeps_raw_response(make_client, snapshot_loader):
    response = _post(make_client(lambda: CannedModel("这不是约定的 JSON 结构")))

    body = response.json()
    assert response.status_code == 502
    assert body["status"] == STATUS_FORMAT_FAILED
    assert body["record"]["status"] == STATUS_FORMAT_FAILED
    assert body["record"]["answer_text"] == "这不是约定的 JSON 结构"
    assert body["record"]["error"]["message"] == body["message"]
    assert body["status"] != STATUS_NO_HITS


def test_citation_failure_is_502(make_client, snapshot_loader):
    payload = json.loads(answer_text("C1"))
    payload["case_answers"][0]["historical_evidences"] = [
        {"checkpoint_id": "E999", "result": "不存在的检查点"}
    ]
    payload["case_answers"][0]["sources"] = [
        {"case_id": "C1", "field": "abnormal_description"},
        {"case_id": "C1", "field": "checkpoint:E999"},
    ]

    response = _post(make_client(lambda: CannedModel(json.dumps(payload, ensure_ascii=False))))

    body = response.json()
    assert response.status_code == 502
    assert body["status"] == STATUS_CITATION_FAILED
    assert body["record"]["status"] == STATUS_CITATION_FAILED
    assert body["record"]["citations"]["issue_count"] > 0
    assert body["record"]["error"]["message"] == body["message"]
    assert body["status"] != STATUS_NO_HITS


def test_database_prerequisite_failure_is_503_and_redacted(monkeypatch, make_client):
    fragment = "sk-FICTIONAL-SECRET-4"

    def loader(schema):
        # 即使底层异常里出现了连接串/凭据片段，响应也只能给固定安全说明。
        raise ValueError(f"数据库路线不可用：postgresql://user:{fragment}@host/db")

    def explode():
        raise AssertionError("前提失败不应构造模型")

    monkeypatch.setattr(api, "load_database_snapshot", loader)

    response = _post(make_client(explode))

    body = response.json()
    assert response.status_code == 503
    assert body["status"] == api.SERVICE_UNAVAILABLE
    assert body["record"] is None
    assert fragment not in response.text
    assert "postgresql://" not in response.text


def test_unexpected_error_is_safe_500(monkeypatch, make_client):
    fragment = "sk-FICTIONAL-SECRET-3"

    def loader(schema):
        raise KeyError(fragment)

    monkeypatch.setattr(api, "load_database_snapshot", loader)

    response = _post(make_client(lambda: CannedModel(answer_text("C1"))))

    body = response.json()
    assert response.status_code == 500
    assert body["status"] == api.INTERNAL_ERROR
    assert body["record"] is None
    assert fragment not in response.text
    assert "Traceback" not in response.text


@pytest.mark.parametrize("error_type", [ValueError, OSError])
def test_model_factory_unexpected_error_is_500(make_client, snapshot_loader, error_type):
    fragment = "sk-FICTIONAL-FACTORY-SECRET"

    def factory():
        raise error_type(fragment)

    response = _post(make_client(factory))

    assert response.status_code == 500
    assert response.json() == {
        "status": api.INTERNAL_ERROR, "message": api.UNEXPECTED_MESSAGE, "record": None,
    }
    assert fragment not in response.text
    assert snapshot_loader == [DEFAULT_SCHEMA]


def test_invalid_model_proxy_is_500(monkeypatch, snapshot_loader):
    # 默认模型工厂的真实 SDK 构造失败；代理 URL 无效，在任何网络调用前就失败。
    monkeypatch.setattr("casetrace.answer.model.load_local_env", lambda: set())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-FICTIONAL-PROXY-SECRET")
    monkeypatch.setenv("HTTPS_PROXY", "invalidscheme://127.0.0.1:1")

    response = _post(TestClient(create_app()))

    assert response.status_code == 500
    assert response.json()["status"] == api.INTERNAL_ERROR
    assert "sk-FICTIONAL-PROXY-SECRET" not in response.text
    assert "invalidscheme" not in response.text


def test_response_serialization_error_is_500(make_client, snapshot_loader):
    model = CannedModel(answer_text())
    # 模拟不可序列化的供应商用量，触发 JSONResponse 的 ValueError；核心确实已调用模型。
    model.usage = TokenUsage(prompt_tokens=float("nan"))

    response = _post(make_client(lambda: model))

    assert len(model.calls) == 1
    assert response.status_code == 500
    assert response.json()["status"] == api.INTERNAL_ERROR
    assert response.json()["record"] is None


@pytest.mark.parametrize("problem", ["identity", "detection_time"])
def test_snapshot_prerequisite_failure_stays_503(
    monkeypatch, loaded_snapshot, make_client, problem,
):
    if problem == "identity":
        loaded = replace(loaded_snapshot, snapshot=replace(
            loaded_snapshot.snapshot, snapshot_id="unsupported-snapshot",
        ))
    else:
        loaded = replace(loaded_snapshot, records={
            **loaded_snapshot.records,
            "details": [replace(detail, detection_time=date(2026, 9, 16))
                        for detail in loaded_snapshot.records["details"]],
        })
    monkeypatch.setattr(api, "load_database_snapshot", lambda schema: loaded)

    response = _post(make_client(_forbidden_model_factory))

    assert response.status_code == 503
    assert response.json() == {
        "status": api.SERVICE_UNAVAILABLE,
        "message": api.DB_PREREQUISITE_MESSAGE, "record": None,
    }


def test_two_requests_keep_their_own_query_and_reload(make_client, snapshot_loader):
    model = CannedModel(answer_text("C1"))
    client = make_client(lambda: model)

    first = _post(client, query=QUERY).json()
    second = _post(client, query="焊线脱落", top_k=2).json()

    assert first["record"]["query"]["text"] == QUERY
    assert first["record"]["query"]["query_id"] is None
    assert second["record"]["query"]["text"] == "焊线脱落"
    assert second["record"]["retrieval"]["top_k"] == 2
    # 同一 app 的两次请求各自读回一次，不缓存、不互相覆盖。
    assert snapshot_loader == [DEFAULT_SCHEMA, DEFAULT_SCHEMA]
