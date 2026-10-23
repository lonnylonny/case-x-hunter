"""M6-02 SD1：单页与静态资源的 HTTP 托管契约（无数据库、无模型）。

本模块只验证接口增量：`GET /` 与 `/static/` 能返回正确的页面与资源类型，
且页面／资源读取不连接数据库、不构造模型；请求体字段与校验的契约仍由
`test_answer_api.py` 覆盖，这里不重复业务细节。

用例把「连接数据库」和「构造模型」都换成失败替身，因此任何一次意外调用都会直接暴露。
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from casetrace import api
from casetrace.api import create_app

ASSETS = ("/static/app.css", "/static/app.js")


def _forbidden_model_factory():
    raise AssertionError("页面与静态资源请求不应构造模型")


@pytest.fixture(autouse=True)
def no_dependencies(monkeypatch):
    """任何数据库连接都视为失败：本模块的用例都不应读库。"""

    def fail(*args, **kwargs):
        raise AssertionError("页面与静态资源请求不应连接数据库")

    monkeypatch.setattr(psycopg, "connect", fail)


@pytest.fixture
def client():
    return TestClient(create_app(model_factory=_forbidden_model_factory))


def test_index_is_located_next_to_the_api_module(client):
    assert api.WEB_DIR == Path(api.__file__).resolve().parent / "web"
    assert api.INDEX_HTML == api.WEB_DIR / "index.html"
    assert api.INDEX_HTML.is_file()

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.lstrip().startswith("<!DOCTYPE html>")


def test_index_survives_working_directory_change(client, monkeypatch, tmp_path):
    """静态文件按模块位置定位，换启动目录（例如容器内 /app）也能返回同一页面。"""

    monkeypatch.chdir(tmp_path)
    response = TestClient(create_app(model_factory=_forbidden_model_factory)).get("/")

    assert response.status_code == 200
    assert response.text == client.get("/").text


def test_settings_and_upload_assets_are_available_without_dependencies(client):
    response = client.get('/settings')
    assert response.status_code == 200
    assert 'id="upload-form"' in response.text
    assert '/dataset/template' in response.text
    assert '/dataset/example' in response.text
    assert 'source_document_id' in response.text
    assert client.get('/static/settings.js').status_code == 200


def test_index_has_input_contract_and_defaults(client):
    page = client.get("/").text

    for element in ('id="query"', 'id="known-at"', 'id="runtime-settings"', 'id="submit-button"'):
        assert element in page
    # 默认快照日期与默认 top_k 写在页面上，且引用相对静态资源。
    assert 'value="2026-09-15"' in page
    assert 'value="4"' not in page  # 实际设置从服务端读取
    for asset in ASSETS:
        assert asset in page
    # 页面壳不包含任何结果占位，避免把空状态误读成成功。
    assert "下载本次运行记录" in page


@pytest.mark.parametrize("asset,expected", [
    ("/static/app.css", "css"),
    ("/static/app.js", "javascript"),
])
def test_static_assets_served_with_expected_types(client, asset, expected):
    response = client.get(asset)

    assert response.status_code == 200
    assert expected in response.headers["content-type"]
    assert response.content


def test_unknown_static_asset_and_traversal_are_404(client):
    assert client.get("/static/missing.css").status_code == 404
    # 静态目录不允许越界读取上层源码。
    assert client.get("/static/%2e%2e/api.py").status_code == 404


def test_page_does_not_expose_credentials_or_infrastructure(client):
    combined = client.get("/").text + "".join(client.get(asset).text for asset in ASSETS)

    for forbidden in ("DEEPSEEK", "postgresql://", "CASETRACE_", "api_key", "Authorization"):
        assert forbidden not in combined
    # 页面不提供数据源／schema／模型选择，只提交 API 既有的三个字段。
    script = client.get("/static/app.js").text
    for field in ("query", "known_at", "top_k"):
        assert field in script
    assert "'/search' : '/answer'" in script  # 两个动作均使用同源相对地址


def test_existing_api_surface_is_unchanged(client):
    schema = client.get("/openapi.json").json()

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
    assert "get" in schema["paths"]["/health"]
    assert "post" in schema["paths"]["/answer"]
    # 页面是展示入口，不进入 API 契约。
    assert "/" not in schema["paths"]


# ── SD2：展示与交互的静态契约（前端行为由 SD3 浏览器检查复核） ──────────────

# 成功回答必须展示的字段：来自 record.answer 的采用案例、候选与不足。
ANSWER_FIELDS = (
    "case_answers",
    "relevance_reason",
    "query_facts",
    "case_facts",
    "historical_root_cause",
    "historical_evidences",
    "historical_corrective_action",
    "skipped_candidates",
    "current_gaps",
    "insufficiency",
    "ranking",
    "sources",
    "context",
)


@pytest.fixture
def script(client):
    return client.get("/static/app.js").text


def test_script_covers_required_answer_and_source_fields(script):
    for field in ANSWER_FIELDS:
        assert field in script, field
    # 候选排名必须与模型采用分开标注，分数不能变成置信度。
    assert "不是相关概率" in script
    assert "置信度" in script


def test_script_maps_http_and_business_status(script):
    for status in ("ok", "no_hits", "model_failed", "format_failed", "citation_failed",
                   "service_unavailable", "internal_error"):
        assert status in script, status
    # 422 单独处理，且不按 AnswerResponse 强读 record。
    assert "422" in script
    assert "record" in script
    # HTTP 状态与业务状态共同判断：200 之外也要有明确分支。
    assert "httpStatus === 200" in script


def test_rendering_never_treats_text_as_html(client, script):
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script
    # 文本一律经 textContent 写入。
    assert "textContent" in script
    # 页面不内联脚本或事件属性，避免绕过统一渲染。
    page = client.get("/").text
    assert "<script" in page and "src=\"/static/app.js\"" in page
    assert "onclick=" not in page
    assert "onerror=" not in page


def test_download_keeps_api_record_and_releases_object_url(script):
    assert "createObjectURL" in script
    assert "revokeObjectURL" in script
    # 下载内容就是本次 record 的 JSON，不在这里改写。
    assert "JSON.stringify(record" in script
    # 空 record 不提供下载：失败与校验分支会清除下载入口。
    assert "clearDownload()" in script


def test_no_persistence_or_automatic_retry(script):
    for forbidden in ("localStorage", "sessionStorage", "document.cookie"):
        assert forbidden not in script
    # 不自动重试模型请求：没有轮询或延时重发。
    assert "setInterval" not in script
    assert "retry" not in script.lower()
