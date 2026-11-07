"""完整演示恢复：公开 HTTP 与隔离 PostgreSQL，不调用付费模型。"""
from contextlib import contextmanager
from pathlib import Path
import json
import threading

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest

from casetrace import api
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.answer.model import OfflineChatModel
from casetrace.storage.connection import resolve_database_url
from casetrace.storage.snapshot import load_snapshot, prepare_snapshot

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / 'data/dev/demo-v3.json'
REFERENCE = ROOT / 'data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx'


@pytest.fixture
def client(storage_connection, scratch_schema, monkeypatch):
    prepare_snapshot(storage_connection, DATASET, REFERENCE,
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)

    @contextmanager
    def test_connect(**kwargs):
        with psycopg.connect(resolve_database_url(test=True)) as connection:
            yield connection

    monkeypatch.setattr(api, 'connect', test_connect)
    monkeypatch.setattr('casetrace.answer.cli.connect', test_connect)
    return TestClient(api.create_app(db_schema=scratch_schema))


def restore(client, expected=None):
    if expected is None:
        expected = client.get('/dataset').json()['snapshot_id']
    return client.post('/dataset/restore-demo', json={'expected_snapshot_id': expected})


def test_upload_restore_full_baseline_and_restart(client, storage_connection, scratch_schema, tmp_path):
    before = load_snapshot(storage_connection, schema=scratch_schema)
    settings = {'top_k': 2, 'max_tokens': 2048}
    assert client.put('/runtime-settings', json=settings).status_code == 200
    upload = client.post('/dataset', params={
        'expected_snapshot_id': client.get('/dataset').json()['snapshot_id'],
        'dataset_name': '上传测试', 'known_at': '2026-10-20',
        'basis': '人工合成，整份资料在登记日期前结案且完整可用。', 'filename': 'example.csv',
    }, content=client.get('/dataset/example').content)
    assert upload.status_code == 200
    stale = before.snapshot.snapshot_id
    assert restore(client, stale).status_code == 409
    response = restore(client)
    assert response.status_code == 200, response.text
    after = load_snapshot(storage_connection, schema=scratch_schema)
    assert after.records == before.records
    assert after.reference == before.reference
    for key in ('sources', 'queries', 'split', 'review_status'):
        assert after.payload[key] == before.payload[key]
    assert len(after.payload['queries']) == 5
    assert [len(after.records[key]) for key in ('cases', 'details', 'evidences', 'groups', 'memberships')] == [9, 9, 9, 1, 3]
    assert 'upload_filename' not in after.payload
    assert after.payload['baseline_snapshot_id'] == stale
    assert after.snapshot.known_at == before.snapshot.known_at
    assert after.snapshot.basis == before.snapshot.basis
    assert after.snapshot.dataset_sha256 == before.snapshot.dataset_sha256
    assert after.snapshot.snapshot_id != stale
    result = client.post('/search', json={'query': '焊线脱落', 'known_at': '2026-09-15'})
    assert result.status_code == 200, result.text
    assert result.json()['record']['snapshot']['snapshot_id'] == after.snapshot.snapshot_id
    prepare_snapshot(storage_connection, tmp_path / 'missing', tmp_path / 'missing.xlsx',
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    restarted = TestClient(api.create_app(db_schema=scratch_schema))
    assert restarted.get('/dataset').json() == response.json()['dataset']
    assert restarted.get('/runtime-settings').json() == settings
    second = restore(client).json()['dataset']['snapshot_id']
    assert second != after.snapshot.snapshot_id
    assert restore(client, after.snapshot.snapshot_id).status_code == 409


def test_restore_write_failure_rolls_back(client, storage_connection, scratch_schema):
    before = load_snapshot(storage_connection, schema=scratch_schema)
    storage_connection.execute(sql.SQL('''
        CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'test rejection' USING ERRCODE = '23514'; END; $$
    ''').format(sql.Identifier(scratch_schema, 'reject_restore')))
    storage_connection.execute(sql.SQL('''
        CREATE TRIGGER reject_restore BEFORE INSERT ON {}
        FOR EACH ROW EXECUTE FUNCTION {}()
    ''').format(sql.Identifier(scratch_schema, 'evidence_checkpoints'),
                sql.Identifier(scratch_schema, 'reject_restore')))
    response = restore(client)
    assert response.status_code == 503
    assert 'test rejection' not in response.text
    assert load_snapshot(storage_connection, schema=scratch_schema) == before


def test_concurrent_restores_only_one_wins(client):
    expected = client.get('/dataset').json()['snapshot_id']
    barrier = threading.Barrier(2)
    responses = []

    def submit():
        barrier.wait(timeout=10)
        responses.append(restore(client, expected).status_code)

    threads = [threading.Thread(target=submit) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    assert sorted(responses) == [200, 409]


def test_restore_keeps_in_progress_answer(client):
    entered, release = threading.Event(), threading.Event()

    class WaitingReplay(OfflineChatModel):
        def complete(self, messages, **kwargs):
            entered.set()
            assert release.wait(timeout=15)
            return super().complete(messages, **kwargs)

    client.app.state.model_factory = lambda: WaitingReplay(json.dumps({
        'case_answers': [], 'skipped_candidates': [], 'current_gaps': ['回放需要更多信息。'],
        'insufficiency': '仅验证快照边界，不作质量结论。',
    }, ensure_ascii=False))
    responses = []
    thread = threading.Thread(target=lambda: responses.append(client.post('/answer', json={
        'query': '焊线脱落', 'known_at': '2026-09-15',
    })), daemon=True)
    thread.start()
    try:
        assert entered.wait(timeout=10)
        assert restore(client).status_code == 200
    finally:
        release.set()
        thread.join(timeout=15)
    assert not thread.is_alive()
    assert responses[0].status_code == 200
    assert responses[0].json()['record']['snapshot']['snapshot_id'] == DEV_V3_SNAPSHOT.snapshot_id
    assert client.get('/dataset').json()['snapshot_id'] != DEV_V3_SNAPSHOT.snapshot_id


def revise_test_snapshot(connection, schema, *, empty=False, payload_marker=False, incompatible=False):
    """构造带合法摘要的隔离场景；不把摘要损坏误当业务不兼容。"""
    from psycopg.types.json import Jsonb
    from casetrace.storage import snapshot as store
    from casetrace.storage.content import build_content, content_digest, ENTITY_TABLES, REFERENCE_TABLES
    from casetrace.storage.reference_source import TABLE_KEYS
    if empty:
        for table in reversed(ENTITY_TABLES):
            connection.execute(sql.SQL('DELETE FROM {}').format(sql.Identifier(schema, table)))
    if incompatible:
        connection.execute(sql.SQL('UPDATE {} SET possible_root_causes = %s').format(
            sql.Identifier(schema, 'failure_modes')), ('测试中移除所有基线来源候选',))
    meta = store._read_metadata(connection, schema)
    if empty:
        meta['payload']['sources'] = {}
        for key in store.ORDERED_TABLE_KEYS:
            meta['ordering'][key] = {} if key in ('case_abnormal_processes', 'detail_failure_modes') else []
    if payload_marker:
        meta['payload']['web_revisions'] = {'C001': ['test marker']}
    content = build_content(
        snapshot_fields={key: meta[key] for key in (*store.IDENTITY_FIELDS, 'split', 'review_status')},
        reference={table: store._read_rows(connection, schema, table, TABLE_KEYS[table]) for table in REFERENCE_TABLES},
        entities=store._restore_entities({table: store._read_rows(connection, schema, table) for table in ENTITY_TABLES}, meta['ordering']),
        payload=meta['payload'], ordering=meta['ordering'],
    )
    connection.execute(sql.SQL('UPDATE {} SET payload = %s, ordering = %s, content_digest = %s').format(
        sql.Identifier(schema, 'snapshot_metadata')),
        (Jsonb(meta['payload']), Jsonb(meta['ordering']), content_digest(content)))


def test_zero_case_snapshot_is_preserved_then_restorable(client, storage_connection, scratch_schema):
    revise_test_snapshot(storage_connection, scratch_schema, empty=True)
    before = client.get('/dataset').json()
    assert before['case_count'] == 0
    prepare_snapshot(storage_connection, DATASET, REFERENCE,
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    assert client.get('/dataset').json() == before
    assert restore(client).status_code == 200
    assert client.get('/dataset').json()['case_count'] == 9


def test_incompatible_reference_rejected_without_changes(client, storage_connection, scratch_schema):
    revise_test_snapshot(storage_connection, scratch_schema, incompatible=True, payload_marker=True)
    before = load_snapshot(storage_connection, schema=scratch_schema)
    response = restore(client)
    assert response.status_code == 422, response.text
    assert '候选' in response.json()['message']
    assert load_snapshot(storage_connection, schema=scratch_schema) == before


def test_restore_clears_old_revision_payload(client, storage_connection, scratch_schema):
    revise_test_snapshot(storage_connection, scratch_schema, payload_marker=True)
    assert restore(client).status_code == 200
    assert 'web_revisions' not in load_snapshot(storage_connection, schema=scratch_schema).payload


def test_readback_failure_rolls_back(client, storage_connection, scratch_schema, monkeypatch):
    from casetrace.storage import snapshot as store
    before = load_snapshot(storage_connection, schema=scratch_schema)
    original = store._load_snapshot

    def fail_new_snapshot(connection, schema, meta):
        if meta['snapshot_id'] != before.snapshot.snapshot_id:
            raise ValueError('test readback failure')
        return original(connection, schema, meta)

    with monkeypatch.context() as patch:
        patch.setattr(store, '_load_snapshot', fail_new_snapshot)
        assert restore(client).status_code == 503
    assert load_snapshot(storage_connection, schema=scratch_schema) == before


def test_baseline_hash_failure_does_not_write(client, storage_connection, scratch_schema, monkeypatch):
    monkeypatch.setattr('casetrace.storage.snapshot.file_sha256', lambda path: 'wrong')
    before = load_snapshot(storage_connection, schema=scratch_schema)
    assert restore(client).status_code == 503
    assert load_snapshot(storage_connection, schema=scratch_schema) == before


def test_compatible_reference_is_preserved(client, storage_connection, scratch_schema):
    storage_connection.execute(sql.SQL('UPDATE {} SET notes = %s').format(
        sql.Identifier(scratch_schema, 'failure_modes')), ('测试主数据背景备注',))
    revise_test_snapshot(storage_connection, scratch_schema)
    before = load_snapshot(storage_connection, schema=scratch_schema)
    assert restore(client).status_code == 200
    after = load_snapshot(storage_connection, schema=scratch_schema)
    assert after.reference == before.reference
    assert after.snapshot.reference_sha256 == before.snapshot.reference_sha256


def test_restored_data_survives_api_process_restart(client, scratch_schema):
    import os
    import socket
    import subprocess
    import sys
    import time
    import httpx

    assert restore(client).status_code == 200
    expected = client.get('/dataset').json()
    env = {**os.environ, 'CASETRACE_DATABASE_URL': resolve_database_url(test=True),
           'DEEPSEEK_API_KEY': '', 'CASETRACE_CONTAINER': '0'}
    code = ('import sys, uvicorn; from casetrace.api import create_app; '
            'uvicorn.run(create_app(db_schema=sys.argv[1]), host="127.0.0.1", '
            'port=int(sys.argv[2]), log_level="error", access_log=False)')
    for _ in range(2):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen([sys.executable, '-c', code, scratch_schema, str(port)],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False) as restarted:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        if restarted.get('/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() > deadline or process.poll() is not None:
                        pytest.fail('独立 API 进程未能启动')
                    time.sleep(0.05)
                assert restarted.get('/dataset').json() == expected
                response = restarted.post('/search', json={'query': '焊线脱落', 'known_at': '2026-09-15'})
                assert response.status_code == 200
                assert response.json()['record']['snapshot']['snapshot_id'] == expected['snapshot_id']
        finally:
            process.terminate()
            process.wait(timeout=10)
