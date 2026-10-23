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
                filename='example.csv', known_at='2026-10-20')
    if params == {}:
        base.pop('basis')
    response = TestClient(create_app()).post('/dataset', params={**base, **params}, content=b'')
    assert response.status_code == 422


def test_upload_limits_body_before_database_access():
    response = TestClient(create_app()).post('/dataset', params={
        'dataset_name': '测试', 'basis': '人工声明整份资料完整可用。',
        'filename': 'large.csv', 'known_at': '2026-10-20',
    }, content=b'x' * (5 * 1024 * 1024 + 1))
    assert response.status_code == 413
