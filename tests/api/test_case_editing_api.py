"""整案维护的公开 HTTP 行为；内存快照接缝不代替真实事务验证。"""

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

from casetrace import api


def edit_body(loaded, case_id='C1'):
    case = next(item for item in loaded.records['cases'] if item.case_id == case_id)
    basic = asdict(case)
    basic.pop('case_id')
    body = {'expected_snapshot_id': loaded.snapshot.snapshot_id, 'case': basic,
            'closure_status': loaded.payload['sources'][case_id]['closure_status']}
    for kind in ('details', 'evidences'):
        body[kind] = [asdict(item) for item in loaded.records[kind] if item.case_id == case_id]
        for item in body[kind]:
            item.pop('case_id')
            for key, value in list(item.items()):
                if hasattr(value, 'isoformat'):
                    item[key] = value.isoformat()
    return body


@pytest.fixture
def editing_client(monkeypatch, loaded_snapshot):
    state = {'loaded': loaded_snapshot}
    monkeypatch.setattr(api, 'connect', lambda: nullcontext(None))
    monkeypatch.setattr(api, 'load_database_snapshot', lambda schema: state['loaded'])

    def save(connection, *, case_id, submission, schema):
        from casetrace.data.case_editing import prepare_case_edit
        from casetrace.storage.snapshot import SnapshotConflictError
        current = state['loaded']
        if submission.expected_snapshot_id != current.snapshot.snapshot_id:
            raise SnapshotConflictError('已变化')
        candidate = prepare_case_edit(current, case_id, submission)
        if candidate is None:
            return current, False
        records, payload, known_at, basis = candidate
        metadata = replace(current.snapshot, snapshot_id='edited-' + str(len(payload['case_revisions'][case_id])),
                           known_at=known_at, basis=basis, split=payload['split'],
                           imported_at=datetime.now(timezone.utc))
        state['loaded'] = replace(current, records=records, payload=payload, snapshot=metadata)
        return state['loaded'], True

    monkeypatch.setattr(api, 'save_case_snapshot', save, raising=False)
    return TestClient(api.create_app()), state


def test_edit_existing_synthetic_case_preserves_identity_and_records(editing_client):
    client, state = editing_client
    before = deepcopy(state['loaded'])
    body = edit_body(before)
    body['case']['root_cause'] = '人工复核后的新结论，不受合成候选限制。'
    response = client.put('/case', params={'case_id': 'C1'}, json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'saved'
    assert data['dataset']['source_type'] == 'user_maintained'
    assert data['dataset']['snapshot_id'] != before.snapshot.snapshot_id
    detail = client.get('/case', params={'case_id': 'C1', 'snapshot_id': data['dataset']['snapshot_id']}).json()
    assert detail['case']['root_cause'] == body['case']['root_cause']
    assert detail['details'][0]['detail_id'] == 'D1'
    assert detail['evidences'][0]['checkpoint_id'] == 'E1'
    assert detail['source']['root_cause'] == before.payload['sources']['C1']['root_cause']
    revision = detail['revisions'][0]
    assert revision['changed_fields'] == ['case.root_cause']
    assert '人工复核' not in str(revision)
    assert state['loaded'].records['cases'][1] == before.records['cases'][1]
    assert before.payload.get('case_revisions') is None


def test_unchanged_and_stale_saves_do_not_create_revisions(editing_client):
    client, state = editing_client
    before = deepcopy(state['loaded'])
    body = edit_body(before)
    response = client.put('/case?case_id=C1', json=body)
    assert response.status_code == 200
    assert response.json()['status'] == 'unchanged'
    assert state['loaded'] == before
    body['case']['root_cause'] = '新结案结论'
    assert client.put('/case?case_id=C1', json=body).status_code == 200
    assert client.put('/case?case_id=C1', json=body).status_code == 409
    assert len(state['loaded'].payload['case_revisions']['C1']) == 1


def test_new_children_require_origins_and_keep_leading_zero_lots(editing_client):
    client, state = editing_client
    body = edit_body(state['loaded'])
    new_detail = {**body['details'][0], 'production_lot': '0000123', 'customer_lot': '0000456'}
    new_detail.pop('detail_id')
    body['details'].append(new_detail)
    before = deepcopy(state['loaded'])
    invalid = client.put('/case?case_id=C1', json=body)
    assert invalid.status_code == 422
    assert invalid.json()['errors'][0]['path'] == 'details[1].origin'
    assert state['loaded'] == before
    new_detail['origin'] = {'kind': 'synthetic', 'description': '人工合成新增批次事件', 'source_location': '合成记录：第二批次'}
    for i in range(2):
        item = {**body['evidences'][0], 'result': f'人工合成补充检查 {i}',
                'origin': {'kind': 'report', 'source_document_id': 'R-001',
                           'source_document_name': '合成测试报告', 'source_location': f'调查项 {i+2}'}}
        item.pop('checkpoint_id')
        body['evidences'].append(item)
    response = client.put('/case?case_id=C1', json=body)
    assert response.status_code == 200, response.text
    detail = response.json()['detail']
    assert [len(detail[key]) for key in ('details', 'evidences')] == [2, 3]
    assert detail['details'][1]['production_lot'] == '0000123'
    assert len({item['checkpoint_id'] for item in detail['evidences']}) == 3
    assert len(detail['source']['web_provenance']['origins']) == 3
    assert all('filename' not in item and 'csv_line' not in item
               for item in detail['source']['web_provenance']['origins'])
    assert len(detail['revisions'][0]['added_records']) == 3


@pytest.mark.parametrize('change,field', [
    (lambda b: b['details'][0].update(detail_id='D2'), 'detail_id'),
    (lambda b: b['details'][0].update(product_id='missing'), 'product_id'),
    (lambda b: b.update(evidences=[]), 'evidences'),
    (lambda b: b['evidences'][0].update(relevance='uncertain'), 'evidences'),
    (lambda b: b['details'][0].update(customer_lot='contradicts-other-case'), 'customer_lot'),
    (lambda b: b['details'][0].update(detection_time='2026-10-01'), 'detection_time'),
])
def test_invalid_candidate_keeps_current_dataset(editing_client, change, field):
    client, state = editing_client
    before = deepcopy(state['loaded'])
    body = edit_body(before)
    change(body)
    response = client.put('/case?case_id=C1', json=body)
    assert response.status_code == 422, response.text
    assert field in response.text
    assert state['loaded'] == before


@pytest.mark.parametrize('change', [
    lambda b: b.update(expected_snapshot_id=' '),
    lambda b: b['case'].update(case_id='changed'),
    lambda b: b['case'].update(editor='someone'),
    lambda b: b['details'][0].update(affected_qty=True),
    lambda b: b['details'][0].update(affected_qty=2147483648),
    lambda b: b['details'][0].update(production_time='2026-06-01T00:00:00'),
    lambda b: b['details'][0].update(origin={'kind': 'report', 'source_document_id': 'R1', 'source_document_name': '同上', 'source_location': 'p1'}),
    lambda b: b.update(availability={'known_at': '2026-10-01', 'basis': '整份资料声明', 'confirmed_complete': False}),
    lambda b: b.update(case_revisions=[]),
])
def test_request_boundary_rejects_invalid_shape(editing_client, change):
    client, state = editing_client
    before = deepcopy(state['loaded'])
    body = edit_body(before)
    change(body)
    assert client.put('/case?case_id=C1', json=body).status_code == 422
    assert state['loaded'] == before


def test_availability_change_checks_all_cases_and_records_summary(editing_client):
    client, state = editing_client
    body = edit_body(state['loaded'])
    body['availability'] = {'known_at': '2026-06-01', 'basis': '人工合成整库完整可用声明', 'confirmed_complete': True}
    response = client.put('/case?case_id=C1', json=body)
    assert response.status_code == 422 and 'D2' in response.text
    body['availability']['known_at'] = '2026-10-01'
    data = client.put('/case?case_id=C1', json=body).json()
    assert data['dataset']['known_at'] == '2026-10-01'
    assert data['detail']['revisions'][0]['changed_fields'] == ['availability.known_at', 'availability.basis']


def test_group_process_conflict_is_not_silently_fixed(editing_client):
    from casetrace.data.dataset_model import CaseGroup, Membership
    client, state = editing_client
    records = state['loaded'].records
    records['groups'].append(CaseGroup('G1', ['same_abnormal_process'], '共同工序组', None))
    records['memberships'].extend([Membership('G1', 'C1', '共同站点'), Membership('G1', 'C2', '共同站点')])
    reference = state['loaded'].reference
    reference.processes['P007'] = 'Molding'
    reference.route_processes['LF_WB'].add('P007')
    before = deepcopy(state['loaded'])
    body = edit_body(before)
    body['case']['abnormal_processes'] = ['P007']
    response = client.put('/case?case_id=C1', json=body)
    assert response.status_code == 422
    assert 'CR-51' in response.text and 'G1' in response.text
    assert state['loaded'] == before


def test_csv_origin_and_child_removal(editing_client):
    client, state = editing_client
    state['loaded'].payload['sources']['C1'] = {'closure_status': 'confirmed', 'records': [
        {'record_type': 'case', 'record_id': 'C1', 'source_document_id': 'R1',
         'source_document_name': '合成报告', 'source_location': '结论', 'filename': 'old.csv', 'csv_line': 2},
    ]}
    body = edit_body(state['loaded'])
    body['case']['root_cause'] = '人工修订的结论'
    data = client.put('/case?case_id=C1', json=body).json()['detail']
    assert data['source']['records'][0]['csv_line'] == 2
    assert data['source']['web_provenance']['modified_records'] == [{'record_type': 'case', 'record_id': 'C1'}]
    body = edit_body(state['loaded'])
    new = {**body['evidences'][0], 'origin': {'kind': 'synthetic', 'description': '人工合成检查', 'source_location': '补充调查项'}}
    new.pop('checkpoint_id')
    body['evidences'].append(new)
    created = client.put('/case?case_id=C1', json=body).json()['detail']['evidences'][-1]['checkpoint_id']
    body = edit_body(state['loaded'])
    body['evidences'] = [item for item in body['evidences'] if item['checkpoint_id'] != created]
    detail = client.put('/case?case_id=C1', json=body).json()['detail']
    assert detail['source']['web_provenance']['origins'] == []
    assert detail['revisions'][-1]['removed_records'] == [{'record_type': 'evidence', 'record_id': created}]


def test_new_search_uses_saved_case_and_excludes_revision_history(editing_client):
    client, state = editing_client
    old_record = client.post('/search', json={'query': '焊线脱落', 'known_at': '2026-09-15'}).json()['record']
    body = edit_body(state['loaded'])
    body['case']['root_cause'] = '人工修订后的原因'
    saved = client.put('/case?case_id=C1', json=body).json()
    record = client.post('/search', json={'query': '人工修订后的原因', 'known_at': '2026-09-15'}).json()['record']
    case = next(item for item in record['context']['cases'] if item['case_id'] == 'C1')
    assert case['case']['root_cause'] == '人工修订后的原因'
    assert case['source']['web_provenance']['modified_records']
    assert 'saved_at' not in str(record['context']) and 'changed_fields' not in str(record['context'])
    assert old_record['snapshot']['snapshot_id'] != saved['dataset']['snapshot_id']
    assert old_record['context']['cases'][0]['case']['root_cause'] == '表面污染'


def test_identical_explicit_csv_origin_is_unchanged(editing_client):
    client, state = editing_client
    report = {'source_document_id': 'R1', 'source_document_name': '合成测试报告', 'source_location': '第1页结论'}
    state['loaded'].payload['sources']['C1'] = {'closure_status': 'confirmed', 'records': [
        {'record_type': 'case', 'record_id': 'C1', **report, 'filename': 'old.csv', 'csv_line': 2}]}
    before = deepcopy(state['loaded'])
    body = edit_body(before)
    body['case']['origin'] = {'kind': 'report', **report}
    response = client.put('/case?case_id=C1', json=body)
    assert response.status_code == 200
    assert response.json()['status'] == 'unchanged'
    assert state['loaded'] == before


def test_answer_in_progress_and_download_record_keep_original_snapshot(editing_client):
    from concurrent.futures import ThreadPoolExecutor
    import json
    import threading
    from casetrace.answer.model import OfflineChatModel
    client, state = editing_client
    entered, release = threading.Event(), threading.Event()
    sent = []
    class WaitingReplay(OfflineChatModel):
        def complete(self, messages, **kwargs):
            sent.extend(message.content for message in messages)
            entered.set()
            assert release.wait(timeout=10)
            return super().complete(messages, **kwargs)
    client.app.state.model_factory = lambda: WaitingReplay(json.dumps({
        'case_answers': [], 'skipped_candidates': [], 'current_gaps': ['只验证身份边界。'],
        'insufficiency': '离线回放需要更多信息。',
    }))
    old = state['loaded'].snapshot.snapshot_id
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, '/answer', json={'query': '焊线脱落', 'known_at': '2026-09-15'})
        try:
            assert entered.wait(timeout=10)
            body = edit_body(state['loaded'])
            body['case']['root_cause'] = '新请求才能看到的结论'
            assert client.put('/case?case_id=C1', json=body).status_code == 200
        finally:
            release.set()
        response = pending.result(timeout=10)
    assert response.status_code == 200, response.text
    record = response.json()['record']  # 同一记录供现有网页下载，不读最新数据库重建。
    assert record['snapshot']['snapshot_id'] == old
    assert record['context']['cases'][0]['case']['root_cause'] == '表面污染'
    assert '新请求才能看到' not in '\n'.join(sent)
    sent.clear()
    entered.clear()
    client.post('/answer', json={'query': '新请求才能看到的结论', 'known_at': '2026-09-15'})
    assert '新请求才能看到的结论' in '\n'.join(sent)
    assert 'saved_at' not in '\n'.join(sent) and 'changed_fields' not in '\n'.join(sent)


@pytest.mark.parametrize('case_id', ['report/001', '.', '..', '报告?#001'])
def test_save_uses_arbitrary_legal_query_ids(editing_client, case_id):
    client, state = editing_client
    loaded = state['loaded']
    loaded.records['cases'][0].case_id = case_id
    for kind in ('details', 'evidences'):
        loaded.records[kind][0].case_id = case_id
    loaded.payload['sources'][case_id] = loaded.payload['sources'].pop('C1')
    body = edit_body(loaded, case_id)
    body['case']['root_cause'] = '特殊编号也能维护'
    response = client.put('/case', params={'case_id': case_id}, json=body)
    assert response.status_code == 200
    assert response.json()['detail']['case']['case_id'] == case_id


def test_unordered_choices_and_reordered_existing_children_are_no_change(editing_client):
    client, state = editing_client
    loaded = state['loaded']
    loaded.reference.processes['P007'] = 'Molding'
    loaded.reference.route_processes['LF_WB'].add('P007')
    loaded.records['cases'][0].abnormal_processes.append('P007')
    loaded.records['details'].append(replace(loaded.records['details'][0], detail_id='D3', production_lot='PL3', customer_lot='CL3'))
    body = edit_body(loaded)
    body['case']['abnormal_processes'].reverse()
    body['details'].reverse()
    before = deepcopy(loaded)
    response = client.put('/case?case_id=C1', json=body)
    assert response.status_code == 200
    assert response.json()['status'] == 'unchanged'
    assert state['loaded'] == before


def test_request_limit_query_and_errors_do_not_leak_inputs(editing_client):
    client, state = editing_client
    body = edit_body(state['loaded'])
    assert client.put('/case?case_id=C1&schema=other', json=body).status_code == 422
    response = client.put('/case?case_id=C1', content=b'x' * (5*1024*1024+1))
    assert response.status_code == 413
    response = client.put('/case?case_id=C1', content=b'{"password":"secret-value"}')
    assert response.status_code == 422 and 'secret-value' not in response.text


def test_nul_text_is_a_field_error_before_database(editing_client):
    client, state = editing_client
    body = edit_body(state['loaded'])
    body['evidences'][0]['result'] = '不能写入 PostgreSQL 的\x00文字'
    before = deepcopy(state['loaded'])
    response = client.put('/case?case_id=C1', json=body)
    assert response.status_code == 422
    assert 'evidences' in response.text and 'result' in response.text
    assert state['loaded'] == before
