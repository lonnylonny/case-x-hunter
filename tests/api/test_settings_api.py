"""04：设置输入、安全错误和配置不可用的 HTTP 行为。"""

import pytest
from fastapi.testclient import TestClient

from casetrace import api


@pytest.mark.parametrize('changes', [
    {'top_k': 0}, {'top_k': 11}, {'top_k': True}, {'top_k': 1.5}, {'top_k': '4'},
    {'max_tokens': 1024}, {'max_tokens': True}, {'max_tokens': 2048.0},
    {'max_tokens': 'secret-test'}, {'api_key': 'secret-test'}, {'model': 'secret-test'},
])
def test_invalid_settings_rejected_without_io_or_echo(monkeypatch, changes):
    def forbidden(*args, **kwargs):
        pytest.fail('无效设置不应连接数据库或构造模型')
    monkeypatch.setattr(api, 'connect', forbidden)
    monkeypatch.setattr(api, 'build_model', forbidden)
    response = TestClient(api.create_app()).put('/runtime-settings', json={
        'top_k': 4, 'max_tokens': 4096, **changes,
    })
    assert response.status_code == 422
    assert 'secret-test' not in response.text


@pytest.mark.parametrize('endpoint', ['/search', '/answer'])
@pytest.mark.parametrize('value', [11, None])
def test_request_top_k_limit_is_enforced(endpoint, value):
    response = TestClient(api.create_app()).post(endpoint, json={
        'query': '焊线脱落', 'known_at': '2026-09-15', 'top_k': value,
    })
    assert response.status_code == 422


def test_settings_connection_failure_is_explicit_and_safe(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('secret-database-url')
    monkeypatch.setattr(api, 'read_runtime_settings', fail)
    monkeypatch.setattr(api, 'connect', fail)
    client = TestClient(api.create_app())
    responses = [client.get('/runtime-settings'), client.put('/runtime-settings', json={
        'top_k': 1, 'max_tokens': 2048,
    })]
    for endpoint in ('/search', '/answer'):
        responses.append(client.post(endpoint, json={
            'query': '焊线脱落', 'known_at': '2026-09-15',
        }))
    for response in responses:
        assert response.status_code == 503
        assert 'secret' not in response.text
        assert response.json()['record'] is None
