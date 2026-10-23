"""04：通过 HTTP 和真实 PostgreSQL 验证实例设置，模型不联网。"""

import os
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from casetrace.api import create_app
from casetrace.storage import initialize_schema
from casetrace.storage.connection import DATABASE_URL_ENV, TEST_DATABASE_URL_ENV
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.storage import prepare_snapshot
from pathlib import Path


def test_settings_survive_new_app_and_repeated_initialization(
    storage_connection, scratch_schema, monkeypatch,
):
    initialize_schema(storage_connection, schema=scratch_schema)
    monkeypatch.setenv(DATABASE_URL_ENV, os.environ[TEST_DATABASE_URL_ENV])
    client = TestClient(create_app(db_schema=scratch_schema))
    response = client.get('/runtime-settings')
    assert response.status_code == 200
    assert response.json() == {'top_k': 4, 'max_tokens': 4096}
    saved = client.put('/runtime-settings', json={'top_k': 1, 'max_tokens': 2048})
    assert saved.status_code == 200
    assert saved.json() == {'top_k': 1, 'max_tokens': 2048}
    initialize_schema(storage_connection, schema=scratch_schema)
    restarted = TestClient(create_app(db_schema=scratch_schema))
    assert restarted.get('/runtime-settings').json() == saved.json()


def test_saved_settings_reach_search_and_model_request(
    storage_connection, scratch_schema, monkeypatch,
):
    root = Path(__file__).resolve().parents[2]
    prepare_snapshot(storage_connection, root / 'data/dev/demo-v3.json',
                     root / 'data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx',
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    monkeypatch.setenv(DATABASE_URL_ENV, os.environ[TEST_DATABASE_URL_ENV])
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'fake-secret')
    calls = []

    def complete(**request):
        calls.append(request)
        return SimpleNamespace(model='offline', choices=[SimpleNamespace(
            finish_reason='stop', message=SimpleNamespace(content=json.dumps({
                'case_answers': [], 'skipped_candidates': [],
                'current_gaps': ['缺少当前检查结果'], 'insufficiency': '仅供流程回放',
            }, ensure_ascii=False)),
        )])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete)))
    monkeypatch.setattr('casetrace.answer.model._build_client', lambda *args: fake)
    client = TestClient(create_app(db_schema=scratch_schema))
    assert client.put('/runtime-settings', json={'top_k': 1, 'max_tokens': 2048}).status_code == 200
    query = {'query': '焊线脱落', 'known_at': '2026-09-15'}
    search = client.post('/search', json=query).json()['record']
    assert search['retrieval']['top_k'] == 1
    assert len(search['ranking']) == 1
    assert calls == []
    response = client.post('/answer', json=query)
    assert response.status_code == 200, response.text
    assert response.json()['record']['retrieval']['top_k'] == 1
    assert calls[0]['max_tokens'] == 2048
    assert calls[0]['temperature'] == 0
    assert 'fake-secret' not in response.text
    explicit = client.post('/search', json={**query, 'top_k': 3}).json()['record']
    assert explicit['retrieval']['top_k'] == 3
    assert client.get('/runtime-settings').json()['top_k'] == 1
