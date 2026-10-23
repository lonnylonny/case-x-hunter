"""M6-02 本地验收用的离线运行入口：把已验收的 Q005 v10 回答固定回放给 Web 页面。

用途只有一个：让验收者在本机用浏览器走一遍**真实**的同源 HTTP 链路
（页面 → `POST /answer` → 真实 PostgreSQL 快照 → 检索/上下文/引用守卫），
但不调用真实模型、不产生费用，也不需要 `DEEPSEEK_API_KEY`。

它复用的是已有接缝，不新增生产模拟开关，也不改核心：

- 数据准备复用 `casetrace.storage` 的 `initialize_schema` / `import_snapshot`；
- 读库与回答复用既有 `create_app` 与回答核心，服务端照常一次短连接读回快照；
- 离线模型用 `casetrace.answer.model.OfflineChatModel`，只回放保存的 `answer_text`。

用法（需已设置 `CASETRACE_TEST_DATABASE_URL`，即真实测试库）：

    uv run --locked python scripts/web_demo_smoke.py --port 8010

退出（Ctrl+C 或 SIGTERM）时会删除本次创建的临时 schema。真实模型调用数为 0；
本入口只用于验收，不是生产入口，也不提供任意响应注入。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import sys
import uuid

import psycopg
from psycopg import sql
import uvicorn

from casetrace.api import create_app
from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.answer.model import API_KEY_ENV, OfflineChatModel
from casetrace.env import load_local_env
from casetrace.storage import (
    DATABASE_URL_ENV,
    TEST_DATABASE_URL_ENV,
    import_snapshot,
    initialize_schema,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET = PROJECT_ROOT / "data/dev/demo-v3.json"
REFERENCE = PROJECT_ROOT / (
    "data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx"
)
V10_DIR = PROJECT_ROOT / "results/m4/dev-v3-answer-v10"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="M6-02 Web 页面离线验收入口")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；默认只绑定本机")
    parser.add_argument("--port", type=int, default=8010, help="监听端口，默认 8010")
    parser.add_argument("--query-id", default="q005", help="回放的 v10 运行记录，默认 q005")
    parser.add_argument("--search-only", action="store_true", help="强制不配置模型，只验证搜索与证据")
    return parser.parse_args()


def prepare_schema(connection: psycopg.Connection, schema: str) -> None:
    """建表并导入 dev-v3 快照；与真实回答路线使用同一份存储代码。"""

    initialize_schema(connection, schema=schema)
    import_snapshot(connection, DATASET, REFERENCE,
                    snapshot=DEV_V3_SNAPSHOT, schema=schema)


def _interrupt(signum, frame) -> None:
    """把停止信号变成 KeyboardInterrupt，和 Ctrl+C 走同一条清理路径。"""

    raise KeyboardInterrupt(f"收到信号 {signum}")


def drop_schema(url: str, schema: str) -> None:
    """删除本次创建的 schema；失败不静默，明确提示手工清理方式。"""

    try:
        with psycopg.connect(url, connect_timeout=5) as cleanup:
            cleanup.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
            )
    except Exception as error:  # noqa: BLE001 - 清理失败必须让验收者看到
        print(f"警告：未能删除临时 schema {schema}（{type(error).__name__}）；"
              f"请手工执行 DROP SCHEMA IF EXISTS {schema} CASCADE。", file=sys.stderr)


def main() -> int:
    args = parse_args()

    load_local_env()
    url = os.environ.get(TEST_DATABASE_URL_ENV, "").strip()
    if not url:
        print(f"缺少 {TEST_DATABASE_URL_ENV}：本入口只在真实测试库上运行。", file=sys.stderr)
        return 2

    record_path = V10_DIR / f"{args.query_id}.json"
    if not record_path.is_file():
        print(f"找不到保存的运行记录：{record_path}", file=sys.stderr)
        return 2
    saved = json.loads(record_path.read_text(encoding="utf-8"))
    if saved.get("status") != "ok" or not saved.get("answer_text"):
        print(f"保存的记录不是成功回答，无法回放：{record_path}", file=sys.stderr)
        return 2

    schema = f"casetrace_web_smoke_{uuid.uuid4().hex[:8]}"
    # 页面与 API 同源；服务端按常规用 CASETRACE_DATABASE_URL 建立自己的短连接，
    # 这里临时指向测试库，使链路读写的就是刚导入的本次 schema。
    os.environ[DATABASE_URL_ENV] = url

    connection = psycopg.connect(url, connect_timeout=5)
    connection.autocommit = True
    try:
        prepare_schema(connection, schema)
    except Exception:
        connection.close()
        raise

    calls: list[dict] = []

    def offline_factory() -> OfflineChatModel:
        model = OfflineChatModel(saved["answer_text"], model_id=saved["response"]["model"])
        calls.append({"model": model.model_id})
        return model

    if args.search_only:
        # 空环境值优先于 .env，确保本验收进程不能使用本机真实凭据。
        os.environ[API_KEY_ENV] = ""
        app = create_app(db_schema=schema)
    else:
        app = create_app(db_schema=schema, model_factory=offline_factory)
    query, known_at = saved["query"]["text"], saved["query"]["known_at"]
    print(f"临时 schema：{schema}（退出时删除）")
    print(f"离线回放：{record_path.name}｜status={saved['status']}｜真实模型调用 0")
    print(f"页面：http://{args.host}:{args.port}/")
    print(f"浏览器可提交的已有 Development Query（{known_at}）：{query}")

    # uvicorn 优雅关闭后会重新抛出捕获到的信号（uvicorn/server.py 的 capture_signals），
    # 默认处置下 SIGTERM 会直接结束进程、跳过下面的清理。这里显式安装自己的处理器，
    # 让 Ctrl+C（SIGINT）与 SIGTERM 都变成 KeyboardInterrupt，保证 finally 一定执行。
    signal.signal(signal.SIGINT, _interrupt)
    signal.signal(signal.SIGTERM, _interrupt)

    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        pass
    finally:
        drop_schema(url, schema)
        connection.close()
        print(f"已删除临时 schema：{schema}｜本次离线模型构造次数：{len(calls)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
