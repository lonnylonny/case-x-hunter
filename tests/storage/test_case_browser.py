"""资料库公开 HTTP 与真实 PostgreSQL；每例只操作自身的隔离 schema。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import psycopg
import pytest

from casetrace import api
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.storage.connection import resolve_database_url
from casetrace.storage.snapshot import prepare_snapshot

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def browser_client(storage_connection, scratch_schema, monkeypatch):
    prepare_snapshot(
        storage_connection, ROOT / 'data/dev/demo-v3.json',
        ROOT / 'data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx',
        snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema,
    )

    @contextmanager
    def test_connect(**kwargs):
        with psycopg.connect(resolve_database_url(test=True)) as connection:
            yield connection

    monkeypatch.setattr(api, 'connect', test_connect)
    monkeypatch.setattr('casetrace.answer.cli.connect', test_connect)
    return TestClient(api.create_app(db_schema=scratch_schema))


def upload_example(client):
    response = client.post('/dataset', params={
        'expected_snapshot_id': client.get('/dataset').json()['snapshot_id'],
        'dataset_name': '浏览集成测试', 'known_at': '2026-09-15', 'filename': 'example.csv',
        'basis': '人工合成测试资料，登记时点前完整可用。',
    }, content=client.get('/dataset/example').content)
    assert response.status_code == 200
    return response.json()['dataset']


def test_complete_library_round_trip(browser_client):
    data = browser_client.get('/cases').json()
    assert data['total_count'] == data['matched_count'] == 9
    identity = data['dataset']['snapshot_id']
    reference = browser_client.get('/dataset/reference', params={'snapshot_id': identity}).json()
    assert reference['snapshot_id'] == identity
    assert all(product['customer_name'] == reference['customers'][product['customer_id']]
               for product in reference['products'])
    assert sum(item['detail_count'] for item in data['items']) == data['dataset']['detail_count'] == 9
    case = browser_client.get('/case', params={'case_id': 'C001', 'snapshot_id': identity}).json()
    assert case['snapshot_id'] == identity
    assert case['source']['sheet'] == 'failure_modes'
    assert case['groups'][0]['member_ids'] == ['C001', 'C003', 'C004']
    assert len(case['details']) == len(case['evidences']) == 1


def test_replacements_reject_old_browse_requests(browser_client):
    old = browser_client.get('/cases').json()['dataset']['snapshot_id']
    uploaded = upload_example(browser_client)
    for route in ('/cases?page=2&page_size=1&', '/case?case_id=C001&', '/dataset/reference?'):
        response = browser_client.get(f'{route}snapshot_id={old}')
        assert response.status_code == 409
    data = browser_client.get('/cases', params={'snapshot_id': uploaded['snapshot_id']}).json()
    assert data['total_count'] == 1
    detail = browser_client.get('/case', params={'case_id': 'UPLOAD_C001', 'snapshot_id': uploaded['snapshot_id']}).json()
    assert len(detail['details']) == 2 and len(detail['evidences']) == 3
    assert len(detail['source']['records']) == 6
    response = browser_client.post('/dataset/restore-demo', json={'expected_snapshot_id': uploaded['snapshot_id']})
    assert response.status_code == 200
    assert browser_client.get('/cases', params={'snapshot_id': uploaded['snapshot_id']}).status_code == 409
    fresh = browser_client.get('/cases').json()
    assert fresh['total_count'] == 9 and fresh['dataset']['snapshot_id'] != old


def test_concurrent_browse_never_combines_snapshots(browser_client):
    started = threading.Event()

    def read_repeatedly():
        result = []
        for _ in range(8):
            response = browser_client.get('/cases')
            assert response.status_code == 200
            result.append(response.json())
            started.set()
        return result

    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(read_repeatedly)
        assert started.wait(timeout=10)
        uploaded = upload_example(browser_client)
        assert browser_client.post('/dataset/restore-demo', json={
            'expected_snapshot_id': uploaded['snapshot_id'],
        }).status_code == 200
        results = reader.result(timeout=20)
    for data in results:
        assert data['total_count'] == data['dataset']['case_count'] == len(data['items'])
        assert data['total_count'] in (1, 9)
        assert sum(item['detail_count'] for item in data['items']) == data['dataset']['detail_count']
        assert sum(item['evidence_count'] for item in data['items']) == data['dataset']['evidence_count']
        assert [item['case_id'] for item in data['items']] == (
            ['UPLOAD_C001'] if data['total_count'] == 1 else [f'C{i:03}' for i in range(1, 10)]
        )
