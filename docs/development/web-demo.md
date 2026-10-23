# Web 页面（M6-02）

在浏览器里输入当前已知异常，阅读结构化历史参考、展开核对来源，并下载本次运行记录。
页面与回答 API **同源**，复用已验收的链路：PostgreSQL 快照 → R3 检索 → 证据上下文 → 生成 → 引用守卫。

范围：一个中文单页、必要的静态资源托管与使用说明。不含前端构建链、认证、公开部署、流式响应或后台任务。
页面是**历史参考**入口，不判断当前 Incident 的最终 Root Cause；引用可定位不等于引用支持原句。

## 页面位置

| 项目 | 位置 |
|---|---|
| 页面 | `GET /`（例如 `http://127.0.0.1:8000/`） |
| 静态资源 | `/static/app.css`、`/static/app.js`，源码在 `src/casetrace/web/` |
| 资源定位 | 按模块位置（`casetrace.api.WEB_DIR`）解析，与启动目录无关 |

`GET /` 与 `/static/*` 只读取文件，不连接数据库、不构造模型；`/health`、`/answer`、`/docs`、`/openapi.json` 行为不变。

## 打开页面

**宿主 `uv` 路线**（数据库需已 `init` / `import`，见 [PostgreSQL 环境说明](postgresql.md)）：

```bash
uv run --locked uvicorn casetrace.api:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/
```

**容器路线**（见 [Docker 运行链路](docker.md)）：

```bash
docker compose up -d --wait api        # 数据库已 init / import
curl --fail-with-body http://127.0.0.1:8000/health
# 端口以 CASETRACE_API_PORT 为准；浏览器打开 http://127.0.0.1:<端口>/
```

“搜索历史案例”调用 `/search`，无需模型密钥。“生成调查参考”单独调用 `/answer`（需 `DEEPSEEK_API_KEY`，**会计费**）。页面通过 `/model-status` 显示配置状态；未配置或无法读取状态时生成按钮禁用，搜索仍可用。配置状态不证明模型服务可用，调用失败仍按失败展示。

## 离线验收（不调用模型、不计费）

只在真实测试库上运行的固定回放入口：临时 schema 导入 dev-v3，把保存的 Q005 v10 回答用离线替身返回。
它复用既有存储与回答代码，不新增生产模拟开关。

```bash
uv run --locked python scripts/web_demo_smoke.py --port 8010 --search-only
# 浏览器打开 http://127.0.0.1:8010/，提交脚本打印的那条 Development Query 原文
```

- 需要 `CASETRACE_TEST_DATABASE_URL`（真实测试库）；缺省时脚本直接退出；
- 临时 schema 名为 `casetrace_web_smoke_<随机>`，Ctrl+C 或 `kill -TERM <pid>` 退出时自动删除；若删除失败会打印手工清理命令；
- `--search-only` 强制模型密钥为空，可检验真实无密钥搜索；去掉该参数时只回放打印的 Q005 v10 回答；
- 真实模型调用数为 **0**；本入口只用于验收，不提供任意响应注入。

下载可见性与缺损记录的浏览器回归（离线服务启动后，在另一个终端运行）：

```bash
CASETRACE_CHROME_BINARY=/usr/bin/chromium node scripts/web_demo_browser_check.mjs http://127.0.0.1:8010/
```

需要 Node 22+ 和 Chrome，默认使用 macOS 的 Chrome 路径；上例指定 Linux Chromium，其它安装位置可设置 `CASETRACE_CHROME_BINARY`。脚本每次创建独立浏览器 profile，对 ABC 示例和空命中发出真实 `/search` 请求；生成、支持不足和失败用固定响应验证，阻止向服务端发送 `/answer`。不调用真实模型，退出后删除该 profile。可追加截图目录参数保存桌面检索、窄屏和生成回放截图。

## 页面行为

**输入。** Query 文本域、快照日期、`top_k` 与提交按钮；默认 `known_at=2026-09-15`、`top_k=4`。
请求体只有 `query`、`known_at`、`top_k` 三个字段；空白 Query、缺失日期或非正整数 `top_k` 在本地拒绝、不发请求。
异常输入框和搜索／生成按钮是主要入口。“试用示例”位于按钮下方，默认折叠，展开后提供 A 焊线剥离（Q001）、B 塑封空洞（Q002）、C BGA 缺球（Q003）。“填入示例”使用已确认的 Development Query 原文，清除上次结果但不提交，随后收起示例并聚焦输入框；问题仍可编辑。首次加载只读模型配置状态，不自动检索或生成。日期和 Top K 放在高级选项中。

**状态。** 提交后锁定输入并禁止重复提交，完成后恢复；新请求开始即清除旧回答与旧下载目标。
结果按 HTTP 状态与响应 `status` **共同**判定：

| 返回 | 页面行为 |
|---|---|
| 检索 200 + `retrieved` | 展示原始问题、候选排名和可展开的历史 Case／Detail／Evidence 等原文，明确没有调用模型或判断支持程度；可下载检索记录 |
| 检索 200 + `no_hits` | 明确没有检索候选、未调用模型；不声称资料库不存在相关案例 |
| 生成 200 + `ok` | 展示运行摘要、采用案例、候选排名与原文、未采用候选及具体缺口；`case_answers` 为空时显示“支持不足／本次未采用历史案例”，使用警示状态，仍表示流程正常完成 |
| 200 + `no_hits` | 显示无候选与具体不足，不生成空的成功案例卡片 |
| 422 | 显示字段校验提示，保留输入供修改 |
| 502 / 503（模型失败、格式失败、引用失败） | 按 `status` 与安全 `message` 显示失败；失败记录可下载但按钮标注“下载失败记录”；记录里的 `answer_text` 不作为成功回答展示 |
| 503 `service_unavailable` / 500 | 显示安全说明，`record=null` 可处理，不提供下载 |
| 网络/解析失败、结构无法识别 | 明确本次未得到可用响应，保留输入供手动重试；不自动重试 |

**结构化回答。** 成功生成后先显示“采用的历史案例”概览表，列出历史案例编号、产品与客户／生产批／客户批、发现阶段与日期、历史异常原文。基本信息来自该 Case 的历史上下文，不从当前 Query 或其它 Case 补造；一案多条 Detail 时保持各条产品、批次和发现信息的对应关系。点击表格的案例编号可展开并定位对应详情，也可通过下方的原生折叠入口自行展开／收起。

逐案详情分为相关依据、当前／历史事实对照、历史原因／检查／措施及来源；桌面事实对照分栏，窄屏改为单栏。表格在窄屏中独立横向滚动。当前信息缺口保持可见；未采用候选、排名、所有候选原文和请求元数据收在默认折叠的“核对检索候选与本次请求”中，避免默认铺开重复内容。只改变展示，不改变模型输出、采用结果或记录下载内容。

**来源与下载。** 每个采用案例详情中的“来源与原始上下文”默认折叠：展开可看到 `sources` 的 `case_id · field`，以及本次上下文里该 Case 的原始 Case／Detail／Evidence／来源记录／产品背景／异常工序。
下载按钮给出本次 `record` 的 JSON（内容与 API 返回值一致）；替换或清除时会释放对象 URL。

**文本安全与隐私。** 页面所有动态文本都经文本节点写入，Query、模型输出、来源与错误文本不会被当作 HTML 执行；
页面不收集或展示 API key、数据库连接或 schema，模型仅显示配置布尔状态；不使用本地存储或 cookie 保存历史。

## 边界与已知限制

- 回答是历史参考，不产生新的 Ground Truth，也不升级 draft Case；
- 只绑定本机地址，无认证、无连接池、无流式响应、无后台任务；
- 页面不提供数据源/schema/模型选择，跑哪份数据由服务端配置决定；
- 浏览器默认请求 `/favicon.ico` 会得到 404（页面未引用它，不影响功能）；
- `HEAD /` 返回 405：页面按 `GET` 提供（浏览器与 `curl` 的 GET 均正常），`/static/*` 支持 HEAD；用 HEAD 做存活探测请改探测 `/health`；
- 页面的前端行为由 `tests/api/test_web.py` 覆盖静态契约，实际交互以浏览器核验为准；模拟响应只证明前端行为，不代替真实数据库集成证据。

## 代码位置

| 位置 | 内容 |
|---|---|
| `src/casetrace/api.py` | `WEB_DIR`、`GET /`、`/static/` 挂载（其余为 M5-03 的接口） |
| `src/casetrace/web/` | `index.html`、`app.css`、`app.js` |
| `tests/api/test_web.py` | 页面与静态资源的托管契约、前端契约与文本安全 |
| `scripts/web_demo_smoke.py` | 离线回放的本地验收入口 |
| `scripts/web_demo_browser_check.mjs` | 下载可见性与缺损记录的真实浏览器回归（固定响应） |
