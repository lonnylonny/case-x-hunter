"""新增完整案例的公开 HTTP 行为；内存快照接缝不代替真实事务验证。"""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from casetrace import api


def creation_body(loaded):
    return {
        'expected_snapshot_id': loaded.snapshot.snapshot_id,
        'case': {
            'abnormal_description': '新增案例：焊线脱落，表面污染',
            'root_cause': '人工复核的污染来源',
            'corrective_action': '增加清洁确认',
            'investigation_others': None,
            'abnormal_processes': ['P004'],
            'origin': {'kind': 'synthetic', 'description': '人工合成新增案例',
                       'source_location': '新案例结案结论段'},
        },
        'details': [
            {'product_id': 'P1', 'customer_lot': 'CL9', 'production_lot': '0009001',
             'production_time': '2026-06-01', 'detection_stage': 'OQC',
             'detection_time': '2026-06-10', 'abnormal_types': ['00001'],
             'affected_qty': 5, 'disposition': '隔离',
             'origin': {'kind': 'report', 'source_document_id': 'R-101',
                        'source_document_name': '新增批次报告',
                        'source_location': '第 2 页批次表第 1 行'}},
            {'product_id': 'P1', 'customer_lot': 'CL9', 'production_lot': '0009002',
             'production_time': '2026-06-02', 'detection_stage': 'OQC',
             'detection_time': '2026-06-11', 'abnormal_types': ['00001'],
             'affected_qty': 6, 'disposition': '报废',
             'origin': {'kind': 'report', 'source_document_id': 'R-101',
                        'source_document_name': '新增批次报告',
                        'source_location': '第 3 页批次表第 2 行'}},
        ],
        'evidences': [
            {'checkpoint_type': 'QC', 'custom_name': None, 'result': '断口观察到污染',
             'relevance': 'related',
             'origin': {'kind': 'report', 'source_document_id': 'R-101',
                        'source_document_name': '新增批次报告',
                        'source_location': '第 4 页调查项 1'}},
            {'checkpoint_type': 'QC', 'custom_name': None, 'result': '清洁流程复核',
             'relevance': 'not_related',
             'origin': {'kind': 'synthetic', 'description': '人工合成复核记录',
                        'source_location': '补充调查项 2'}},
            {'checkpoint_type': 'Other', 'custom_name': '产线访谈', 'result': '操作员访谈记录',
             'relevance': 'uncertain',
             'origin': {'kind': 'synthetic', 'description': '人工合成访谈记录',
                        'source_location': '补充调查项 3'}},
        ],
        'closure_status': 'confirmed',
    }


@pytest.fixture
def creation_client(monkeypatch, loaded_snapshot):
    state = {'loaded': loaded_snapshot}
    monkeypatch.setattr(api, 'connect', lambda: nullcontext(None))
    monkeypatch.setattr(api, 'load_database_snapshot', lambda schema: state['loaded'])

    def create(connection, *, submission, schema):
        from casetrace.data.case_editing import prepare_case_creation
        from casetrace.storage.snapshot import SnapshotConflictError
        current = state['loaded']
        if submission.expected_snapshot_id != current.snapshot.snapshot_id:
            raise SnapshotConflictError('已变化')
        records, payload, known_at, basis, case_id = prepare_case_creation(current, submission)
        metadata = replace(current.snapshot, snapshot_id='created-' + case_id,
                           known_at=known_at, basis=basis, imported_at=datetime.now(timezone.utc))
        state['loaded'] = replace(current, records=records, payload=payload, snapshot=metadata)
        return state['loaded'], case_id

    monkeypatch.setattr(api, 'create_case_snapshot', create, raising=False)
    return TestClient(api.create_app()), state


def test_create_full_case_and_immediate_browse_search(creation_client):
    client, state = creation_client
    before = deepcopy(state['loaded'])
    body = creation_body(before)
    response = client.post('/cases', json=body)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data['status'] == 'created'
    case_id = data['case_id']
    assert case_id not in {case.case_id for case in before.records['cases']}
    assert data['dataset']['case_count'] == 3
    assert data['dataset']['detail_count'] == 4
    assert data['dataset']['evidence_count'] == 5
    assert data['dataset']['source_type'] == 'user_maintained'
    detail = data['detail']
    assert detail['snapshot_id'] == data['dataset']['snapshot_id']
    assert [len(detail[key]) for key in ('details', 'evidences')] == [2, 3]
    assert [item['production_lot'] for item in detail['details']] == ['0009001', '0009002']
    assert len({item['detail_id'] for item in detail['details']}) == 2
    assert len({item['checkpoint_id'] for item in detail['evidences']}) == 3
    assert all(len(item['detail_id']) > 1 and item['detail_id'][0] == 'D' for item in detail['details'])
    assert detail['groups'] == []
    revision = detail['revisions'][0]
    assert revision['operation'] == 'create'
    assert len(revision['added_records']) == 6
    assert revision['changed_fields'] == [] and revision['removed_records'] == []
    assert detail['source']['closure_status'] == 'confirmed'
    assert len(detail['source']['web_provenance']['origins']) == 6
    assert all('filename' not in item and 'csv_line' not in item
               for item in detail['source']['web_provenance']['origins'])

    listed = client.get('/cases', params={'snapshot_id': data['dataset']['snapshot_id']}).json()
    assert listed['total_count'] == 3
    assert any(item['case_id'] == case_id for item in listed['items'])

    record = client.post('/search', json={'query': '人工复核的污染来源',
                                          'known_at': '2026-09-15'}).json()['record']
    assert any(case['case_id'] == case_id for case in record['context']['cases'])

    assert state['loaded'].records['groups'] == before.records['groups']
    assert state['loaded'].records['memberships'] == before.records['memberships']
    assert state['loaded'].records['cases'][:2] == before.records['cases']


def test_creation_requires_origins_and_rejects_client_ids(creation_client):
    client, state = creation_client
    before = deepcopy(state['loaded'])

    missing_case_origin = creation_body(before)
    missing_case_origin['case'].pop('origin')
    response = client.post('/cases', json=missing_case_origin)
    assert response.status_code == 422
    assert 'case.origin' in response.text
    assert state['loaded'] == before

    client_id = creation_body(before)
    client_id['details'][0]['detail_id'] = 'D1'
    response = client.post('/cases', json=client_id)
    assert response.status_code == 422
    assert 'details[0].detail_id' in response.text and '服务端生成' in response.text
    assert state['loaded'] == before

    missing_child_origin = creation_body(before)
    missing_child_origin['evidences'][0].pop('origin')
    response = client.post('/cases', json=missing_child_origin)
    assert response.status_code == 422
    assert 'evidences[0].origin' in response.text
    assert state['loaded'] == before


@pytest.mark.parametrize('change,expected', [
    (lambda body: body['details'][0].update(detection_time='2026-10-01'), 'detection_time'),
    (lambda body: body['details'][0].update(production_lot='PL1', customer_lot='CL-OTHER'), 'CR-09'),
    (lambda body: body.update(evidences=[]), 'CR-30'),
    (lambda body: body['details'][0].update(product_id='missing'), 'CR-05'),
    (lambda body: body['case'].update(abnormal_processes=['P999']), 'CR-49'),
    (lambda body: body['details'][0].update(affected_qty=0), 'affected_qty'),
])
def test_invalid_candidate_keeps_current_dataset(creation_client, change, expected):
    client, state = creation_client
    before = deepcopy(state['loaded'])
    body = creation_body(before)
    change(body)
    response = client.post('/cases', json=body)
    assert response.status_code == 422, response.text
    assert expected in response.text
    assert state['loaded'] == before
    assert client.get('/cases').json()['total_count'] == 2


@pytest.mark.parametrize('change', [
    lambda body: body.update(expected_snapshot_id=' '),
    lambda body: body['case'].update(case_id='C1'),
    lambda body: body['case'].update(editor='someone'),
    lambda body: body['details'][0].update(affected_qty=True),
    lambda body: body['details'][0].update(production_time='2026-06-01T00:00:00'),
    lambda body: body['details'][0].update(origin={'kind': 'report', 'source_document_id': 'R1',
                                                   'source_document_name': '同上',
                                                   'source_location': 'p1'}),
    lambda body: body.update(availability={'known_at': '2026-10-01', 'basis': '整份资料声明',
                                           'confirmed_complete': False}),
    lambda body: body.update(closure_status='other'),
    lambda body: body.update(case_revisions=[]),
])
def test_request_boundary_rejects_invalid_shape(creation_client, change):
    client, state = creation_client
    before = deepcopy(state['loaded'])
    body = creation_body(before)
    change(body)
    assert client.post('/cases', json=body).status_code == 422
    assert state['loaded'] == before


def test_creation_conflict_and_limits(creation_client):
    client, state = creation_client
    body = creation_body(state['loaded'])
    assert client.post('/cases', json=body).status_code == 201
    assert client.post('/cases', json=body).status_code == 409
    assert client.post('/cases', content=b'x' * (5 * 1024 * 1024 + 1)).status_code == 413
    response = client.post('/cases', content=b'{"password":"secret-value"}')
    assert response.status_code == 422 and 'secret-value' not in response.text


def test_create_first_case_from_zero_case_snapshot(creation_client):
    client, state = creation_client
    current = state['loaded']
    payload = {key: value for key, value in current.payload.items()
               if key not in ('cases', 'details', 'evidences', 'groups', 'memberships')}
    payload['sources'] = {}
    state['loaded'] = replace(current, records={
        'cases': [], 'details': [], 'evidences': [], 'groups': [], 'memberships': [],
    }, payload=payload)
    body = creation_body(state['loaded'])
    response = client.post('/cases', json=body)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data['dataset']['case_count'] == 1
    listed = client.get('/cases', params={'snapshot_id': data['dataset']['snapshot_id']}).json()
    assert listed['total_count'] == 1 and listed['items'][0]['case_id'] == data['case_id']
    record = client.post('/search', json={'query': '人工复核的污染来源',
                                          'known_at': '2026-09-15'}).json()['record']
    assert record['status'] == 'retrieved'
    assert [case['case_id'] for case in record['context']['cases']] == [data['case_id']]


def test_creation_availability_change_checks_whole_dataset(creation_client):
    client, state = creation_client
    body = creation_body(state['loaded'])
    body['availability'] = {'known_at': '2026-06-05', 'basis': '人工合成整库完整可用声明',
                            'confirmed_complete': True}
    response = client.post('/cases', json=body)
    assert response.status_code == 422 and 'details[0]' in response.text
    assert '完整可用日期' in response.text
    body['availability']['known_at'] = '2026-10-01'
    response = client.post('/cases', json=body)
    assert response.status_code == 201, response.text
    assert response.json()['dataset']['known_at'] == '2026-10-01'
