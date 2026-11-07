"""资料库读取的公开 HTTP 契约；数据库与浏览器另有集成验证。"""

import pytest
from dataclasses import replace
from datetime import date
from fastapi.testclient import TestClient

from casetrace import api


@pytest.fixture
def browser_client(monkeypatch, loaded_snapshot):
    monkeypatch.setattr(api, 'load_database_snapshot', lambda schema: loaded_snapshot)

    def forbidden(*args, **kwargs):
        raise AssertionError('资料库浏览不应读取模型设置、检索或构造模型')

    monkeypatch.setattr(api, 'read_runtime_settings', forbidden)
    monkeypatch.setattr(api, 'prepare_answer_run', forbidden)
    return TestClient(api.create_app(model_factory=forbidden))


def test_list_to_complete_case_without_model(browser_client, loaded_snapshot):
    response = browser_client.get('/cases')
    assert response.status_code == 200
    data = response.json()
    identity = data['dataset']['snapshot_id']
    assert data['total_count'] == data['matched_count'] == 2
    assert [item['case_id'] for item in data['items']] == ['C1', 'C2']
    item = data['items'][0]
    assert item['detail_count'] == item['evidence_count'] == 1
    assert item['products'][0]['customer_name'] == '演示客户'
    assert item['detection_start'] == item['detection_end'] == '2026-06-01'
    detail = browser_client.get('/case', params={'case_id': 'C1', 'snapshot_id': identity})
    assert detail.status_code == 200
    case = detail.json()
    assert case['snapshot_id'] == identity
    assert case['case']['root_cause'] == '表面污染'
    assert case['details'][0]['customer_lot'] == 'CL1'
    assert case['details'][0]['product']['product_family'] == '演示产品族'
    assert case['evidences'][0]['result'] == '观察到表面污染'
    assert case['source'] == loaded_snapshot.payload['sources']['C1']
    assert case['groups'] == []


def test_filters_match_one_detail_and_keep_whole_case_summary(browser_client, loaded_snapshot):
    from casetrace.data.validators import validate_relations

    reference = loaded_snapshot.reference
    reference.products['P2'] = {**reference.products['P1'], 'product_id': 'P2', 'product_name': '第二产品'}
    reference.products['P3'] = {**reference.products['P1'], 'product_id': 'P3', 'product_name': '其他客户产品'}
    reference.product_customers.update(P2='CUS1', P3='CUS2')
    reference.customer_names['CUS2'] = '另一客户'
    records = loaded_snapshot.records
    records['details'][1] = replace(records['details'][1], product_id='P3',
                                    production_lot='PL003', customer_lot='CL003')
    records['details'].append(replace(records['details'][0], detail_id='D3', product_id='P2',
                                      production_lot='0000123', customer_lot='0000456',
                                      detection_stage='Customer', detection_time=date(2026, 6, 8)))
    assert validate_relations(**records, **reference.validator_maps()) == []
    assert browser_client.get('/cases', params={'product_id': 'P1', 'detection_stage': 'Customer'}).json()['matched_count'] == 0
    assert browser_client.get('/cases', params={'product_id': 'P1', 'customer_id': 'CUS2'}).json()['matched_count'] == 0
    data = browser_client.get('/cases', params={
        'product_id': 'P2', 'customer_id': 'CUS1', 'detection_stage': 'Customer',
        'abnormal_process_id': 'P004', 'keyword': '  0000123  ',
    }).json()
    assert data['total_count'] == 2 and data['matched_count'] == 1
    item = data['items'][0]
    assert item['case_id'] == 'C1' and item['detail_count'] == 2
    assert [product['product_id'] for product in item['products']] == ['P1', 'P2']
    assert (item['detection_start'], item['detection_end']) == ('2026-06-01', '2026-06-08')


@pytest.mark.parametrize('keyword,expected', [
    ('c1', ['C1']), ('污染', ['C1']), ('参数异常', ['C2']),
    ('pl1', ['C1', 'C2']), ('cl1', ['C1', 'C2']), ('观察到', []),
])
def test_keyword_and_case_pagination(browser_client, keyword, expected):
    data = browser_client.get('/cases', params={'keyword': keyword, 'page_size': 1}).json()
    assert data['matched_count'] == len(expected) and data['total_count'] == 2
    assert [item['case_id'] for item in data['items']] == expected[:1]
    if len(expected) > 1:
        second = browser_client.get('/cases', params={'keyword': keyword, 'page_size': 1, 'page': 2}).json()
        assert [item['case_id'] for item in second['items']] == expected[1:]
    beyond = browser_client.get('/cases', params={'keyword': keyword, 'page': 99}).json()
    assert beyond['items'] == [] and beyond['matched_count'] == len(expected)


def test_reference_is_bound_to_the_displayed_snapshot(browser_client, loaded_snapshot):
    identity = loaded_snapshot.snapshot.snapshot_id
    data = browser_client.get('/dataset/reference', params={'snapshot_id': identity}).json()
    assert data['snapshot_id'] == identity
    assert data['customers'] == {'CUS1': '演示客户'}
    assert data['product_families'] == {'PF1': '演示产品族'}
    assert data['products'][0]['customer_name'] == '演示客户'
    assert browser_client.get('/dataset/reference').status_code == 200
    assert browser_client.get('/dataset').json()['source_type'] == 'builtin_demo'


@pytest.mark.parametrize('route', ['/cases', '/case', '/dataset/reference'])
def test_read_conflicts_before_using_records(browser_client, route):
    params = {'snapshot_id': 'stale'}
    if route == '/case':
        params['case_id'] = 'missing'
    response = browser_client.get(route, params=params)
    assert response.status_code == 409
    assert response.json()['status'] == 'snapshot_conflict'


@pytest.mark.parametrize('route,params', [
    ('/cases', {'page': 0}), ('/cases', {'page': '1.5'}),
    ('/cases', {'page_size': 101}), ('/cases', {'detection_stage': 'Wire Bond'}),
    ('/cases', {'keyword': 'x' * 201}), ('/cases', {'schema': 'other'}),
    ('/cases', {'snapshot_id': ''}), ('/cases', {'snapshot_id': ' '}),
    ('/case', {'case_id': 'C1'}), ('/case', {'case_id': 'C1', 'snapshot_id': ' '}),
    ('/case', {'snapshot_id': 'current'}), ('/case', {'case_id': '', 'snapshot_id': 'current'}),
    ('/dataset/reference', {'snapshot_id': ''}), ('/dataset/reference', {'path': 'elsewhere'}),
])
def test_read_rejects_invalid_parameters_before_database(monkeypatch, route, params):
    def forbidden(*args):
        raise AssertionError('非法输入不应读库')

    monkeypatch.setattr(api, 'load_database_snapshot', forbidden)
    assert TestClient(api.create_app()).get(route, params=params).status_code == 422


def test_empty_and_missing_case_are_distinct(browser_client, loaded_snapshot):
    identity = loaded_snapshot.snapshot.snapshot_id
    assert browser_client.get('/case', params={'case_id': 'missing', 'snapshot_id': identity}).status_code == 404
    none = browser_client.get('/cases', params={'customer_id': 'UNKNOWN'}).json()
    assert none['total_count'] == 2 and none['matched_count'] == 0
    for items in loaded_snapshot.records.values():
        items.clear()
    loaded_snapshot.payload['sources'].clear()
    empty = browser_client.get('/cases').json()
    assert empty['total_count'] == empty['matched_count'] == 0 and empty['items'] == []
    assert empty['dataset']['case_count'] == 0


@pytest.mark.parametrize('route', ['/cases', '/case?case_id=C1&snapshot_id=current', '/dataset/reference'])
def test_database_failure_is_not_an_empty_library(monkeypatch, route):
    def fail(*args):
        raise OSError('connection secret')

    monkeypatch.setattr(api, 'load_database_snapshot', fail)
    response = TestClient(api.create_app()).get(route)
    assert response.status_code == 503
    assert response.json()['status'] == 'service_unavailable'
    assert 'secret' not in response.text and 'items' not in response.json()


def test_complete_groups_and_two_source_shapes(browser_client, loaded_snapshot):
    from casetrace.data.dataset_model import CaseGroup, Membership

    records = loaded_snapshot.records
    records['groups'].append(CaseGroup('G1', ['project'], '项目组完整说明', None))
    records['memberships'].extend([Membership('G1', 'C1', '本案关联理由'), Membership('G1', 'C2', '另一案')])
    identity = loaded_snapshot.snapshot.snapshot_id
    data = browser_client.get('/case', params={'case_id': 'C1', 'snapshot_id': identity}).json()
    assert data['groups'] == [{
        'group_id': 'G1', 'group_type': ['project'], 'description': '项目组完整说明',
        'other_type_description': None, 'association_reason': '本案关联理由', 'member_ids': ['C1', 'C2'],
    }]
    loaded_snapshot.payload['sources']['C1'] = {
        'closure_status': 'NDF', 'records': [
            {'record_type': 'case', 'record_id': 'C1', 'source_document_id': 'R001',
             'source_document_name': '人工合成报告', 'source_location': '调查结论第一项',
             'filename': 'original.csv', 'csv_line': 2},
        ],
    }
    loaded_snapshot.payload['import_format'] = 'casetrace-csv-v1'
    loaded_snapshot_source = browser_client.get('/case', params={'case_id': 'C1', 'snapshot_id': identity}).json()['source']
    assert loaded_snapshot_source['closure_status'] == 'NDF'
    assert loaded_snapshot_source['records'][0]['source_location'] == '调查结论第一项'
    assert browser_client.get('/dataset').json()['source_type'] == 'csv_upload'


@pytest.mark.parametrize('case_id', ['report/001', '.', '..', 'a/../b', '报告?#001'])
def test_detail_preserves_existing_legal_text_ids(browser_client, loaded_snapshot, case_id):
    from casetrace.data.validators import validate_relations

    loaded_snapshot.records['cases'][0].case_id = case_id
    for kind in ('details', 'evidences'):
        for item in loaded_snapshot.records[kind]:
            if item.case_id == 'C1':
                item.case_id = case_id
    sources = loaded_snapshot.payload['sources']
    sources[case_id] = sources.pop('C1')
    assert validate_relations(**loaded_snapshot.records, **loaded_snapshot.reference.validator_maps()) == []
    response = browser_client.get('/case', params={
        'case_id': case_id, 'snapshot_id': loaded_snapshot.snapshot.snapshot_id,
    })
    assert response.status_code == 200
    assert response.json()['case']['case_id'] == case_id
