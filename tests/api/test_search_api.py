"""02 搜索：HTTP 边界验证无密钥检索，数据库读回使用既有合成夹具。"""

from dataclasses import replace
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from casetrace import api
from casetrace.answer.model import OfflineChatModel


def forbidden_model():
    raise AssertionError("检索不应构造模型")


@pytest.fixture
def search_client(monkeypatch, loaded_snapshot):
    def forbidden_connection(*args, **kwargs):
        raise AssertionError("离线接口测试不应建立数据库连接")

    monkeypatch.setattr(psycopg, "connect", forbidden_connection)
    monkeypatch.setattr(api, "load_database_snapshot", lambda schema: loaded_snapshot)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    return TestClient(api.create_app(model_factory=forbidden_model))


def test_search_without_key_returns_original_query_and_evidence(search_client):
    query = "  焊线脱落，已排除运输碰伤\n"
    response = search_client.post("/search", json={
        "query": query, "known_at": "2026-09-15", "top_k": 1,
    })

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "retrieved"
    record = body["record"]
    assert record["query"]["text"] == query
    assert record["context"]["query"] == query
    assert [item["case_id"] for item in record["ranking"]] == ["C1"]
    assert record["context"]["cases"][0]["evidences"][0]["result"] == "观察到表面污染"
    assert record["response"]["called_model"] is False
    assert record["answer"] is None


def test_search_no_hits_has_no_answer_or_model_call(search_client):
    response = search_client.post("/search", json={
        "query": "zzzxqv987654", "known_at": "2026-09-15",
    })

    assert response.status_code == 200
    assert response.json()["status"] == "no_hits"
    record = response.json()["record"]
    assert record["ranking"] == record["context"]["cases"] == []
    assert record["response"]["called_model"] is False
    assert record["answer"] is None


@pytest.mark.parametrize("extra", [
    {"query": " "}, {"known_at": "2026/09/16"}, {"top_k": True},
    {"top_k": 0}, {"top_k": 1.5}, {"api_key": "not-accepted"},
])
def test_invalid_search_does_not_load_database(monkeypatch, extra):
    def forbidden_load(schema):
        pytest.fail("无效检索不应读数据库")

    monkeypatch.setattr(api, "load_database_snapshot", forbidden_load)
    client = TestClient(api.create_app(model_factory=forbidden_model))
    response = client.post("/search", json={
        "query": "焊线脱落", "known_at": "2026-09-15", **extra,
    })
    assert response.status_code == 422


@pytest.mark.parametrize("error,expected", [
    (ValueError("secret-db-url"), 503), (RuntimeError("secret-internal"), 500),
])
def test_search_failures_are_explicit_and_safe(search_client, monkeypatch, error, expected):
    def failed_load(schema):
        raise error

    monkeypatch.setattr(api, "load_database_snapshot", failed_load)
    response = search_client.post("/search", json={
        "query": "焊线脱落", "known_at": "2026-09-15",
    })
    assert response.status_code == expected
    assert response.json()["status"] == (
        "service_unavailable" if expected == 503 else "internal_error"
    )
    assert response.json()["record"] is None
    assert "secret" not in response.text


def test_search_rejects_wrong_snapshot(search_client, loaded_snapshot, monkeypatch):
    wrong = replace(loaded_snapshot, snapshot=replace(
        loaded_snapshot.snapshot, snapshot_id="unsupported-snapshot",
    ))
    monkeypatch.setattr(api, "load_database_snapshot", lambda schema: wrong)
    response = search_client.post("/search", json={
        "query": "焊线脱落", "known_at": "2026-09-15",
    })
    assert response.status_code == 503
    assert response.json()["status"] == "service_unavailable"


@pytest.mark.parametrize("key,configured", [("", False), ("  ", False), ("fake-secret", True)])
def test_model_status_only_reports_configuration(monkeypatch, tmp_path, key, configured):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)
    monkeypatch.setattr(api, "build_model", lambda model: forbidden_model())
    monkeypatch.setattr(psycopg, "connect", lambda *args, **kwargs: forbidden_model())
    response = TestClient(api.create_app()).get("/model-status")
    assert response.status_code == 200
    assert response.json() == {"configured": configured}
    assert "fake-secret" not in response.text


def test_model_status_reads_local_env_without_exposing_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=fake-local-secret\n")
    # monkeypatch 恢复环境，避免本次 load_local_env 影响其它用例。
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    response = TestClient(api.create_app()).get("/model-status")
    assert response.json() == {"configured": True}
    assert "fake-local-secret" not in response.text


def test_supported_format_can_report_insufficient_evidence_with_candidates(search_client):
    gap = "未提供具体异常现象和位置，无法判断历史异常是否有调查参考价值。"
    model = OfflineChatModel(json.dumps({
        "case_answers": [], "skipped_candidates": [],
        "current_gaps": [gap], "insufficiency": "候选不足以支持当前异常的调查参考。",
    }, ensure_ascii=False))
    client = TestClient(api.create_app(model_factory=lambda: model))
    response = client.post("/answer", json={
        "query": "P1 仅收到异常通知，未提供具体异常现象、位置或检查结果。",
        "known_at": "2026-09-15",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["record"]["ranking"]
    assert body["record"]["answer"]["case_answers"] == []
    assert body["record"]["answer"]["current_gaps"] == [gap]
    assert len(model.calls) == 1
