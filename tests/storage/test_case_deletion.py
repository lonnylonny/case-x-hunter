"""删除案例与空库的真实 PostgreSQL 验证；仅操作测试自建的隔离 schema。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import threading

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
import pytest

from casetrace import api
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.storage.connection import resolve_database_url
from casetrace.storage.snapshot import load_snapshot, prepare_snapshot

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(storage_connection, scratch_schema, monkeypatch):
    prepare_snapshot(storage_connection, ROOT / 'data/dev/demo-v3.json',
        ROOT / 'data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx',
        snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)

    @contextmanager
    def test_connect(**kwargs):
        with psycopg.connect(resolve_database_url(test=True)) as connection:
            yield connection

    monkeypatch.setattr(api, 'connect', test_connect)
    monkeypatch.setattr('casetrace.answer.cli.connect', test_connect)
    return TestClient(api.create_app(db_schema=scratch_schema))


def identity(client):
    return client.get('/dataset').json()['snapshot_id']


def delete_case(client, case_id):
    return client.request('DELETE', '/case', params={'case_id': case_id},
                          json={'expected_snapshot_id': identity(client), 'confirmed': True})


def test_delete_readback_restart_and_group_rule(client, storage_connection, scratch_schema, tmp_path):
    before = load_snapshot(storage_connection, schema=scratch_schema)
    response = delete_case(client, 'C001')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'deleted'
    after = load_snapshot(storage_connection, schema=scratch_schema)
    assert after.snapshot.snapshot_id.startswith('delete-')
    assert after.snapshot.content_digest != before.snapshot.content_digest
    assert after.snapshot.dataset_sha256 == before.snapshot.dataset_sha256
    assert len(after.records['cases']) == 8
    assert 'C001' not in {item.case_id for item in after.records['cases']}
    assert 'C001' not in {item.case_id for item in after.records['details']}
    assert 'C001' not in {item.case_id for item in after.records['evidences']}
    assert ('G001', 'C001') not in {(item.group_id, item.case_id) for item in after.records['memberships']}
    assert {item.group_id for item in after.records['groups']} == {item.group_id for item in before.records['groups']}
    assert 'C001' not in after.payload['sources']
    assert after.reference == before.reference

    blocked = delete_case(client, 'C003')
    assert blocked.status_code == 422, blocked.text
    assert 'CR-38' in blocked.text and 'G001' in blocked.text
    assert load_snapshot(storage_connection, schema=scratch_schema) == after
    assert delete_case(client, 'C002').status_code == 200

    new = TestClient(api.create_app(db_schema=scratch_schema))
    assert new.get('/dataset').json()['case_count'] == 7
    prepare_snapshot(storage_connection, tmp_path / 'missing.json', tmp_path / 'missing.xlsx',
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    final = load_snapshot(storage_connection, schema=scratch_schema)
    assert final.records['cases'] and final.snapshot.snapshot_id.startswith('delete-')
    assert client.request('DELETE', '/case', params={'case_id': 'C004'},
                          json={'expected_snapshot_id': after.snapshot.snapshot_id,
                                'confirmed': True}).status_code == 409


def test_delete_last_case_from_csv_upload_leaves_zero_cases(client, storage_connection, scratch_schema, tmp_path):
    upload = client.post('/dataset', params={
        'dataset_name': '删除验收用单案 CSV', 'known_at': '2026-09-15', 'basis': '人工合成整库声明',
        'filename': 'example.csv', 'expected_snapshot_id': identity(client),
    }, content=client.get('/dataset/example').content)
    assert upload.status_code == 200, upload.text
    csv_case = upload.json()['dataset']
    assert csv_case['case_count'] == 1
    a = TestClient(api.create_app(db_schema=scratch_schema))
    response = a.request('DELETE', '/case', params={'case_id': 'UPLOAD_C001'},
                         json={'expected_snapshot_id': csv_case['snapshot_id'], 'confirmed': True})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['dataset']['case_count'] == 0
    empty = load_snapshot(storage_connection, schema=scratch_schema)
    assert empty.counts['cases'] == empty.counts['case_details'] == 0
    assert empty.counts['evidence_checkpoints'] == 0
    assert empty.payload.get('sources') == {}

    for path in ('/search', '/answer'):
        result = a.post(path, json={'query': '焊线脱落', 'known_at': '2026-09-15'})
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['status'] == 'empty_dataset' and body['record'] is None
        assert '资料库' in body['message']
    assert a.get('/dataset/reference').json()['products']
    assert a.get('/cases').json()['total_count'] == 0

    prepare_snapshot(storage_connection, tmp_path / 'missing.json', tmp_path / 'missing.xlsx',
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    preserved = load_snapshot(storage_connection, schema=scratch_schema)
    assert preserved.counts['cases'] == 0
    assert preserved.snapshot.snapshot_id == data['dataset']['snapshot_id']


@pytest.mark.parametrize('failure_at', ['case_details', 'snapshot_metadata', 'readback'])
def test_failure_rolls_back_everything(client, storage_connection, scratch_schema, monkeypatch, failure_at):
    from casetrace.storage import snapshot as storage
    before = load_snapshot(storage_connection, schema=scratch_schema)
    if failure_at == 'readback':
        original = storage.load_snapshot

        def fail_readback(*args, **kwargs):
            loaded = original(*args, **kwargs)
            if loaded.snapshot.snapshot_id.startswith('delete-'):
                raise ValueError('test readback failure')
            return loaded
        monkeypatch.setattr(storage, 'load_snapshot', fail_readback)
    else:
        storage_connection.execute(sql.SQL('''
            CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test rejection' USING ERRCODE = '23514'; END; $$
        ''').format(sql.Identifier(scratch_schema, 'reject_delete')))
        storage_connection.execute(sql.SQL('''
            CREATE TRIGGER reject_delete BEFORE INSERT ON {}
            FOR EACH ROW EXECUTE FUNCTION {}()
        ''').format(sql.Identifier(scratch_schema, failure_at), sql.Identifier(scratch_schema, 'reject_delete')))
    assert delete_case(client, 'C002').status_code == 503
    assert load_snapshot(storage_connection, schema=scratch_schema) == before


def test_concurrent_delete_and_edit_only_one_old_identity_wins(client):
    snapshot = identity(client)
    data = client.get('/case', params={'case_id': 'C001', 'snapshot_id': snapshot}).json()
    edit = {'expected_snapshot_id': snapshot,
            'case': {key: data['case'][key] for key in (
                'abnormal_description', 'root_cause', 'corrective_action',
                'investigation_others', 'abnormal_processes')},
            'closure_status': data['source']['closure_status']}
    for kind, fields in (
        ('details', ('detail_id', 'product_id', 'customer_lot', 'production_lot', 'production_time',
                     'detection_stage', 'detection_time', 'abnormal_types', 'affected_qty', 'disposition')),
        ('evidences', ('checkpoint_id', 'checkpoint_type', 'custom_name', 'result', 'relevance')),
    ):
        edit[kind] = [{key: item[key] for key in fields} for item in data[kind]]
    edit['case']['root_cause'] = '并发编辑的结论'
    barrier = threading.Barrier(2)

    def write_delete():
        barrier.wait(timeout=10)
        return client.request('DELETE', '/case', params={'case_id': 'C002'},
                              json={'expected_snapshot_id': snapshot, 'confirmed': True}).status_code

    def write_edit():
        barrier.wait(timeout=10)
        return client.put('/case', params={'case_id': 'C001'}, json=edit).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(write_delete), executor.submit(write_edit)
        codes = sorted([first.result(timeout=20), second.result(timeout=20)])
    assert codes == [200, 409]
