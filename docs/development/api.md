# 回答 HTTP 接口（FastAPI）

M5-03 提供本机学习展示用的 HTTP 接口：把当前问题交给同一套回答核心（R3 检索 → 证据上下文 → 生成 → 引用守卫），返回可复查的完整运行记录。

接口遵守[回答核心约定](../design/behavior-contracts.md#3-grounded-answer-核心约定)，HTTP 边界如下：

- 搜索与回答只做 HTTP 适配；资料接口委托 storage 整体替换当前运行数据，不改 qrels、检索方法或提示词；
- 固定走 PostgreSQL（连接沿用 `CASETRACE_DATABASE_URL` / 项目根 `.env`），schema 由服务端工厂配置；
- 客户端只提交问题，不提交连接、凭据、路径、schema、模型选择或数据源；
- 资料接口仅提供固定 CSV 整体替换，不含逐条 CRUD、连接池、缓存、流式响应、后台任务、认证或前端构建链；默认只绑定 `127.0.0.1`；
- 同一应用托管 `GET /`、`GET /settings` 与 `/static/*`（同源调用 API），见 [Web 页面使用说明](web-demo.md)；页面文件读取不连接数据库、不构造模型。

## 安装与准备

```bash
uv sync --locked --inexact
```

数据库连接、初始化与导入见 [PostgreSQL 环境说明](postgresql.md)；容器路线（`docker compose` 同时启动 PostgreSQL 与 API）见 [Docker 运行链路](docker.md)，两条路线的接口行为一致。目标 schema 首次初始化可导入 dev-v3，后续可通过资料接口替换当前快照：

```bash
uv run --locked python -m casetrace.storage init
uv run --locked python -m casetrace.storage import
```

## 启动与检查

```bash
uv run --locked uvicorn casetrace.api:app --host 127.0.0.1 --port 8000
curl --fail-with-body http://127.0.0.1:8000/health          # {"status":"ok"}
```

- `/health` 只证明 HTTP 进程能响应，不代表数据库或模型可用；
- `/docs` 交互文档、`/openapi.json` 契约（请求模型与 200/422/502/503/500 可见）。

## 请求

### 数据状态与整体替换（展示升级 03）

`GET /settings` 为设置页。`GET /dataset` 返回名称、Case/Detail/Evidence 数量、更新时间、登记时点及依据、draft 状态和数据摘要，不返回连接或凭据。`GET /dataset/template` 和 `/dataset/example` 可在无数据库时下载固定 CSV 模板及完整合成示例；`GET /dataset/reference` 返回当前实例允许的主数据和枚举。填写规则见 [CSV 上传格式](../data/upload-format.md)。

`POST /dataset` 的请求体为原始 CSV 字节（`Content-Type: text/csv`），查询参数为 `dataset_name`（1–120 字符）、`known_at`（严格 ISO 日期）、`basis`（1–2000 字符）、`filename`（1–255 字符的 CSV 文件名，无目录），拒绝额外参数和空白／占位。请求流读取超过 5 MiB 即返回 413，不进入数据库。原始字节摘要在 UTF-8 解码前计算；接受 BOM。

上传成功返回 200：`{"status":"imported","message":"当前数据集已整体替换。","dataset":{...}}`。CSV 或业务检查失败返回 422，`errors` 中包含 `row`（物理起始行，无法定位时 null）、`column`、`message`；元数据校验失败沿用框架 `detail`。写入或数据库前提失败返回 503 和安全说明，事务回滚；未预期错误为 500。网络断开时客户端不能由缺少回执推断事务未提交，应读取当前数据状态核对，页面不自动重传。

替换只修改当前运行 schema 的业务记录和单行快照元数据，主数据保持原内容；原文件和评估文件不被写入。读回共享锁覆盖多表读取，替换和启动准备使用同一元数据表的排他锁。模型生成发生在读库连接关闭之后，正在执行的回答保留旧快照并完成。网页上传不调用模型。

### 实例设置（展示升级 04）

`GET /runtime-settings` 返回 `{"top_k":4,"max_tokens":4096}`。`PUT /runtime-settings` 要求同时提交这两个字段；拒绝未知字段，不接收模型、端点或密钥。Top K 为 1–10 的严格整数，输出上限仅支持严格整数 2048、4096。成功返回实际已提交的值；读取或保存不可用返回 503，不能用默认值冒充成功。422 错误保留字段位置与原因，不回显输入值。

非密钥设置保存在当前 schema 的独立单行表中。每次网页/API 请求开始读取一份不可变配置，下一次请求生效，无需重启；已开始的请求保持原参数。CLI 保留原有参数行为。上传替换、`prepare` 与进程重启均保留设置。仍固定 DeepSeek `deepseek-flash`、非思考模式、JSON Output、温度 0；设置页状态只表示密钥是否配置，不验证连接。

已有开发库升级时运行 `uv run --locked python -m casetrace.storage prepare` 补齐设置表；容器主启动入口已执行该步骤。缺失设置表会明确失败，不会自动静默初始化。

### 搜索与回答

`POST /search` 与 `POST /answer` 的 JSON 对象只有三个字段，额外字段拒绝（422）：

| 字段 | 约定 |
|---|---|
| `query` | 必填字符串，拒绝空白；原样传给核心，不改写、截断或反填 |
| `known_at` | 必填 `YYYY-MM-DD`；须与 `GET /dataset` 返回的当前登记日期一致，不接受时间戳或 datetime。内置示例为 `2026-09-15` |
| `top_k` | 可省略，使用已保存实例值（初始为 4）；显式值覆盖本次请求但不修改实例设置。仅接受 1–10 的严格整数，拒绝 bool、小数、字符串及 null |

```bash
curl --fail-with-body http://127.0.0.1:8000/answer \
  -H 'Content-Type: application/json' \
  --data '{"query":"焊线脱落，已排除运输碰伤","known_at":"2026-09-15","top_k":4}'
```

正常路径会调用真实模型（需 `DEEPSEEK_API_KEY`，会计费）。

## 响应

顶层为 `status`、`message`、`record`；`record` 沿用完整的运行记录（排名、上下文、实际模型消息、回答、引用报告、来源身份与用量），可与 CLI `--json` 记录同样复查。读取示例：

```bash
curl --fail-with-body -sS http://127.0.0.1:8000/answer \
  -H 'Content-Type: application/json' \
  --data '{"query":"焊线脱落，已排除运输碰伤","known_at":"2026-09-15"}' \
  | python -c '
import json, sys
response = json.load(sys.stdin)
status = response.get("status")
print(status or "invalid_request")
if status == "ok":
    print(response["record"]["ranking"])
    print(response["record"]["answer_text"])
elif status == "no_hits":
    print(response["record"]["answer"]["insufficiency"])
else:
    print(response.get("message", response.get("detail", "请求失败")), file=sys.stderr)
    sys.exit(1)
'
```

`curl --fail-with-body` 会保留 HTTP 错误的 JSON；读取器先判断状态，只展示成功回答或空命中的具体不足，失败时打印说明并以非零退出码结束。422 使用 `detail`，数据库/未预期错误不读取空的 `record`。

HTTP 与 `status` 的映射：

| 情况 | HTTP | 响应 |
|---|---|---|
| `ok` | 200 | 成功记录与回答 |
| `no_hits` | 200 | 保留具体不足与空候选；不构造模型 |
| `model_failed`（`ModelConfigError`） | 503 | 服务端模型配置不可用 |
| 其它 `model_failed` / `format_failed` / `citation_failed` | 502 | 保留核心状态与失败证据 |
| 请求校验失败 | 422 | 框架 `detail`；不读数据库、不构造模型 |
| DB 配置／连接／未导入／快照前提失败 | 503 | `status=service_unavailable`、`record=null` |
| 未预期异常 | 500 | 通用安全说明，不暴露 traceback |

数据库读取错误在读库入口处理；核心中的快照身份/时点错误用 `SnapshotError` 标记并返回 503。模型构造、检索或响应输出阶段的其它未预期异常返回 500，不误报为数据库故障。

客户端以 HTTP 与 `status` 判断成功：格式／引用失败即使记录里带模型文本，也不能当成成功展示。模型失败记录里的 SDK 异常原文会在 HTTP 返回的副本中换成固定安全说明（保留错误类型、调用标记及其余证据）；`service_unavailable` 只是网络边界状态，不是核心业务状态。

## 无密钥检索与模型配置状态（展示升级 02）

`POST /search` 与回答使用相同数据库读回、R3、快照检查和证据上下文函数；不构造客户端、不调用模型。请求字段与上表一致：

```bash
curl --fail-with-body http://127.0.0.1:8000/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"焊线脱落，已排除运输碰伤","known_at":"2026-09-15","top_k":4}'
```

响应沿用 `status`、`message`、`record` 外壳。HTTP 200 的 `status` 为 `retrieved` 或 `no_hits`；记录版本为 `search-run-1`，含原始 `query`、`snapshot`、`corpus`、`retrieval`、`ranking`、`context`、`notes`，并明确 `response.called_model=false`、`answer=null`。检索候选不是已确认相关案例，不用分数判断支持充分；此记录不是模型回答或引用校验结果。422、503、500 的含义沿用上表。

`GET /model-status` 只返回 `{"configured":true}` 或 `{"configured":false}`。服务端沿用环境变量优先、本机 `.env` 兜底的规则；注入模型工厂的离线测试服务也报告已配置。此接口不连接数据库、不构造模型、不联网验证凭据，不返回密钥或参数明文。配置存在不保证实际调用成功；修改 `.env` 后，宿主 Python 需重启进程；Docker 需重新创建 API 容器，例如 `docker compose up -d --force-recreate api`，普通容器 restart 不会更新已注入的环境变量。

生成仍调用原有 `POST /answer`，完整运行记录、状态和引用守卫保持原约定。存在候选但 `answer.case_answers=[]` 时，页面显示支持不足和具体缺口；HTTP／核心状态仍为 `200/ok`，表示流程完成，不表示证据充分或语义审阅通过。

## 边界与限制

- 回答是**历史参考**，不判断当前 Incident 的最终 Root Cause；引用可定位不等于引用支持原句，语义支持仍需人工审阅。
- 运行记录不产生新的 Ground Truth，也不升级 draft Case。
- 有效请求链路的验收证据用 `TestClient` + 真实 PostgreSQL + 离线模型完成（见 `tests/storage/test_api_integration.py`），不需要真实模型计费请求。

## 代码位置

| 位置 | 内容 |
|---|---|
| `src/casetrace/api.py` | `create_app`、默认 `app`、请求／响应模型、`/health`、`/answer`，以及 M6-02 的 `GET /` 与 `/static/` 挂载 |
| `src/casetrace/web/` | 搜索与资料设置页面、静态资源及完整 CSV 示例，见 [Web 页面使用说明](web-demo.md) |
| `tests/api/` | 无库、无模型的 HTTP 契约、离线状态映射与页面托管契约 |
| `tests/storage/test_api_integration.py` | 真实 PostgreSQL 五 Query 回放与读取边界 |
| `scripts/web_demo_smoke.py` | M6-02 离线回放的本地验收入口（真实测试库 + 临时 schema，不调用模型） |
