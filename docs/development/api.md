# 回答 HTTP 接口（FastAPI）

M5-03 提供本机学习展示用的 HTTP 接口：把当前问题交给同一套回答核心（R3 检索 → 证据上下文 → 生成 → 引用守卫），返回可复查的完整运行记录。

接口遵守[回答核心约定](../design/behavior-contracts.md#3-grounded-answer-核心约定)，HTTP 边界如下：

- 搜索与回答只做 HTTP 适配；资料接口委托 storage 整体替换当前运行数据，不改 qrels、检索方法或提示词；
- 固定走 PostgreSQL（连接沿用 `CASETRACE_DATABASE_URL` / 项目根 `.env`），schema 由服务端工厂配置；
- 客户端只提交问题，不提交连接、凭据、路径、schema、模型选择或数据源；
- 资料接口提供案例浏览、完整只读详情、整案编辑、新增与删除、CSV 整体替换与演示恢复；不含分组/主数据编辑、回收站、连接池、缓存、流式响应、后台任务、认证或前端构建链；默认只绑定 `127.0.0.1`；
- 同一应用托管 `GET /`、`GET /library`、`GET /settings` 与 `/static/*`（同源调用 API），见 [Web 页面使用说明](web-demo.md)；页面文件读取不连接数据库、不构造模型。

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

`GET /settings` 为模型/检索设置页，`GET /library` 为资料库页，上传、恢复及主数据入口均在资料库。`GET /dataset` 返回名称、Case/Detail/Evidence 数量、更新时间、登记时点及依据、draft 状态、来源类型 `source_type` 和数据摘要，不返回连接或凭据。`GET /dataset/template` 和 `/dataset/example` 可在无数据库时下载固定 CSV 模板及上传格式示例（1 案、2 明细、3 证据）；`GET /dataset/reference` 返回当前实例允许的主数据和枚举。填写规则见 [CSV 上传格式](../data/upload-format.md)。

`POST /dataset` 的请求体为原始 CSV 字节（`Content-Type: text/csv`），查询参数为 `dataset_name`（1–120 字符）、`known_at`（严格 ISO 日期）、`basis`（1–2000 字符）、`filename`（1–255 字符的 CSV 文件名，无目录）、必填 `expected_snapshot_id`（打开页面时的当前身份），拒绝额外参数和空白／占位。请求流读取超过 5 MiB 即返回 413，不进入数据库。原始字节摘要在 UTF-8 解码前计算；接受 BOM。

上传在写事务锁内先核对 `expected_snapshot_id`，过期返回 409 `snapshot_conflict`，不解析并覆盖新资料；缺少身份返回 422。

上传成功返回 200：`{"status":"imported","message":"当前数据集已整体替换。","dataset":{...}}`。CSV 或业务检查失败返回 422，`errors` 中包含 `row`（物理起始行，无法定位时 null）、`column`、`message`；元数据校验失败沿用框架 `detail`。写入或数据库前提失败返回 503 和安全说明，事务回滚；未预期错误为 500。网络断开时客户端不能由缺少回执推断事务未提交，应读取当前数据状态核对，页面不自动重传。

替换只修改当前运行 schema 的业务记录和单行快照元数据，主数据保持原内容；原文件和评估文件不被写入。读回共享锁覆盖多表读取，替换和启动准备使用同一元数据表的排他锁。模型生成发生在读库连接关闭之后，正在执行的回答保留旧快照并完成。网页上传不调用模型。

### 整案维护（资料库工作包 03）

`PUT /case?case_id=...` 接收整案 JSON，保留合法任意文本编号的 query 传递方式；拒绝其他 query 和嵌套额外字段。请求流上限 5 MiB。主要字段：

| 字段 | 内容 |
|---|---|
| `expected_snapshot_id` | 必填，打开编辑时的快照身份；不能以提交前读取最新身份绕过冲突 |
| `case` | `abnormal_description`、`root_cause`、`corrective_action`、可空 `investigation_others`、`abnormal_processes`；不传 case_id |
| `details` | 保存后的全部明细，字段与读取实体一致，去掉父 case_id、产品背景和名称投影；既有 detail_id 保留，新增省略 ID |
| `evidences` | 保存后的全部 Case 级调查项，去掉父 case_id；既有 checkpoint_id 保留，新增省略 ID |
| `closure_status` | 显式 `confirmed` 或 `NDF`，原值预填，独立于 draft 审阅状态 |
| 每条记录的可选 `origin` | 省略即沿用；新子记录必填。`kind=report` 需 source_document_id、source_document_name、source_location；`kind=synthetic` 需 description（人工合成说明）和 source_location。不得提交 filename/csv_line |
| 可选 `availability` | 改变时提交 known_at、basis、confirmed_complete=true；省略沿用整库日期与依据，不由保存时间推定 |

已有 ID 只允许属于目标案，省略已有子记录表示移除；新增 ID 由系统分配。保留既有源顺序及其他案例、分组和主数据，工序/异常类型仅换序不产生修订。数量严格为正整数且不超 PostgreSQL integer 上限；日期只接受 YYYY-MM-DD，所有文本拒绝 NUL。

服务端在同一排他锁事务中先核对身份，再校验完整候选集的 CR、来源、confirmed 证据门槛、日期和规模，写实体/来源/顺序/摘要/逐案日志并读回核对。人工维护不套用合成 GR 或强迫正文继续包含原合成候选；恢复基线和文件路线维持原校验。

成功返回 200 `{"status":"saved","dataset":{...},"detail":{...}}`；真正无变化为 `status=unchanged`，保留原身份、时间和日志。`detail` 与 GET 的完整形状一致。过期为 409 `snapshot_conflict`（优先于案例查找、业务校验和无变化判断），当前身份无案为 404；业务错误为 422 `invalid_dataset`，`errors` 带 path、record_id、field、message；结构错误仍为安全的 `detail`。体积超限 413，服务失败 503/500，失败事务回滚。网络结果不明不能推断未提交，应先核对当前身份及详情，不自动重发。

首次实际维护使用新运行身份，`source_type=user_maintained`，原文件摘要、基线来源仍作沿革，资料保持待人工审阅。逐案 `revisions` 只存时间、操作、字段路径和增删记录 ID，不存全文、姓名或手写理由。原 CSV 行号及合成候选不会变成修订正文证明；当前合法来源及必要修订边界进入回答来源，修订日志/时间/技术摘要不进入模型证据。CSV 或恢复成功整体替换会清除旧日志。

### 新增完整案例（资料库工作包 04）

`POST /cases` 接收与整案编辑同形的 JSON：`expected_snapshot_id`、`case`、`details`、`evidences`、`closure_status`、可选 `availability`。新增不接受客户端 Case/Detail/Evidence 编号（`case` 内出现 `case_id` 或子记录出现既有 ID 均为 422），也不接受分组、客户归属或审阅状态覆盖。每条记录必须登记 `origin`，Case 级来源与至少 1 条明细、1 项证据缺一不可；请求流上限 5 MiB，规模仍为 500 案／10000 条业务记录。

服务端在同一排他锁事务中核对身份，生成全局唯一的 `C<uuid>`/`D<uuid>`/`E<uuid>` 编号，构造完整候选并执行与编辑相同的 CR、来源、confirmed 证据、日期与规模校验；新案默认不入组，`sources[case_id]` 记录逐记录首次来源与 `operation=create` 初次修订，待人工审阅。不通过先写占位案再编辑实现新增。

成功返回 201：`{"status":"created","case_id":...,"dataset":{...},"detail":{...}}`，`detail` 与 GET 同形并含初次修订。过期 409、结构或业务错误 422（业务错误带可定位 `errors`）、超限 413、服务失败 503/500；失败全部回滚，输入由页面保留。重复提交同一旧身份最多成功一次，响应丢失后不自动重试。

### 删除案例与空库（资料库工作包 05）

`DELETE /case?case_id=...` 的 JSON 请求体为 `{"expected_snapshot_id": "...", "confirmed": true}`（严格布尔，缺失或 false 为 422；不接受额外字段）。HTTP 方法带请求体属既有写入口风格；测试客户端需用 `client.request('DELETE', ...)`。

服务端在锁内先核对身份（过期优先返回 409），再确认案例存在（404），移除该案、子记录、成员关系、来源映射与逐案修订，保留全部 Group 并交给完整候选校验：删除导致组不足两案（CR-38）或其它规则失败返回 422 并列出原因，不自动解散组、不改其他案例。成功返回 200 `{"status":"deleted","case_id":...,"dataset":{...}}`，顺序与摘要同步更新；重复旧请求返回 409，数据库故障 503 且事务回滚。不提供回收站，删除失败或取消不产生修订记录。

合法删除最后一案后保留零案快照与主数据。此时对 `/search`、`/answer` 的有效请求在时点校验通过后、进入检索或模型路径前返回 HTTP 200 `{"status":"empty_dataset","message":"当前资料库为空：...","record":null}`，不构建检索器、不调用模型；服务未初始化/不可用时仍为原 503，非空库零命中仍为 `no_hits`。CSV 导入仍要求至少一案，正常重启不回填九案。

### 案例资料库读取（资料库工作包 02）

`GET /cases` 返回 `dataset`、`total_count`（整库）、`matched_count`（分页前匹配）、`page`、`page_size` 和 `items`。每项包含 `case_id`、完整 `abnormal_description`、去重产品与对应客户、已确认异常站点、`detection_start/end` 和整案 `detail_count/evidence_count`。按源 Case 顺序稳定分页；摘要在页面限制行数，接口不截断文本。

| 查询参数 | 规则 |
|---|---|
| `keyword` | 最多 200 字符，去首尾空白、不区分大小写的子串匹配：编号、异常、历史根因及任一明细的生产批/客户批 |
| `product_id` / `customer_id` / `abnormal_process_id` | 各最多 200 字符，空值表示不筛选；合法格式但不存在的编号返回零匹配 |
| `detection_stage` | 空值或既有发现阶段枚举；不接受工序名称代填 |
| `page` / `page_size` | 默认 1 / 20；page ≥ 1，page_size 为 1–100 的整数；页码越界保留真实计数并返回空 items |
| `snapshot_id` | 首次列表可省略；若提供必须非空，最多 200 字符；后续筛选/翻页使用已展示身份 |

产品、客户、阶段必须由同一条 Detail 同时满足，工序检查 Case 的已确认异常工序；所有指定条件之间为 AND。列表汇总不裁掉未匹配的子记录。未知查询参数被拒绝（422）。

`GET /case?case_id=...&snapshot_id=...` 要求提供当前列表身份，返回 `snapshot_id`、完整 `case`、`details`、`evidences`、`groups`、`source`、`revisions`。保留实体和来源顺序；明细附产品/客户/产品族/路线及异常类型名称，分组附本案关联理由和成员编号，Evidence 属于整案。`source` 仍是原来源结构：合成候选说明或 CSV 逐记录报告位置；不推造报告或导入前历史；网页维护后增补 `web_provenance` 标记及逐记录来源，`revisions` 为实际网页修订摘要，无历史时为空列表。

`GET /dataset/reference` 保持原键与无参数读取兼容，增加 `snapshot_id`、`customers`（客户编号到名称）、`product_families`，产品中附客户和产品族名称。可传 `snapshot_id` 约束当前身份，页面以此避免混用列表与另一快照主数据。

这三类读取每次只加载一次完整快照，不构建检索器、不读取模型设置、不构造模型。身份不符先返回 409 `snapshot_conflict`；同身份下无案例为 404 `case_not_found`。非法输入为 422；数据库/内容前提不可用为 503，未预期故障为 500，均不将错误冒充零案。

`dataset.source_type` 为 `builtin_demo`、`csv_upload`、`user_maintained` 或 `other`，表示现有载入途径；保留 `is_upload` 兼容调用方。运行身份、`baseline_snapshot_id`、原文件摘要和内容摘要继续各自表达原含义。逐案编辑、新增或删除后的资料标记为 `user_maintained`，写入契约见下两节。

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
| `empty_dataset` | 200 | 当前资料库为零案；`record=null`，不构建检索、不调用模型 |
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


### 完整演示库恢复

`POST /dataset/restore-demo` 接收 JSON `{"expected_snapshot_id": "GET /dataset 返回的当前 ID"}`，拒绝额外字段。服务端使用固定九案 JSON，并在同一数据库锁和事务中核对当前身份、校验当前主数据兼容性、替换案例与关系、来源、顺序及元数据，再读回核对摘要。主数据和运行设置保留。

成功为 200，返回 `status: restored`、`message` 和 `dataset`；过期版本为 409 `snapshot_conflict`；业务不兼容为 422 `invalid_dataset` 并附具体原因；资源或数据库故障沿用 503/500 边界。失败不部分写入。客户端不得自动重发结果不明的写请求。

恢复快照为独立运行身份，`baseline_snapshot_id` 保留原基线来源；`dataset_sha256` 仍是原 JSON 文件摘要，`content_digest` 标识当前运行内容。文件实验路线身份约定不变；恢复不产生新的质量指标。CSV、恢复、整案编辑、新增与删除统一检查预期快照身份。


### 页面发布版本与缓存

三个 HTML 入口 `/`、`/settings`、`/library` 及 `/static/` 响应使用 `Cache-Control: no-cache`，要求使用前向服务端重新校验；静态资源的条件请求也保留该策略。HTML 中的站内三页链接和静态资源 URL 附加同一 `ui` 内容版本（由当前 HTML/JS/CSS 内容计算），避免旧页面缓存或不同发布的资源混用；下载与业务 API 地址不改写。

任一 HTML 入口明确传 `refresh_ui=1` 时，额外返回 `Clear-Site-Data: "cache"`，用于一次清除更新前已存在的 HTTP 缓存；普通请求、静态资源和业务 API 不触发清理，不包含 cookies/storage 清理指令。此操作不访问数据库、不改变资料或模型设置。
