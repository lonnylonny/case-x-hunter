"""03：从公开 HTTP 边界验收真实 PostgreSQL 的替换与持久性。"""

from contextlib import contextmanager
from pathlib import Path
import csv
import hashlib
import io
import json
import threading

from fastapi.testclient import TestClient
import psycopg
import pytest
from psycopg import sql

from casetrace import api
from casetrace.storage.connection import resolve_database_url
from casetrace.storage.snapshot import prepare_snapshot
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.answer.model import OfflineChatModel

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / 'data/dev/demo-v3.json'
REFERENCE = ROOT / 'data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx'
PARAMS = dict(dataset_name='复杂案例上传演示', known_at='2026-10-20',
              basis='人工合成演示声明：报告全部案例在2026-10-20之前结案且完整可用。',
              filename='complex.csv')


@pytest.fixture
def upload_client(storage_connection, scratch_schema, monkeypatch):
    prepare_snapshot(storage_connection, DATASET, REFERENCE,
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)

    @contextmanager
    def test_connect(**kwargs):
        with psycopg.connect(resolve_database_url(test=True)) as connection:
            yield connection

    monkeypatch.setattr(api, 'connect', test_connect)
    # load_database_snapshot 的数据库边界同样只指向测试库。
    monkeypatch.setattr('casetrace.answer.cli.connect', test_connect)
    return TestClient(api.create_app(db_schema=scratch_schema))


def upload_example(client, raw=None, **params):
    if raw is None:
        raw = client.get('/dataset/example').content
    return client.post('/dataset', params={**PARAMS, **params}, content=raw,
                       headers={'Content-Type': 'text/csv'})


def search(client, query='焊线脱落', known_at='2026-10-20'):
    return client.post('/search', json=dict(query=query, known_at=known_at))


def test_complex_case_replaces_example_and_is_queryable(upload_client):
    response = upload_example(upload_client)
    assert response.status_code == 200, response.text
    state = upload_client.get('/dataset').json()
    assert state['name'] == '复杂案例上传演示'
    assert state['case_count'] == 1
    assert state['known_at'] == '2026-10-20'
    assert state['updated_at']
    assert state['review_status'] == 'draft_pending_human_review'
    assert state['snapshot_id'].startswith('upload-')
    result = search(upload_client)
    assert result.status_code == 200, result.text
    record = result.json()['record']
    assert [item['case_id'] for item in record['ranking']] == ['UPLOAD_C001']
    case = record['context']['cases'][0]
    assert [item['detail_id'] for item in case['details']] == ['UPLOAD_D001', 'UPLOAD_D002']
    assert [item['checkpoint_id'] for item in case['evidences']] == ['UPLOAD_E001', 'UPLOAD_E002', 'UPLOAD_E003']
    assert len(case['source']['records']) == 6
    assert case['source']['records'][3]['source_location'] == '第4页：拉力试验记录'
    assert case['source']['records'][3]['csv_line'] == 5
    assert case['source']['records'][3]['filename'] == 'complex.csv'
    assert record['snapshot']['snapshot_id'] == state['snapshot_id']
    assert record['snapshot']['basis'] == PARAMS['basis']
    assert record['response']['called_model'] is False


def edit_csv(client, change):
    rows = list(csv.DictReader(io.StringIO(client.get('/dataset/example').content.decode('utf-8-sig'))))
    change(rows)
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode('utf-8')


@pytest.mark.parametrize('column,value,row_index', [
    ('product_id', 'NO_SUCH_PRODUCT', 1),
    ('abnormal_processes', 'NO_SUCH_PROCESS', 0),
    ('abnormal_types', '10', 1),
    ('case_id', 'MISSING_PARENT', 1),
    ('root_cause', ' ', 0),
    ('source_document_id', '', 2),
    ('source_location', '同上', 4),
    ('detection_time', '2026-11-01', 1),
    ('production_time', '2026-06-30', 1),
    ('affected_qty', '1.5', 1),
    ('affected_qty', '2147483648', 1),
    ('detail_id', 'UPLOAD_D001', 2),
    ('custom_name', '', 3),
    ('closure_status', '', 0),
])
def test_invalid_rows_are_locatable_and_preserve_old_dataset(upload_client, column, value, row_index):
    before = upload_client.get('/dataset').json()

    def change(rows):
        rows[row_index][column] = value
        if column == 'custom_name':
            rows[row_index]['checkpoint_type'] = 'Other'

    raw = edit_csv(upload_client, change)
    response = upload_example(upload_client, raw)
    assert response.status_code == 422, response.text
    errors = response.json()['errors']
    assert any(error['row'] == row_index + 2 for error in errors), errors
    assert upload_client.get('/dataset').json() == before
    result = search(upload_client, known_at='2026-09-15')
    assert result.status_code == 200
    assert any(item['case_id'] == 'C001' for item in result.json()['record']['ranking'])


@pytest.mark.parametrize('raw,expected_column', [
    (b'case_id\nC1\n', 'record_type'), (b'\xff\xfe', 'file'), (b'', 'record_type'),
    (b'"unterminated', 'file'),
])
def test_parse_errors_leave_current_data_unchanged(upload_client, raw, expected_column):
    before = upload_client.get('/dataset').json()
    response = upload_example(upload_client, raw)
    assert response.status_code == 422
    assert any(error['column'] == expected_column for error in response.json()['errors'])
    assert upload_client.get('/dataset').json() == before


def test_write_failure_rolls_back_deleted_rows_and_metadata(upload_client, storage_connection, scratch_schema):
    before = upload_client.get('/dataset').json()
    storage_connection.execute(sql.SQL('''
        CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'test rejection' USING ERRCODE = '23514'; END; $$
    ''').format(sql.Identifier(scratch_schema, 'reject_upload')))
    storage_connection.execute(sql.SQL('''
        CREATE TRIGGER reject_upload BEFORE INSERT ON {}
        FOR EACH ROW EXECUTE FUNCTION {}()
    ''').format(sql.Identifier(scratch_schema, 'evidence_checkpoints'),
                sql.Identifier(scratch_schema, 'reject_upload')))

    response = upload_example(upload_client)
    assert response.status_code == 503
    assert 'test rejection' not in response.text
    assert upload_client.get('/dataset').json() == before
    result = search(upload_client, known_at='2026-09-15')
    assert result.status_code == 200
    assert any(item['case_id'] == 'C001' for item in result.json()['record']['ranking'])


def test_restart_preparation_and_new_app_preserve_uploaded_dataset(
    upload_client, storage_connection, scratch_schema, tmp_path,
):
    original_files = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (DATASET, REFERENCE)}
    assert upload_example(upload_client).status_code == 200
    before = upload_client.get('/dataset').json()
    # 与容器重启相同的 prepare；故意传不存在的样例路径，证明不会回填样例。
    prepared = prepare_snapshot(storage_connection, tmp_path / 'missing.json', tmp_path / 'missing.xlsx',
                                snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    restarted = TestClient(api.create_app(db_schema=scratch_schema))
    assert prepared.status == 'preserved'
    assert restarted.get('/dataset').json() == before
    assert search(restarted).json()['record']['ranking'][0]['case_id'] == 'UPLOAD_C001'
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in original_files} == original_files


def test_second_upload_replaces_previous_upload_without_mixing_cases(upload_client):
    assert upload_example(upload_client).status_code == 200
    before = upload_client.get('/dataset').json()
    raw = edit_csv(upload_client, lambda rows: [row.update(case_id='UPLOAD_C002') for row in rows])
    assert upload_example(upload_client, raw, dataset_name='第二份上传').status_code == 200
    after = upload_client.get('/dataset').json()
    assert after['snapshot_id'] != before['snapshot_id']
    assert after['name'] == '第二份上传'
    assert after['case_count'] == 1
    assert [item['case_id'] for item in search(upload_client).json()['record']['ranking']] == ['UPLOAD_C002']


def test_upload_does_not_interrupt_in_progress_answer(upload_client):
    entered, release = threading.Event(), threading.Event()

    class WaitingReplay(OfflineChatModel):
        def complete(self, messages, **kwargs):
            entered.set()
            assert release.wait(timeout=15)
            return super().complete(messages, **kwargs)

    model = WaitingReplay(json.dumps({
        'case_answers': [], 'skipped_candidates': [], 'current_gaps': ['测试回放：需要更多当前信息。'],
        'insufficiency': '测试回放不作质量结论。',
    }, ensure_ascii=False))
    upload_client.app.state.model_factory = lambda: model
    responses = []
    thread = threading.Thread(target=lambda: responses.append(upload_client.post('/answer', json={
        'query': '焊线脱落', 'known_at': '2026-09-15',
    })), daemon=True)
    thread.start()
    try:
        assert entered.wait(timeout=10)
        response = upload_example(upload_client)
        assert response.status_code == 200, response.text
    finally:
        release.set()
        thread.join(timeout=15)
    assert not thread.is_alive()
    assert responses[0].status_code == 200, responses[0].text
    record = responses[0].json()['record']
    assert record['snapshot']['snapshot_id'] == 'dev-v3-2026-09-15'
    assert 'C001' in [item['case_id'] for item in record['ranking']]
    assert upload_client.get('/dataset').json()['snapshot_id'].startswith('upload-')
    assert search(upload_client).json()['record']['ranking'][0]['case_id'] == 'UPLOAD_C001'


def test_multiline_csv_source_uses_physical_start_line(upload_client):
    raw = edit_csv(upload_client, lambda rows: rows[0].update(abnormal_description='焊线脱落\n第二行描述'))
    response = upload_example(upload_client, raw)
    assert response.status_code == 200, response.text
    sources = search(upload_client).json()['record']['context']['cases'][0]['source']['records']
    assert [item['csv_line'] for item in sources] == [2, 4, 5, 6, 7, 8]


def test_uploaded_data_supports_cited_answer_replay(upload_client):
    assert upload_example(upload_client).status_code == 200
    model = OfflineChatModel(json.dumps({
        'case_answers': [{
            'case_id': 'UPLOAD_C001', 'relevance_reason': '当前焊线脱落与历史焊盘界面剥离有调查参考关系。',
            'query_facts': ['当前发现焊线脱落'], 'case_facts': ['历史两批拉力试验发现焊盘界面剥离'],
            'historical_root_cause': '历史结案记录为焊盘表面污染。',
            'historical_corrective_action': None,
            'historical_evidences': [{'checkpoint_id': 'UPLOAD_E001',
                                     'result': '两批拉力试验观察到焊盘界面剥离。'}],
            'sources': [{'case_id': 'UPLOAD_C001', 'field': 'abnormal_description'},
                        {'case_id': 'UPLOAD_C001', 'field': 'root_cause'},
                        {'case_id': 'UPLOAD_C001', 'field': 'checkpoint:UPLOAD_E001'}],
        }], 'skipped_candidates': [], 'current_gaps': ['当前异常的表面检查结果未知。'],
        'insufficiency': None,
    }, ensure_ascii=False))
    upload_client.app.state.model_factory = lambda: model
    response = upload_client.post('/answer', json={'query': '焊线脱落', 'known_at': '2026-10-20'})
    assert response.status_code == 200, response.text
    record = response.json()['record']
    assert record['status'] == 'ok'
    assert record['snapshot']['snapshot_id'].startswith('upload-')
    assert record['citations']['issues'] == []
    assert 'SYN-REPORT-001' in model.sent_text()


def test_confirmed_cause_requires_related_evidence(upload_client):
    def remove_support(rows):
        for row in rows:
            if row['record_type'] == 'evidence':
                row['relevance'] = 'uncertain'

    before = upload_client.get('/dataset').json()
    response = upload_example(upload_client, edit_csv(upload_client, remove_support))
    assert response.status_code == 422
    assert any('CR-35' in item['message'] and item['row'] == 2 for item in response.json()['errors'])
    assert upload_client.get('/dataset').json() == before


def test_ndf_does_not_require_related_label_or_match_candidate_library(upload_client):
    def make_ndf(rows):
        rows[0].update(closure_status='NDF', root_cause='NDF：调查未能确认原因。')
        rows[1]['affected_qty'] = '6000'  # 上传不沿用合成 GR-06 的 5000 颗限制。
        for row in rows:
            if row['record_type'] == 'evidence':
                row['relevance'] = 'uncertain'

    response = upload_example(upload_client, edit_csv(upload_client, make_ndf))
    assert response.status_code == 200, response.text
    case = search(upload_client).json()['record']['context']['cases'][0]
    assert case['source']['closure_status'] == 'NDF'
    assert case['details'][0]['affected_qty'] == 6000


def test_error_location_does_not_assume_identifier_character_set(upload_client):
    def change(rows):
        for row in rows:
            row['case_id'] = 'C]1'
        rows[0]['abnormal_processes'] = 'BAD_PROCESS'

    response = upload_example(upload_client, edit_csv(upload_client, change))
    assert response.status_code == 422
    assert any(item['row'] == 2 and 'CR-49' in item['message'] for item in response.json()['errors'])


def test_api_process_restart_preserves_current_data(upload_client, scratch_schema):
    """真实退出/启动 Uvicorn 进程，不只重建 TestClient 对象。"""
    import os
    import socket
    import subprocess
    import sys
    import time
    import httpx

    assert upload_example(upload_client).status_code == 200
    expected = upload_client.get('/dataset').json()
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
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        if client.get('/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() > deadline or process.poll() is not None:
                        pytest.fail('独立 API 进程未能启动')
                    time.sleep(0.05)
                assert client.get('/dataset').json() == expected
                response = search(client)
                assert response.status_code == 200
                assert response.json()['record']['ranking'][0]['case_id'] == 'UPLOAD_C001'
        finally:
            process.terminate()
            process.wait(timeout=10)
