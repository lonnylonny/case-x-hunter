"""M5-03 SD3 真实 PostgreSQL 对照：HTTP 接口与文件路线必须等价。

只在真实测试库上运行（`CASETRACE_TEST_DATABASE_URL`），每个用例独占一个 schema；
缺库时为普通回归显式 skip，但真实 PG 对照未执行不能验收通过。

服务的连接配置在夹具里临时指向测试库：`create_app` 不接收 URL，它按常规用
`CASETRACE_DATABASE_URL` 建立自己的短连接读回，所以这里检验的是真实 DB 链路，
而不是预先构造好的快照对象。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from casetrace import api
from casetrace.answer import cli
from casetrace.answer.cli import STATUS_OK, run_answer_question
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.answer.model import OfflineChatModel
from casetrace.api import create_app
from casetrace.storage import (
    DATABASE_URL_ENV,
    TEST_DATABASE_URL_ENV,
    import_snapshot,
    initialize_schema,
    load_snapshot,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "data/dev/demo-v3.json"
REFERENCE = PROJECT_ROOT / (
    "data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx"
)
V10_DIR = PROJECT_ROOT / "results/m4/dev-v3-answer-v10"
QUERY_IDS = ("q001", "q002", "q003", "q004", "q005")


@pytest.fixture
def api_database(storage_connection, scratch_schema, monkeypatch):
    """在独占 schema 导入 dev-v3，并把服务的连接配置临时指向测试库。

    返回 (schema, 已读回快照)：快照只用于核对读回结果，不注入服务。
    """

    initialize_schema(storage_connection, schema=scratch_schema)
    import_snapshot(
        storage_connection, DATASET, REFERENCE,
        snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema,
    )
    loaded = load_snapshot(storage_connection, schema=scratch_schema)
    monkeypatch.setenv(DATABASE_URL_ENV, os.environ[TEST_DATABASE_URL_ENV])
    return scratch_schema, loaded


def saved_v10(query_id: str) -> dict:
    """M4-04 保存的 v10 运行记录：固定的原文、排名、上下文与实际消息。"""

    return json.loads((V10_DIR / f"{query_id}.json").read_text(encoding="utf-8"))


def query_of(record: dict) -> tuple[str, date]:
    return record["query"]["text"], date.fromisoformat(record["query"]["known_at"])


def jsonable(value):
    """把核心记录里的元组等转成 JSON 形态，便于与 HTTP 响应逐字段比较。"""

    return json.loads(json.dumps(value, ensure_ascii=False))


def post_answer(client, record: dict, *, top_k: int = 4):
    query, known_at = query_of(record)
    return client.post("/answer", json={
        "query": query, "known_at": known_at.isoformat(), "top_k": top_k,
    })


def no_model():
    raise AssertionError("这条路线不应构造模型")


def test_search_without_key_matches_saved_rankings_and_context(api_database, monkeypatch):
    schema, _ = api_database
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(api, "build_model", lambda name: no_model())
    client = TestClient(create_app(db_schema=schema))
    assert client.get("/model-status").json() == {"configured": False}

    for query_id in ("q001", "q002", "q003", "q005"):
        saved = saved_v10(query_id)
        response = client.post("/search", json={
            "query": saved["query"]["text"], "known_at": "2026-09-15",
        })
        assert response.status_code == 200, query_id
        body = response.json()
        assert body["status"] == "retrieved", query_id
        record = body["record"]
        assert record["ranking"] == saved["ranking"], query_id
        assert record["context"] == saved["context"], query_id
        assert record["query"]["text"] == saved["query"]["text"], query_id
        assert record["corpus"]["data_source"] == "postgres"
        assert record["response"]["called_model"] is False
        assert record["answer"] is None

    empty = client.post("/search", json={"query": "zzzxqv987654", "known_at": "2026-09-15"})
    assert empty.json()["status"] == "no_hits"
    assert empty.json()["record"]["context"]["cases"] == []


# ── 1. 五 Query：HTTP 与文件路线等价，并与 v10 对照 ──────────────────────

def test_api_matches_file_route_and_v10(api_database, monkeypatch):
    schema, loaded = api_database
    loads = []
    real_loader = api.load_database_snapshot

    def counting_loader(name):
        loads.append(name)
        return real_loader(name)

    monkeypatch.setattr(api, "load_database_snapshot", counting_loader)

    for query_id in QUERY_IDS:
        saved = saved_v10(query_id)
        query, known_at = query_of(saved)

        api_model = OfflineChatModel(saved["answer_text"])
        client = TestClient(create_app(db_schema=schema, model_factory=lambda: api_model))
        response = post_answer(client, saved)

        assert response.status_code == 200, query_id
        body = response.json()
        assert body["status"] == STATUS_OK, query_id
        api_record = body["record"]

        file_model = OfflineChatModel(saved["answer_text"])
        file_record = run_answer_question(
            query, known_at, dataset_path=DATASET, reference_path=REFERENCE,
            model_factory=lambda: file_model, query_id=saved["query"]["query_id"],
        ).record

        # 排名、完整上下文与实际模型消息：HTTP／文件／v10 三者一致。
        assert (jsonable(api_record["ranking"]) == jsonable(file_record["ranking"])
                == saved["ranking"]), query_id
        assert (jsonable(api_record["context"]) == jsonable(file_record["context"])
                == saved["context"]), query_id
        assert (jsonable(api_record["request"]["sent_messages"])
                == jsonable(file_record["request"]["sent_messages"])
                == saved["request"]["sent_messages"]), query_id
        assert api_model.calls[0]["messages"] == file_model.calls[0]["messages"], query_id
        # 状态、结构化回答、响应原文、引用报告、模型用量一致。
        assert api_record["status"] == file_record["status"], query_id
        assert (jsonable(api_record["answer"]) == jsonable(file_record["answer"])
                == saved["answer"]), query_id
        assert api_record["answer_text"] == file_record["answer_text"], query_id
        assert (jsonable(api_record["citations"]) == jsonable(file_record["citations"])
                == saved["citations"]), query_id
        assert api_record["response"]["usage"] == file_record["response"]["usage"], query_id
        # query_id 由客户端决定：HTTP 不提供，所以记录里是 null。
        assert api_record["query"]["query_id"] is None, query_id
        assert file_record["query"]["query_id"] == saved["query"]["query_id"], query_id
        # 来源与摘要按各自路线；摘要复用读回值，不另行重算。
        assert api_record["corpus"]["data_source"] == "postgres", query_id
        assert api_record["corpus"]["schema"] == schema, query_id
        assert (api_record["corpus"]["content_digest"]
                == loaded.snapshot.content_digest), query_id
        assert file_record["corpus"]["data_source"] == "file", query_id
        assert api_record["snapshot"] == file_record["snapshot"], query_id
        # 源码身份同进程同代码；耗时不作跨路线比较。
        assert api_record["implementation"] == file_record["implementation"], query_id
        assert "postgresql://" not in response.text, query_id

    # 每个请求恰好一次快照加载，且都用服务端配置的 schema。
    assert loads == [schema] * len(QUERY_IDS)


# ── 2. 读取边界与独立性：一次读回、连接先关、不碰文件 ────────────────────

def test_api_reads_once_and_closes_connection_before_model(api_database, monkeypatch):
    schema, loaded = api_database
    loads = []
    real_loader = api.load_database_snapshot

    def counting_loader(name):
        loads.append(name)
        return real_loader(name)

    monkeypatch.setattr(api, "load_database_snapshot", counting_loader)

    observed = {}
    real_connect = cli.connect

    @contextmanager
    def spy_connect(*args, **kwargs):
        with real_connect(*args, **kwargs) as connection:
            observed["connection"] = connection
            yield connection
        # 真实短连接在 with 退出时关闭；这里记录它确实已经关上。
        observed["closed_after_loader"] = connection.closed

    monkeypatch.setattr(cli, "connect", spy_connect)

    saved = saved_v10("q005")

    def factory():
        observed["loader_done_before_model"] = observed.get("closed_after_loader") is True
        return OfflineChatModel(saved["answer_text"])

    client = TestClient(create_app(db_schema=schema, model_factory=factory))
    response = post_answer(client, saved)

    assert response.status_code == 200
    assert response.json()["status"] == STATUS_OK
    assert loads == [schema]
    assert observed["closed_after_loader"] is True
    assert observed["loader_done_before_model"] is True
    # 摘要来自真实读回的值，而不是旧注入对象。
    assert (response.json()["record"]["corpus"]["content_digest"]
            == loaded.snapshot.content_digest)


def test_api_answers_from_database_without_file_loaders(api_database, monkeypatch):
    """请求时文件/Excel 加载入口不可用，仍能从数据库完成回答。"""

    schema, _ = api_database
    from casetrace.answer import context as context_module

    def explode(*args, **kwargs):
        raise AssertionError("数据库路线不应读取文件/Excel，也不应重跑 verify")

    monkeypatch.setattr(context_module, "load_validated_dataset", explode)
    monkeypatch.setattr(context_module, "check_source_records", explode)
    monkeypatch.setattr("casetrace.storage.snapshot.verify_snapshot", explode)

    saved = saved_v10("q005")
    model = OfflineChatModel(saved["answer_text"])
    client = TestClient(create_app(db_schema=schema, model_factory=lambda: model))
    response = post_answer(client, saved)

    assert response.status_code == 200
    assert response.json()["status"] == STATUS_OK
    assert response.json()["record"]["query"]["text"] == saved["query"]["text"]


def test_invalid_request_never_touches_database_and_bad_url_is_safe(monkeypatch):
    """无效请求不读库；非法 DB URL 只给 503 安全说明，不回显连接串。"""

    fragment = "sk-FICTIONAL-SECRET-DB"
    monkeypatch.setenv(DATABASE_URL_ENV, f"postgresql://user:{fragment}@127.0.0.1:1/none")
    client = TestClient(create_app(db_schema="casetrace_absent", model_factory=no_model))

    invalid = client.post("/answer", json={"query": "   ", "known_at": "2026-09-15"})
    assert invalid.status_code == 422

    response = client.post("/answer", json={"query": "焊线脱落", "known_at": "2026-09-15"})
    body = response.json()
    assert response.status_code == 503
    assert body["status"] == "service_unavailable"
    assert body["record"] is None
    assert fragment not in response.text
    assert "postgresql://" not in response.text
