"""整案保存真实 PostgreSQL 验证；仅操作测试自建的隔离 schema。"""

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


def submission(client, case_id='C001'):
    identity = client.get('/dataset').json()['snapshot_id']
    data = client.get('/case', params={'case_id': case_id, 'snapshot_id': identity}).json()
    body = {'expected_snapshot_id': identity,
            'case': {key: data['case'][key] for key in (
                'abnormal_description', 'root_cause', 'corrective_action', 'investigation_others', 'abnormal_processes')},
            'closure_status': data['source']['closure_status']}
    for kind, fields in (
        ('details', ('detail_id', 'product_id', 'customer_lot', 'production_lot', 'production_time',
                     'detection_stage', 'detection_time', 'abnormal_types', 'affected_qty', 'disposition')),
        ('evidences', ('checkpoint_id', 'checkpoint_type', 'custom_name', 'result', 'relevance')),
    ):
        body[kind] = [{key: item[key] for key in fields} for item in data[kind]]
    return body


def save(client, body, case_id='C001'):
    return client.put('/case', params={'case_id': case_id}, json=body)


def test_save_readback_restart_and_replace_revision_history(client, storage_connection, scratch_schema, tmp_path):
    before = load_snapshot(storage_connection, schema=scratch_schema)
    body = submission(client)
    assert save(client, body).json()['status'] == 'unchanged'
    assert load_snapshot(storage_connection, schema=scratch_schema) == before
    body['case']['root_cause'] = '人工维护测试的新结论'
    response = save(client, body)
    assert response.status_code == 200, response.text
    after = load_snapshot(storage_connection, schema=scratch_schema)
    assert after.snapshot.snapshot_id != before.snapshot.snapshot_id
    assert after.snapshot.content_digest != before.snapshot.content_digest
    assert after.snapshot.dataset_sha256 == before.snapshot.dataset_sha256
    assert after.payload['sources']['C001']['web_provenance']['modified_records']
    assert len(after.payload['case_revisions']['C001']) == 1
    assert after.reference == before.reference
    assert after.records['cases'][1:] == before.records['cases'][1:]
    assert after.records['groups'] == before.records['groups']
    assert after.records['memberships'] == before.records['memberships']
    assert save(client, body).status_code == 409
    prepare_snapshot(storage_connection, tmp_path / 'missing.json', tmp_path / 'missing.xlsx',
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    assert load_snapshot(storage_connection, schema=scratch_schema) == after
    new = TestClient(api.create_app(db_schema=scratch_schema))
    assert new.get('/dataset').json() == response.json()['dataset']
    upload = new.post('/dataset', params={
        'dataset_name': '修订后整体替换', 'known_at': '2026-09-15', 'basis': '人工合成整库声明',
        'filename': 'example.csv', 'expected_snapshot_id': after.snapshot.snapshot_id,
    }, content=new.get('/dataset/example').content)
    assert upload.status_code == 200
    assert 'case_revisions' not in load_snapshot(storage_connection, schema=scratch_schema).payload
    uploaded_id = upload.json()['dataset']['snapshot_id']
    csv_body = submission(new, 'UPLOAD_C001')
    csv_body['case']['root_cause'] = 'CSV 案例的人工改写'
    assert save(new, csv_body, 'UPLOAD_C001').status_code == 200
    csv_current = new.get('/dataset').json()['snapshot_id']
    assert new.post('/dataset/restore-demo', json={'expected_snapshot_id': uploaded_id}).status_code == 409
    assert new.post('/dataset/restore-demo', json={'expected_snapshot_id': csv_current}).status_code == 200
    final = load_snapshot(storage_connection, schema=scratch_schema)
    assert final.records == before.records and 'case_revisions' not in final.payload


@pytest.mark.parametrize('failure_at', ['case_details', 'snapshot_metadata', 'readback'])
def test_failure_rolls_back_everything(client, storage_connection, scratch_schema, monkeypatch, failure_at):
    from casetrace.storage import snapshot as storage
    before = load_snapshot(storage_connection, schema=scratch_schema)
    body = submission(client)
    body['case']['root_cause'] = '不能部分入库的修改'
    if failure_at == 'readback':
        original = storage.load_snapshot
        def fail_readback(*args, **kwargs):
            loaded = original(*args, **kwargs)
            if loaded.snapshot.snapshot_id.startswith('edit-'):
                raise ValueError('test readback failure')
            return loaded
        monkeypatch.setattr(storage, 'load_snapshot', fail_readback)
    else:
        storage_connection.execute(sql.SQL('''
            CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test rejection' USING ERRCODE = '23514'; END; $$
        ''').format(sql.Identifier(scratch_schema, 'reject_edit')))
        storage_connection.execute(sql.SQL('''
            CREATE TRIGGER reject_edit BEFORE INSERT ON {}
            FOR EACH ROW EXECUTE FUNCTION {}()
        ''').format(sql.Identifier(scratch_schema, failure_at), sql.Identifier(scratch_schema, 'reject_edit')))
    assert save(client, body).status_code == 503
    assert load_snapshot(storage_connection, schema=scratch_schema) == before


@pytest.mark.parametrize('other', ['edit', 'csv', 'restore'])
def test_concurrent_writers_only_one_old_identity_wins(client, other):
    body = submission(client)
    body['case']['root_cause'] = '并发修改结论'
    barrier = threading.Barrier(2)
    def write_edit():
        barrier.wait(timeout=10)
        return save(client, body).status_code
    def write_other():
        barrier.wait(timeout=10)
        if other == 'edit':
            return save(client, body).status_code
        if other == 'restore':
            return client.post('/dataset/restore-demo', json={'expected_snapshot_id': body['expected_snapshot_id']}).status_code
        return client.post('/dataset', params={
            'expected_snapshot_id': body['expected_snapshot_id'], 'dataset_name': '并发上传',
            'known_at': '2026-09-15', 'basis': '人工合成整库声明', 'filename': 'example.csv',
        }, content=client.get('/dataset/example').content).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(write_edit), executor.submit(write_other)
        assert sorted([first.result(timeout=20), second.result(timeout=20)]) == [200, 409]
