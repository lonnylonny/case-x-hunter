# 06：完整流程验收

**交付行为：** 将九案恢复、资料库浏览、增删改与搜索串成可重复验证的使用流程，补齐集成缺口、文档和截图，如实记录限制。

**Blocked by：** [01 完整演示资料恢复](01-demo-dataset.md)、[02 案例资料库浏览](02-case-browser.md)、[03 编辑与修订记录](03-case-editing.md)、[04 新增完整案例](04-case-creation.md)、[05 删除与空库](05-case-deletion.md)。

**Status：** 2026-11-06 已实施；隔离真实实例闭环、全量回归与文档更新完成；运行实例部署与整体记录见 [验收记录](../acceptance.md)。

规范：[完整规格](../spec.md)。定位与执行次序见 [计划](../plan.md)。

## 验收标准

- [x] 在独立验收实例走通：首次九案 → 浏览筛选 → 上传格式示例替换成一案 → 明确恢复九案，分组、来源、日期及设置均正确。
- [x] 走通：编辑现有案例 → 保存 → 展开修订记录 → 新搜索读取新内容 → 刷新/重启仍保留；原出处与网页修订清楚区分。
- [x] 走通：新增含 2 Detail、3 Evidence 的案例 → 保存 → 查看详情与来源 → 搜索，无密钥也可完成基本维护。
- [x] 验证取消、必填/关系校验失败、写入失败、编辑冲突不改变原资料；用户输入在可恢复情况下保留。
- [x] 两标签页及 CSV/恢复/编辑/删除交错时，过期写入被拒绝，尤其重复恢复同一基线不能复用身份绕过冲突。
- [x] 有分组的无效删除被明确拦截；合法无分组案例可以删除，无其他案例或主数据被意外改写。
- [x] 独立单案场景可删除到空库，重启仍为空；从空库新增案例后恢复搜索；再验证从空库导入及恢复完整演示库。
- [x] 正常 CSV/演示整体替换同时替换旧修订记录，失败则整套旧数据和修订记录保留。
- [x] 离线回答验证：进行中请求保持旧快照，新请求使用新快照；来源可定位，修订日志不作为调查证据进入模型。
- [x] 桌面及窄屏的列表、长详情、多子记录编辑、折叠修订、删除提示和空态可操作，附关键截图。
- [x] 对照全部工作包记录验证命令与结果，真实 PostgreSQL、浏览器、离线回放分别说明；未执行或跳过的项目不写成通过。
- [x] 运行适当完整软件回归，保留原 CSV、设置、CLI 文件实验及启动行为；原始数据、评估集、标签、实验记录未改变。
- [x] 更新使用文档和对应行为约定，说明三个入口、九案恢复、统一保存、来源沿用、修订摘要、删除拦截、空库和已知限制。
- [x] 上一轮两页/只整份替换的限制不再出现在新的运行说明中作为当前承诺；历史完成记录仍按当时事实保留。
- [x] 不将软件验收描述为回答质量提升；不付费重评模型，不扩展为展示脚本、主数据编辑或完整审计平台。

## 验证边界

复用 01–05 已有可信证据，主要补跨包集成及修复所影响的行为，不为了收尾重复所有无关实验。真实测试库未配置时明确列为缺口，不使用模拟响应冒充持久性验证。截图记录测试场景，不包含密钥。

验收数据只在隔离实例使用；不为验收覆盖用户当前运行库。实际用户数据迁移或删除仍按仓库要求另行确认。

## 细化执行计划（2026-11-06，待确认）

执行总入口：[剩余工作计划](../remaining-work.md)。本包同时汇总上一轮 [1-5 构建收尾](../../demo-upgrade/issues/05-demo.md)，不重复实施已完成功能，不提前关闭独立展示准备。

### 1. 实施前先确认真实验收路径

记录当前工作树已有改动，不覆盖或重置；核对 01–03 证据及目前缺口。安全检查测试连接存在性和连通性，不输出凭据；按总计划使用测试库独占 schema，或本仓库 `tmp/` 下独立 Compose 实例。验收进程显式连接测试数据库及测试 schema，模型调用强制禁用；不得退回日常数据库。

先执行现有 `test_case_browser.py`、`test_case_editing.py`、`test_dataset_upload.py`、`test_dataset_restore.py` 中真实 PostgreSQL 测试。跳过不算通过；已有断言失败先分清环境与行为，必要回归修复属于本批。真实库可用性尽早确定，避免直到最后才发现原子性无法验证。

### 2. 集成场景与证据

| 场景 | 验收证据 |
|---|---|
| 首次初始化→九案→筛选/详情→单案 CSV→恢复九案 | 真实 API/DB 计数、分组/来源/日期一致；设置保留 |
| 编辑→修订→搜索→刷新→重启 API/执行正常准备 | 内容、来源、修订及身份保持；新增搜索读新内容 |
| 新增 2 Detail/3 Evidence→详情→搜索→重启 | 自动 ID、批号、逐记录来源与 create 修订持久化 |
| 取消/非法输入/注入故障/冲突 | 实体、来源、顺序、摘要、修订整体不变；前端输入在适用时保留 |
| 两标签页及五个写入口交错 | 新增/编辑/删除/CSV/恢复均有旧身份拒绝证据；真实锁竞争覆盖新增、删除，重复恢复身份不复用 |
| 三成员组删除/两成员组阻止/无分组案删除 | 不自动解散组，其他案例及主数据不变；修订和来源正确清理 |
| 单案→删空→重启→新增；重新删空→CSV；重新删空→恢复 | 三条独立空库恢复路径通过；空库检索/回答无模型、无空索引 |
| 进行中回答与写操作交错 | 离线可控回放保持旧快照/下载；新请求读取新快照；模型输入无修订日志 |
| 三页导航、长表单、已有旧缓存升级 | 真实浏览器桌面/窄屏操作及截图，入口与保存按钮可见 |

新增可重复的真实实例浏览器验收脚本（建议 `scripts/web_dataset_acceptance_check.mjs`）；明确服务地址、数据库隔离和停止条件，不向随意传入的日常服务执行删除/替换。现有 `web_case_*` HTTP 夹具脚本继续保留其快速回归作用，记录时与真实后端分开。截图放 `docs/plans/dataset-management/evidence/04/`、`05/`、`06/` 并实际查看，确认无凭据且文案/布局可读。

### 3. 验证命令与收尾顺序

以下为计划入口，不是已执行结果；新增文件按 04/05 落地。定向检查先于全量回归，若后续只改文档不重复运行全部测试。

```bash
uv run --locked --no-sync pytest -q tests/api/test_case_creation_api.py tests/api/test_case_deletion_api.py tests/api/test_search_api.py tests/api/test_answer_api.py
uv run --locked --no-sync pytest -q tests/storage/test_case_browser.py tests/storage/test_case_editing.py tests/storage/test_case_creation.py tests/storage/test_case_deletion.py tests/storage/test_dataset_upload.py tests/storage/test_dataset_restore.py -rs
node scripts/web_case_creation_check.mjs
node scripts/web_case_deletion_check.mjs
node scripts/web_case_editing_check.mjs
node scripts/web_dataset_browser_check.mjs
node scripts/web_cache_browser_check.mjs
node scripts/web_dataset_acceptance_check.mjs
uv run --locked --no-sync pytest -q
git diff --check
```

真实实例浏览器脚本需完成隔离配置后运行；语法检查覆盖改动 JS。全量结果分别统计通过、失败及跳过原因。复查 `data/`、历史 `results/`、主数据和标签无本批改动，不用还原命令破坏用户已有工作树。

### 4. 文档、历史收尾与部署

更新 README、API/网页/Docker 使用说明及行为约定，包含三个入口、新增/删除/空库、日期与来源、冲突、HTTP 状态、缓存刷新及运行更新方法。`docs/development/docker.md` 中早期“网页检索属于任务 02”“上传后重启等待任务 03”等当前说明须按实际功能修正；历史验收记录保留。

上一轮 1-5 按原五项清单关联既有启动/搜索/上传/设置证据及本轮集成结果，记录平台限制；同步其 plan 与包内状态。资料库 01–03 只补真实库验收记录，保留用户“暂时通过”的时间背景。独立 1-6 展示准备保持待确认。

隔离验收通过后，记录日常实例更新前的资料身份、内容摘要、计数及非敏感设置；执行 `docker compose build api` 与 `docker compose up -d --no-deps --force-recreate --wait api`，只更新 API。记录更新前可恢复的镜像身份；若新服务无法健康启动，恢复原 API 镜像，不改数据库。所有命令按实际容器环境及权限运行，不先承诺成功。

部署后验证 `/health`、资料读取、首页/资料库/设置导航和资源版本；用既有缓存刷新入口验证新版可达。更新前后资料与设置须一致；日常实例仅进行读取核对，写路径由隔离实例提供证据。不可把健康检查或服务器文件已更新当作浏览器升级验证。

### 5. 完成与阻塞处理

输出一份整体验收记录（建议 `docs/plans/dataset-management/acceptance.md`），关联各包的实际命令、测试环境、截图和缺口，说明是否完成部署。仅在真实库与跨包必需场景通过后标记完整验收完成；若环境确实阻塞，记录已实现/已验证部分及具体剩余项，不虚报完成。

每包完成后继续下一包，不再逐包问是否执行；只有超出总计划授权的业务规则、迁移、真实资料删除或跨仓库事项另行提出。临时资源仅清理本次明确创建并核对归属的对象，不自动 commit/push。

## 完成记录

2026-11-06：用户确认按修正后计划连续实施。本包只在隔离 schema 的独立实例验收；日常运行实例只做构建、更新与读取核对，未用用户资料做破坏性测试；未调用付费模型、未改 Locked Test、未 commit/push。

实际改动：

- 新增 `scripts/web_dataset_acceptance_check.mjs`：读取 `CASETRACE_TEST_DATABASE_URL`（缺失拒绝运行），创建一次性 schema `casetrace_accept_<随机>`；`storage prepare --test --schema` 导入九案；后台启动 `create_app(db_schema=...)` 的 uvicorn（`CASETRACE_DATABASE_URL` 显式指向测试库、模型密钥置空）；真实 Chromium 走完整流程；结束时只删除本次创建并核对的 schema。
- 验收暴露并修复一项行为问题：删除最后一案后，“已删除”提示会覆盖空库引导文案；现改为空库时保留空态说明。
- 文档更新：README、`docs/development/api.md`（`POST /cases`、`DELETE /case`、`empty_dataset`）、`docs/development/web-demo.md`（新增/删除/空库入口与浏览器回归命令）、`docs/development/docker.md`（删除“网页检索属于任务 02、上传后等待任务 03”等过时表述，增加仅更新 API 的步骤）、`docs/design/behavior-contracts.md`（04/05 约定）。
- 汇总上一轮 1-5 构建收尾，见 [05-demo 完成记录](../../demo-upgrade/issues/05-demo.md#完成记录2026-11-06)；独立 1-6 展示准备保持待确认。

验收命令与结果：

| 检查 | 结果及边界 |
|---|---|
| `node scripts/web_dataset_acceptance_check.mjs docs/plans/dataset-management/evidence/06`（需 `CASETRACE_TEST_DATABASE_URL`） | 23 项通过：九案首次载入与筛选、编辑→修订→新搜索、新增 2 明细/3 证据、重启 API 保留、旧身份保存 409、三成员组删一案后两成员组被 CR-38 拦截、CSV 单案→删空、空库界面与空库搜索、空库→新增→搜索、空库→CSV、空库→恢复九案、再次重启保留。真实 PostgreSQL 隔离 schema + 真实 FastAPI + Chromium，模型调用 0，结束自删 schema |
| `uv run --locked --no-sync pytest -q` | 1005 passed、12 skipped、169 subtests passed；12 个跳过项见 `-rs` 的明确原因，不写作通过 |
| 浏览器专项（HTTP 夹具） | 读取 25、编辑 49、新增 42、删除 34、资料管理 22、缓存升级 10 项通过；夹具证据与上方真实实例证据分开记录 |
| 离线回放与旧回答 | `tests/api/test_case_editing_api.py` 的进行中回答/下载保持旧快照用例随全量通过；模型消息不含修订时间、字段摘要或日志；不产生回答质量结论 |
| 数据边界 | `git status` 无 `data/`、`results/` 改动；未连接日常数据库执行写操作 |

已实际查看 06 证据截图：[初始九案](../evidence/06/01-initial-list.png)、[编辑后详情](../evidence/06/02-edited-detail.png)、[新增表单](../evidence/06/03-create-form.png)、[新增详情](../evidence/06/04-created-detail.png)、[冲突草稿](../evidence/06/05-conflict-draft.png)、[删除确认](../evidence/06/06-delete-confirm.png)、[删除拦截](../evidence/06/07-delete-blocked.png)、[空库](../evidence/06/08-empty-library.png)、[空库搜索](../evidence/06/09-empty-search.png)、[恢复九案](../evidence/06/10-restored-nine.png)。真实实例闭环为桌面视口；窄屏证据见 03/04/05 包。

平台与限制：Windows/macOS 未实测；真实实例闭环未调用模型（按计划使用离线回放与既有证据）；1-6 展示准备未开始。
