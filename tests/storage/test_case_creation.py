"""新增完整案例的真实 PostgreSQL 验证；仅操作测试自建的隔离 schema。"""

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


def creation_body(client, source_case='C001'):
    identity = client.get('/dataset').json()['snapshot_id']
    data = client.get('/case', params={'case_id': source_case, 'snapshot_id': identity}).json()
    template = data['details'][0]
    case = {
        'abnormal_description': '新增验收案例：同一异常的新批次',
        'root_cause': '人工复核的新案例根因',
        'corrective_action': '追加清洁确认步骤',
        'investigation_others': None,
        'abnormal_processes': list(data['case']['abnormal_processes']),
        'origin': {'kind': 'synthetic', 'description': '人工合成新增验收案例',
                   'source_location': '新案例结案结论段'},
    }
    details = []
    for number in (1, 2):
        details.append({
            'product_id': template['product_id'],
            'customer_lot': 'CL-NEW-1', 'production_lot': f'0009{number:03d}',
            'production_time': '2026-06-01', 'detection_stage': template['detection_stage'],
            'detection_time': f'2026-06-1{number}',
            'abnormal_types': list(template['abnormal_types']),
            'affected_qty': 100 + number, 'disposition': '隔离',
            'origin': {'kind': 'report', 'source_document_id': 'NEW-R-001',
                       'source_document_name': '新增验收报告',
                       'source_location': f'第 {number} 页批次表'},
        })
    evidences = [{
        'checkpoint_type': 'QC', 'custom_name': None, 'result': f'新增验收调查 {number}',
        'relevance': 'related' if number == 1 else 'uncertain',
        'origin': {'kind': 'synthetic', 'description': '人工合成新增调查',
                   'source_location': f'补充调查项 {number}'},
    } for number in (1, 2, 3)]
    return {'expected_snapshot_id': identity, 'case': case, 'details': details,
            'evidences': evidences, 'closure_status': 'confirmed'}


def test_create_readback_restart_and_search(client, storage_connection, scratch_schema, tmp_path):
    before = load_snapshot(storage_connection, schema=scratch_schema)
    body = creation_body(client)
    response = client.post('/cases', json=body)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data['status'] == 'created' and data['case_id'] not in {c.case_id for c in before.records['cases']}
    after = load_snapshot(storage_connection, schema=scratch_schema)
    assert after.snapshot.snapshot_id.startswith('create-')
    assert after.snapshot.content_digest != before.snapshot.content_digest
    assert after.snapshot.dataset_sha256 == before.snapshot.dataset_sha256
    assert len(after.records['cases']) == len(before.records['cases']) + 1
    assert len(after.records['details']) == len(before.records['details']) + 2
    assert len(after.records['evidences']) == len(before.records['evidences']) + 3
    assert after.records['groups'] == before.records['groups']
    assert after.records['memberships'] == before.records['memberships']
    assert after.records['cases'][:-1] == before.records['cases']
    new_case_id = data['case_id']
    new_details = [item for item in after.records['details'] if item.case_id == new_case_id]
    assert [item.production_lot for item in new_details] == ['0009001', '0009002']
    source = after.payload['sources'][new_case_id]
    assert source['closure_status'] == 'confirmed'
    assert len(source['web_provenance']['origins']) == 6
    revision = after.payload['case_revisions'][new_case_id][0]
    assert revision['operation'] == 'create' and len(revision['added_records']) == 6

    new = TestClient(api.create_app(db_schema=scratch_schema))
    assert new.get('/dataset').json() == data['dataset']
    detail = new.get('/case', params={'case_id': new_case_id,
                                      'snapshot_id': after.snapshot.snapshot_id}).json()
    assert [item['production_lot'] for item in detail['details']] == ['0009001', '0009002']
    record = new.post('/search', json={'query': '人工复核的新案例根因',
                                       'known_at': '2026-09-15'}).json()['record']
    assert new_case_id in [case['case_id'] for case in record['context']['cases']]

    prepare_snapshot(storage_connection, tmp_path / 'missing.json', tmp_path / 'missing.xlsx',
                     snapshot=DEV_V3_SNAPSHOT, schema=scratch_schema)
    assert load_snapshot(storage_connection, schema=scratch_schema) == after
    assert client.post('/cases', json=body).status_code == 409


@pytest.mark.parametrize('failure_at', ['case_details', 'snapshot_metadata', 'readback'])
def test_failure_rolls_back_everything(client, storage_connection, scratch_schema, monkeypatch, failure_at):
    from casetrace.storage import snapshot as storage
    before = load_snapshot(storage_connection, schema=scratch_schema)
    body = creation_body(client)
    if failure_at == 'readback':
        original = storage.load_snapshot

        def fail_readback(*args, **kwargs):
            loaded = original(*args, **kwargs)
            if loaded.snapshot.snapshot_id.startswith('create-'):
                raise ValueError('test readback failure')
            return loaded
        monkeypatch.setattr(storage, 'load_snapshot', fail_readback)
    else:
        storage_connection.execute(sql.SQL('''
            CREATE FUNCTION {}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test rejection' USING ERRCODE = '23514'; END; $$
        ''').format(sql.Identifier(scratch_schema, 'reject_create')))
        storage_connection.execute(sql.SQL('''
            CREATE TRIGGER reject_create BEFORE INSERT ON {}
            FOR EACH ROW EXECUTE FUNCTION {}()
        ''').format(sql.Identifier(scratch_schema, failure_at), sql.Identifier(scratch_schema, 'reject_create')))
    assert client.post('/cases', json=body).status_code == 503
    assert load_snapshot(storage_connection, schema=scratch_schema) == before


def test_concurrent_create_and_edit_only_one_old_identity_wins(client):
    identity = client.get('/dataset').json()['snapshot_id']
    data = client.get('/case', params={'case_id': 'C001', 'snapshot_id': identity}).json()
    edit = {'expected_snapshot_id': identity,
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
    body = creation_body(client)
    barrier = threading.Barrier(2)

    def write_create():
        barrier.wait(timeout=10)
        return client.post('/cases', json=body).status_code

    def write_edit():
        barrier.wait(timeout=10)
        return client.put('/case', params={'case_id': 'C001'}, json=edit).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(write_create), executor.submit(write_edit)
        codes = sorted([first.result(timeout=20), second.result(timeout=20)])
    assert codes[1] == 409 and codes[0] in (200, 201)
