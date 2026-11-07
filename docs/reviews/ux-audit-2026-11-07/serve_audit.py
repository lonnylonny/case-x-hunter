"""审查专用进程：文件合成快照、既有回答回放；禁止持久化写入及真实模型调用。

运行：.venv/bin/python docs/reviews/ux-audit-2026-11-07/serve_audit.py
不代表 PostgreSQL 集成验收。仅监听本机 8028。
"""
from datetime import datetime
import json
from pathlib import Path

import uvicorn
from fastapi.responses import JSONResponse
from casetrace import api
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.demo import load_validated_dataset
from casetrace.settings import DEFAULT_SETTINGS
from casetrace.storage import LoadedSnapshot, StoredSnapshot

ROOT = Path(__file__).resolve().parents[3]
dataset = ROOT / 'data/dev/demo-v3.json'
reference_path = ROOT / 'data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx'
records, payload, reference = load_validated_dataset(dataset, reference_path)
identity = DEV_V3_SNAPSHOT
loaded = LoadedSnapshot(records, reference, payload, StoredSnapshot(
    identity.snapshot_id, identity.known_at, str(dataset), identity.dataset_sha256,
    str(reference_path), identity.reference_sha256, payload['split'],
    payload['review_status'], identity.basis, 'audit-file-fixture',
    'content-digest-v2', datetime(2026, 11, 7)),
    {key: len(value) for key, value in records.items()})
api.load_database_snapshot = lambda schema: loaded
api.read_runtime_settings = lambda schema: DEFAULT_SETTINGS

def forbidden_model():
    raise RuntimeError('Audit forbids real model calls')

app = api.create_app(model_factory=forbidden_model)
saved = json.loads((ROOT / 'results/m4/dev-v3-answer-v10/q005.json').read_text())

@app.middleware('http')
async def audit_boundary(request, call_next):
    if request.url.path == '/model-status':
        return JSONResponse({'configured': True})
    if request.url.path == '/answer' and request.method == 'POST':
        return JSONResponse({'status': 'ok', 'message': '审查专用：固定 Q005 回放，非在线生成。', 'record': saved})
    if request.method not in ('GET', 'HEAD') and request.url.path != '/search':
        return JSONResponse({'status': 'audit_read_only', 'message': '审查进程禁止保存及替换资料。'}, status_code=403)
    return await call_next(request)

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8028)
