"""删除案例与空库的公开 HTTP 行为；内存快照接缝不代替真实事务验证。"""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from casetrace import api


@pytest.fixture
def deletion_client(monkeypatch, loaded_snapshot):
    state = {'loaded': loaded_snapshot}
    monkeypatch.setattr(api, 'connect', lambda: nullcontext(None))
    monkeypatch.setattr(api, 'load_database_snapshot', lambda schema: state['loaded'])

    def delete(connection, *, case_id, expected_snapshot_id, schema):
        from casetrace.data.case_editing import prepare_case_deletion
        from casetrace.storage.snapshot import SnapshotConflictError
        current = state['loaded']
        if expected_snapshot_id != current.snapshot.snapshot_id:
            raise SnapshotConflictError('已变化')
        records, payload = prepare_case_deletion(current, case_id)
        metadata = replace(current.snapshot, snapshot_id='deleted-' + case_id,
                           imported_at=datetime.now(timezone.utc))
        state['loaded'] = replace(current, records=records, payload=payload, snapshot=metadata)
        return state['loaded']

    monkeypatch.setattr(api, 'delete_case_snapshot', delete, raising=False)
    return TestClient(api.create_app()), state


def delete_body(state):
    return {'expected_snapshot_id': state['loaded'].snapshot.snapshot_id, 'confirmed': True}


def delete_request(client, case_id, body):
    # httpx 的便捷 delete() 不接受请求体；DELETE 带 JSON 与浏览器 fetch 行为一致。
    return client.request('DELETE', '/case', params={'case_id': case_id}, json=body)


def test_delete_removes_case_children_sources_and_revisions(deletion_client):
    client, state = deletion_client
    state['loaded'].payload['case_revisions'] = {
        'C1': [{'saved_at': '2026-11-06T12:00:00+00:00', 'operation': 'edit',
                'changed_fields': ['case.root_cause'], 'added_records': [], 'removed_records': []}],
    }
    state['loaded'].payload['sources']['C1']['web_provenance'] = {
        'modified_records': [{'record_type': 'case', 'record_id': 'C1'}], 'origins': []}
    other_case = deepcopy(state['loaded'].records['cases'][1])
    other_details = deepcopy(state['loaded'].records['details'][1:])
    response = delete_request(client, 'C1', delete_body(state))
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'deleted' and data['case_id'] == 'C1'
    assert data['dataset']['case_count'] == 1
    assert data['dataset']['detail_count'] == 1
    assert data['dataset']['evidence_count'] == 1
    assert data['dataset']['source_type'] == 'user_maintained'
    assert state['loaded'].payload['sources'] == {'C2': state['loaded'].payload['sources']['C2']}
    assert 'case_revisions' not in state['loaded'].payload
    assert [case.case_id for case in state['loaded'].records['cases']] == ['C2']
    assert state['loaded'].records['cases'][0] == other_case
    assert state['loaded'].records['details'] == other_details
    assert state['loaded'].records['memberships'] == []
    listed = client.get('/cases', params={'snapshot_id': data['dataset']['snapshot_id']}).json()
    assert listed['total_count'] == 1
    missing = client.get('/case', params={'case_id': 'C1',
                                          'snapshot_id': data['dataset']['snapshot_id']})
    assert missing.status_code == 404
    stale = client.get('/case', params={'case_id': 'C1', 'snapshot_id': 'old-identity'})
    assert stale.status_code == 409


@pytest.mark.parametrize('change', [
    lambda body: body.pop('confirmed'),
    lambda body: body.update(confirmed=False),
    lambda body: body.update(confirmed='true'),
    lambda body: body.update(extra='x'),
    lambda body: body.update(expected_snapshot_id=' '),
])
def test_delete_request_boundary_rejects_unconfirmed(deletion_client, change):
    client, state = deletion_client
    before = deepcopy(state['loaded'])
    body = delete_body(state)
    change(body)
    assert delete_request(client, 'C1', body).status_code == 422
    assert state['loaded'] == before


def test_delete_requires_current_identity_and_known_case(deletion_client):
    client, state = deletion_client
    before = deepcopy(state['loaded'])
    stale = {'expected_snapshot_id': 'old-identity', 'confirmed': True}
    assert delete_request(client, 'C1', stale).status_code == 409
    assert delete_request(client, 'missing', delete_body(state)).status_code == 404
    assert delete_request(client, 'missing', stale).status_code == 409
    assert state['loaded'] == before


def test_group_member_deletion_is_blocked_by_cr38(deletion_client):
    from casetrace.data.dataset_model import CaseGroup, Membership
    client, state = deletion_client
    records = state['loaded'].records
    records['groups'].append(CaseGroup('G1', ['same_abnormal_process'], '共同工序组', None))
    records['memberships'].extend([Membership('G1', 'C1', '共同站点'), Membership('G1', 'C2', '共同站点')])
    before = deepcopy(state['loaded'])
    response = delete_request(client, 'C1', delete_body(state))
    assert response.status_code == 422, response.text
    assert 'CR-38' in response.text and 'G1' in response.text
    assert state['loaded'] == before


def test_delete_last_cases_leaves_empty_dataset_business_status(deletion_client):
    client, state = deletion_client
    assert delete_request(client, 'C1', delete_body(state)).status_code == 200
    response = delete_request(client, 'C2', delete_body(state))
    assert response.status_code == 200, response.text
    assert response.json()['dataset']['case_count'] == 0
    listed = client.get('/cases').json()
    assert listed['total_count'] == 0 and listed['items'] == []
    for path in ('/search', '/answer'):
        empty = client.post(path, json={'query': '焊线脱落', 'known_at': '2026-09-15'})
        assert empty.status_code == 200, empty.text
        data = empty.json()
        assert data['status'] == 'empty_dataset' and data['record'] is None
        assert '资料库' in data['message']
    reference = client.get('/dataset/reference').json()
    assert reference['products']


def test_delete_keeps_no_hits_for_nonempty_dataset(deletion_client):
    client, state = deletion_client
    record = client.post('/search', json={'query': '完全无关的词汇集',
                                          'known_at': '2026-09-15'}).json()
    assert record['status'] == 'no_hits' and record['record'] is not None


def test_delete_works_for_special_legal_ids(deletion_client):
    client, state = deletion_client
    loaded = state['loaded']
    for kind in ('cases', 'details', 'evidences'):
        for item in loaded.records[kind]:
            if item.case_id == 'C1':
                item.case_id = 'report/001'
    loaded.payload['sources']['report/001'] = loaded.payload['sources'].pop('C1')
    response = delete_request(client, 'report/001', delete_body(state))
    assert response.status_code == 200, response.text
    assert response.json()['case_id'] == 'report/001'
