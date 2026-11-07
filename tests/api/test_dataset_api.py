"""03：模板下载是公开 HTTP 行为，不依赖模型或数据库。"""

import csv
import io

from fastapi.testclient import TestClient

from casetrace.api import create_app
from casetrace import api
import pytest


def test_downloads_describe_complex_case_without_database():
    client = TestClient(create_app())
    template = client.get('/dataset/template')
    assert template.status_code == 200
    assert 'attachment' in template.headers['content-disposition']
    columns = next(csv.reader(io.StringIO(template.content.decode('utf-8-sig'))))
    assert {'record_type', 'case_id', 'detail_id', 'checkpoint_id',
            'source_document_id', 'source_document_name', 'source_location'} <= set(columns)
    example = client.get('/dataset/example')
    assert example.status_code == 200
    rows = list(csv.DictReader(io.StringIO(example.content.decode('utf-8-sig'))))
    assert [row['record_type'] for row in rows] == [
        'case', 'detail', 'detail', 'evidence', 'evidence', 'evidence',
    ]
    assert {row['case_id'] for row in rows} == {'UPLOAD_C001'}
    assert all(row['source_location'] for row in rows)


@pytest.mark.parametrize('route', ['/search', '/answer'])
def test_query_date_must_match_current_dataset(monkeypatch, loaded_snapshot, route):
    monkeypatch.setattr(api, 'load_database_snapshot', lambda schema: loaded_snapshot)
    response = TestClient(create_app()).post(route, json={
        'query': '焊线脱落', 'known_at': '2026-10-20',
    })
    assert response.status_code == 422
    assert '2026-09-15' in response.json()['detail'][0]['msg']


@pytest.mark.parametrize('params', [
    {}, {'dataset_name': ' '}, {'basis': '同上'}, {'filename': '../x.csv'},
    {'known_at': '2026/10/20'}, {'known_at': '2026-10-20T00:00:00'}, {'review_status': 'confirmed'},
])
def test_upload_rejects_invalid_metadata_without_database(params):
    base = dict(dataset_name='测试', basis='人工声明整份资料在指定日期前完整可用。',
                filename='example.csv', known_at='2026-10-20', expected_snapshot_id='current')
    if params == {}:
        base.pop('basis')
    response = TestClient(create_app()).post('/dataset', params={**base, **params}, content=b'')
    assert response.status_code == 422


def test_upload_limits_body_before_database_access():
    response = TestClient(create_app()).post('/dataset', params={
        'dataset_name': '测试', 'basis': '人工声明整份资料完整可用。',
        'filename': 'large.csv', 'known_at': '2026-10-20', 'expected_snapshot_id': 'old',
    }, content=b'x' * (5 * 1024 * 1024 + 1))
    assert response.status_code == 413


@pytest.mark.parametrize('body', [{}, {'expected_snapshot_id': ' '},
                                  {'expected_snapshot_id': 'old', 'path': '/tmp/x'}])
def test_restore_requires_explicit_snapshot_without_database(body):
    response = TestClient(create_app()).post('/dataset/restore-demo', json=body)
    assert response.status_code == 422


@pytest.mark.parametrize('exception,status', [
    (api.SnapshotConflictError('old'), 409),
    (api.DemoValidationError('C001 的来源候选不在主数据中'), 422),
    (RuntimeError('internal secret'), 503),
])
def test_restore_maps_failures_without_exposing_service_details(monkeypatch, exception, status):
    from contextlib import nullcontext
    monkeypatch.setattr(api, 'connect', lambda: nullcontext(None))

    def fail(*args, **kwargs):
        raise exception

    monkeypatch.setattr(api, 'restore_demo_snapshot', fail)
    response = TestClient(create_app()).post('/dataset/restore-demo', json={
        'expected_snapshot_id': 'old',
    })
    assert response.status_code == status
    if status == 422:
        assert 'C001' in response.json()['message']
    assert 'internal secret' not in response.text


def test_csv_requires_current_snapshot_and_maps_conflict(monkeypatch):
    from contextlib import nullcontext
    client = TestClient(create_app())
    params = {'dataset_name': '版本检查', 'basis': '合成测试整库声明',
              'filename': 'test.csv', 'known_at': '2026-09-15'}
    assert client.post('/dataset', params=params, content=b'').status_code == 422
    monkeypatch.setattr(api, 'connect', lambda: nullcontext(None))

    def conflict(*args, **kwargs):
        assert kwargs['expected_snapshot_id'] == 'old'
        raise api.SnapshotConflictError('stale')

    monkeypatch.setattr(api, 'replace_csv_snapshot', conflict)
    response = client.post('/dataset', params={**params, 'expected_snapshot_id': 'old'}, content=b'')
    assert response.status_code == 409
    assert response.json()['status'] == 'snapshot_conflict'
