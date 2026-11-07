# 01：完整演示资料恢复

**交付行为：** 首次初始化使用项目 V1 九案完整资料；使用者在网页确认后可重新载入完整演示库，能清楚区分完整演示库和单案例上传格式示例。

**Blocked by：** 无，可先开始。

**Status：** 代码已实现，软件/浏览器回归通过；真实 PostgreSQL 验收已随 06 完成

规范：[完整规格](../spec.md)。读代码定位与执行次序见 [计划](../plan.md)。

## 验收标准

- [ ] 首次未登记快照的实例载入既有 9 Case、9 Detail、9 Evidence、1 Group、3 Membership；保留既有示例 Query、合成来源和日期依据，不重新生成资料。
- [ ] 已有运行资料的实例正常启动不被覆盖；零案但已有快照也不等于首次初始化。
- [x] 网页提供“载入完整演示数据”；确认前显示当前资料将被整体替换，包括网页修订记录；取消不改变数据。
- [ ] 恢复成功得到完整九案及原有关系、来源、时点，页面与新搜索一致，示例问题入口仍可使用。
- [ ] 每次恢复使用独立运行快照身份并保存基线来源，过期恢复请求拒绝；不因复用固定 ID 允许旧编辑覆盖新资料。
- [ ] 复用原合成校验和兼容的当前主数据；不通过 CSV 丢弃分组，不替换主数据，不把恢复操作当作重新评估。
- [ ] 主数据不兼容、校验失败或写入中途失败时，原数据、元数据和修订记录全部保留。
- [x] 单案例下载入口及说明改为“上传格式示例”，明确为 1 案、2 明细、3 证据；保留模板下载与现有上传能力。
- [ ] 恢复不重置模型/检索设置，不修改原始文件、评估集或历史实验记录。
- [ ] 已取得旧快照的回答可以完成；新请求读取恢复后的快照；重启保留成功恢复的资料。

## 验证与交接

从已有上传资料出发，经公开 HTTP 和浏览器确认恢复九案，再搜索并检查来源；用真实数据库验证中途失败回滚和重新连接/启动保留。恢复冲突用两次不同版本请求覆盖，模型使用离线回放。

若资料库尚未落地，入口暂放现有设置页；资料库落地后由 02 迁移入口。如果 02 已完成，直接放资料库，不要求它反向依赖本包。向后续包交接基线识别、恢复快照规则及可复用事务接口，不预先扩展通用版本系统。

## 细化执行计划（2026-11-05）

本节为实施前规划，不表示代码已改或验收已通过。沿用已确认规格，不重新访谈；本轮仅更新本地计划，不连接运行数据库、不实施迁移或删除。

### 范围与现有接缝

本包完成“当前资料 → 确认恢复 → 完整九案 → 新搜索”的纵向流程。入口先放设置页；不提前实现资料库浏览、逐案维护或修订记录生成。

| 当前代码事实 | 本包处理 | 依据 |
|---|---|---|
| `prepare_snapshot` 以元数据是否存在决定首次导入，已有快照读回后保留 | 保留启动机制，补齐九案完整性和零案快照回归；不另造自动恢复流程 | [snapshot.py](../../../../src/casetrace/storage/snapshot.py)、[test_prepare.py](../../../../tests/storage/test_prepare.py) |
| CSV 替换已有独占锁、事务、实体替换和摘要读回 | 复用必要写入步骤，增加恢复专用入口及锁内版本核对；不得调用会导入主数据的初始化路径实现恢复 | [replace_csv_snapshot](../../../../src/casetrace/storage/snapshot.py) |
| `load_validated_dataset` 可接收现有主数据，执行 CR/GR；来源另行检查 | 直接载入九案 JSON，使用当前主数据完成原合成检查 | [demo.py](../../../../src/casetrace/demo.py) |
| 数据库回答仅在 `split == user_upload` 时默认采用运行身份，其他情况回落固定身份 | 数据库路线默认采用已加载快照身份；文件路线保留现有身份检查 | [prepare_answer_run](../../../../src/casetrace/answer/context.py) |
| 数据名称的默认判断依赖固定快照 ID，设置页下载文案称“完整合成示例” | 恢复 payload 明确名称与基线来源；修正文案及 API 下载说明 | [api.py](../../../../src/casetrace/api.py)、[settings.html](../../../../src/casetrace/web/settings.html) |
| 浏览器脚本使用真实 Chromium，但数据接口是 HTTP 夹具 | 扩充交互回归，并单独记录真实 API/测试库联调证据，不能混称数据库验收 | [web_dataset_browser_check.mjs](../../../../scripts/web_dataset_browser_check.mjs) |

### 1. 固定恢复契约与身份表示

- 基线始终为 [data/dev/demo-v3.json](../../../../data/dev/demo-v3.json)，身份、原文件 SHA-256、`known_at=2026-09-15` 和依据沿用 [DEV_V3_SNAPSHOT](../../../../src/casetrace/answer/context.py)。不改原文件或生成另一份九案库。
- 首次初始化继续使用现有基线身份；每次用户明确恢复，即使当前内容已经是九案，也生成 `demo-restore-<uuid>` 运行身份。重复提交相同预期版本只允许第一次成功。
- 拟在现有 payload 增加 `dataset_name: 完整演示数据（九案）` 和 `baseline_snapshot_id`。保留基线的 `split`、待审阅状态、五条 Query、逐案来源及其余原始说明；这些元数据不表示新评估结论。
- `dataset_sha256` 继续表示原始 JSON 字节；`content_digest` 按当前主数据、运行身份、payload、实体和顺序重新计算。`reference_sha256` 沿用当前实例主数据来源，不能改写成基线主数据哈希来掩盖差异。
- 不靠运行 ID 前缀推断来源，也不为了绕过回答身份问题将恢复资料标成 CSV 上传。初始固定身份仍须兼容；02 可复用基线来源字段做展示，03 修改后须保留来源沿革并另标人工修订。
- 本包不定义修订记录模型：候选 payload 从干净基线构造，不合并旧运行 payload，因而整体替换不携带旧修订。03 落地正式修订结构时补其专门回归。

### 2. 实现窄范围存储事务

拟在 [snapshot.py](../../../../src/casetrace/storage/snapshot.py) 增加 `restore_demo_snapshot(..., expected_snapshot_id, schema=...)`；基线文件由服务端确定，HTTP 不接受路径、任意基线或替代日期。

事务按以下顺序执行：

1. 检查 schema，取得现有 `snapshot_metadata` 独占锁，读回当前快照并验证摘要。
2. 在同一锁内比较 `expected_snapshot_id`；不一致抛出专用冲突异常，尚未修改任何实体。不能先在 API 读版本、随后无条件写库。
3. 核对基线 JSON 的原文件哈希，调用 `load_validated_dataset(..., reference=current.reference)` 和 `check_source_records`；同时检查发现日期不晚于基线完整可用日期。保留原有 CR、GR、结案证据及合成来源检查。
4. 兼容性的判定是九案引用、关系和合成来源能通过当前主数据校验，不要求当前主数据文件哈希必须等于旧基线。缺失引用、路线不符或来源候选不再合法时明确拒绝；不得静默写回基线主数据。
5. 构建新运行身份和干净 payload，使用当前主数据行及其路线顺序构造完整候选内容、实体顺序和摘要。
6. 按现有依赖顺序替换业务实体、关系和快照元数据；保留主数据与运行设置。事务内调用既有读回检查，成功后才提交。校验、写入或读回任一步失败全部回滚。

只在恢复和 CSV 确实重复的地方提取小型私有写入函数；事务和版本检查由入口明确控制，不建设通用命令/版本框架。继续使用现有 JSONB 和摘要结构，预计不需要 schema 迁移；若实际需要，先提出具体方案。

### 3. 接通 API 与搜索身份

拟新增 `POST /dataset/restore-demo`，请求体：

```json
{"expected_snapshot_id": "页面刚展示的当前快照 ID"}
```

| 结果 | HTTP / 响应约定 | 页面动作 |
|---|---|---|
| 成功 | 200，`status: restored`、中文 message、`dataset: dataset_info(loaded)` | 展示九案、原日期及新身份对应的状态，允许前往搜索 |
| 缺少/空白版本等请求错误 | 422，沿用字段校验格式 | 显示请求问题，不写库 |
| 预期版本已过期 | 409，`status: snapshot_conflict`，提示刷新核对 | 不自动重试；刷新后必须再次明确确认 |
| 主数据不兼容或基线业务校验失败 | 422，`status: invalid_dataset`，提供可解释的记录/字段原因 | 显示具体失败原因，保留当前资料 |
| 资源、数据库或写入故障 | 沿用既有 503/500 服务错误边界，不暴露连接信息 | 提示核对当前状态，不自动重发 |

基线文件缺失或哈希损坏属于服务端资源问题，不能包装成用户字段输入错误。不要用现有上传路径的通用 503 吞掉可解释的兼容性校验结果。

调整 `prepare_answer_run`：未显式提供 `snapshot` 且已有 `loaded_snapshot` 时，从库内身份构造预期身份，不再限定 `user_upload`；显式传入身份仍严格比对，文件默认仍是 `DEV_V3_SNAPSHOT`。保留一次读取后释放连接、再进行模型生成的机制。

按总计划的阶段划分，本包只为恢复请求引入必填预期身份。恢复与恢复竞争，以及 CSV 先完成后旧恢复请求到达，均应返回冲突；旧 CSV 请求随后覆盖新恢复的反向路径，需由 03 将 CSV 接入统一版本检查后封闭。此阶段不能宣称“所有写入入口都已防过期覆盖”。

### 4. 完成设置页交互

修改 [settings.html](../../../../src/casetrace/web/settings.html)、[settings.js](../../../../src/casetrace/web/settings.js)，必要时使用现有样式：

- 在当前资料区域增加“载入完整演示数据”，说明目标是九案完整合成资料。确认内容包含当前名称、整体替换及清除网页修订记录的影响。
- 取消确认不发送 POST。未成功取得当前身份时禁止恢复；请求进行中禁止同页重复恢复或上传。
- 提交的是用户确认所对应的已展示版本，不能确认后暗中刷新版本再覆盖。成功使用返回的完整 `dataset` 更新状态；冲突保留现状并要求刷新核对。
- 网络中断时不声称肯定回滚：响应可能在提交后丢失，提示先核对当前状态，不自动重试。
- 下载入口及 API summary 改成“上传格式示例（1 案、2 明细、3 证据）”；保留现有 URL、模板和 CSV 内容。同步清理当期使用说明中的混淆称呼。
- 搜索页继续使用现有刷新/页面恢复时的数据状态检查；验证恢复后的日期与新身份能够生效，旧结果及下载记录仍标记其旧身份。
- 保留五条基线 Query 数据，验证既有网页示例问题入口可用；不以此为由新增 Query 管理器或扩展当前示例菜单。

### 5. 验证矩阵

优先在现有测试接缝增加可观察行为断言；恢复集成测试可集中放到拟新增的 `tests/storage/test_dataset_restore.py`。

| 场景 | 验证入口与通过条件 |
|---|---|
| 首次准备与正常启动 | 扩展 `test_prepare.py`：无快照首次得到 9/9/9/1/3 和五条 Query，来源/日期正确；已有上传或恢复快照保持不变；有效零案快照也保持不变。零案使用隔离存储夹具，不提前实现删除 API |
| 上传后恢复 | 真实库 + TestClient：先上传一案，再恢复；比较完整实体、分组、成员、来源、Query 与基线，不能只断言计数；主数据、设置不变，旧上传元数据消失 |
| 独立身份与冲突 | 连续两次合法恢复产生不同 ID；旧 ID 再请求为 409。两个独立连接并发使用同一预期 ID，恰好一个成功、一个冲突；补 CSV 成功后旧恢复被拒绝 |
| 主数据兼容与原始检查 | 用合法且摘要一致的隔离快照构造“兼容但与基线不同的主数据”和“不兼容主数据”；前者恢复且主数据保持，后者 422 并给出原因。基线哈希损坏拒绝，不为失败测试篡改仓库源文件 |
| 全量回滚 | 复用上传测试的数据库触发器故障方式，在写入中途失败；另注入读回检查失败。重新连接后旧实体、顺序、来源、payload、摘要均不变；旧 payload 可带测试标记证明未被部分替换 |
| 重启保留 | 恢复后重新连接、执行 prepare 并启动新应用进程，身份、内容、来源和设置保持；参考 `test_dataset_upload.py` 的进程重启测试 |
| 回答与文件边界 | 离线模型暂停在旧快照生成期间，恢复后放行：旧回答/下载仍是旧快照，新搜索/回答是新快照；来源可定位。保留既有数据库/文件路线等价性回归，不调用付费模型 |
| 网页与命名 | 浏览器覆盖取消零写入、成功九案、409、业务错误、网络结果不明、重复点击、示例下载及返回搜索；桌面/窄屏截图不含密钥 |

当前浏览器脚本的 HTTP 夹具只能证明 UI 行为。另在隔离测试实例完成真实“上传一案 → 确认恢复 → 九案搜索”，证据中区分浏览器夹具、真实 PostgreSQL 和离线回答回放。

实施后的建议命令（此处未执行）：

```bash
uv run --locked pytest -q tests/api/test_dataset_api.py tests/storage/test_prepare.py tests/storage/test_dataset_upload.py tests/storage/test_dataset_restore.py
uv run --locked pytest -q tests/storage/test_api_integration.py tests/storage/test_answer_integration.py tests/answer
node scripts/web_dataset_browser_check.mjs
```

真实库测试沿用 `CASETRACE_TEST_DATABASE_URL` 和现有隔离 schema 夹具；不打印连接串、不操作用户运行库。测试库不可用则记录 skipped 和缺口，不能勾选持久性/事务验收。若现有环境不足以完成浏览器或进程重启验证，同样记录未执行原因。

### 6. 文档与交接完成条件

- 按实际实现更新 [web-demo.md](../../../development/web-demo.md)、[api.md](../../../development/api.md)，必要时补 [behavior-contracts.md](../../../design/behavior-contracts.md) 的恢复、身份和失败边界；历史完成记录不改写。
- 包内记录实际修改文件、命令结果、截图链接及尚未验证的项；只根据真实证据勾选上方验收标准。
- 向 02 交接可迁移的恢复控件、接口响应和基线来源字段；向 03 交接冲突异常、锁内检查及最小事务写入接缝，并明确 CSV 反向覆盖缺口和正式修订清理回归。
- 最后核对 diff：原始数据、评估标签、历史 results、主数据文件与模型设置未被规划外修改。无需远程 issue、commit 或 push。

## 实施记录

2026-11-05 实施：

- `storage/snapshot.py` 新增 `restore_demo_snapshot`、专用冲突/业务校验异常，恢复和 CSV 共用窄范围实体替换函数；沿用现有 schema 与 JSONB，没有迁移。
- `POST /dataset/restore-demo` 必填 `expected_snapshot_id`，提供 200/409/422/503/500 边界。每次恢复生成独立身份并保存 `baseline_snapshot_id`，保留基线文件摘要和原日期依据。
- 数据库搜索/回答默认采用已加载的运行身份；显式身份比对和文件路线约定保留。更新两项旧固定身份测试，并保留发现日期越界拦截。
- 设置页增加确认恢复、忙碌/冲突/结果不明处理，下载改名；状态读取用序号防止旧响应覆盖。更新 API、使用说明和行为约定。
- 新增 11 项真实库集成测试：完整基线、身份、来源、主数据、设置、并发、回滚、零案、修订 payload 清理、旧回答及进程重启。

验证证据：

- `uv run --locked --no-sync pytest -q`：761 passed、124 skipped、169 subtests passed（9.22s）；没有付费模型调用。
- `uv run --locked --no-sync pytest -q tests/storage/test_dataset_restore.py -rs`：11 skipped，原因是未设置 `CASETRACE_TEST_DATABASE_URL`。
- `CASETRACE_SCREENSHOT_DIR=docs/plans/dataset-management/evidence/01 node scripts/web_dataset_browser_check.mjs`：20 focused checks passed。真实 Chromium + HTTP 夹具，覆盖取消不请求、重复点击、成功、409、业务错误、网络结果不明及旧回答页面身份。
- 已查看[桌面截图](../evidence/01/restore-desktop.png)及[窄屏截图](../evidence/01/restore-narrow.png)，恢复及上传示例入口可读可操作。截图使用夹具，不是实际库证明。
- `git diff --check` 通过；双轴 code-review 发现的两项前端问题已修复并复查。

限制与交接：

- PostgreSQL 事务、完整来源读回、并发和独立进程重启尚无真实库执行证据；上方相关验收项保持未勾选，不能宣称整包验收完成。真实 API + 浏览器的数据库联调、真实库上的离线回答回放也待测试库可用后执行。
- 修订清理目前验证目标是旧 payload 标记；03 引入正式修订结构后需追加正式记录清理回归。
- 02 迁移设置页恢复入口；03 复用 `SnapshotConflictError` 与锁内预期身份规则，并封闭旧 CSV 反向覆盖缺口。
- 未连接或替换用户运行库，未修改原始数据/评估集/历史 results，未 commit/push。

## 复核记录（2026-11-05）

按用户要求，在规划 02 前以 `HEAD=5beb61a` 对当前未提交的第一包实现复核，包含新增的 `tests/storage/test_dataset_restore.py`。

- Standards 轴：未发现需报告的仓库规范违规或高价值代码异味。
- Spec 轴：发现 1 项页面竞态。初始化/刷新 GET 仍在途中时允许上传；上传成功后延迟旧 GET 会覆盖新的概览及 `currentDataset`，导致恢复无谓返回 409。服务端版本锁不受影响。
- 已修正 `settings.js`：上传及确认恢复开始时使先前读取序号失效。浏览器脚本补充延迟旧 GET 成功及失败两条回归；先在未修复版本观察到 `pre-upload success cannot overwrite uploaded state` 断言失败，再验证修复后通过。
- `uv run --locked --no-sync pytest -q`：761 passed、124 skipped、169 subtests passed（9.57s，页面修正前的完整 Python 复核；修正仅涉及 JavaScript）。
- `uv run --locked --no-sync pytest -q tests/storage/test_dataset_restore.py -rs`：11 skipped，原因仍为未设置 `CASETRACE_TEST_DATABASE_URL`。
- `node scripts/web_dataset_browser_check.mjs`：修正后 22 focused checks passed，真实 Chromium + HTTP 夹具，包含新增的两条竞态回归。沙箱不允许监听本机临时端口，取得工具批准后在沙箱外运行；未使用运行数据库或模型。
- `uv run --locked --no-sync pytest -q tests/api/test_dataset_api.py tests/api/test_web.py`：修正后 31 passed（0.76s）。`git diff --check` 与两份修改脚本的 `node --check` 通过。
- 本次没有改变页面布局，沿用上方 01 截图；没有生成新的截图，也不将夹具截图当作真实库证据。

结论：发现的代码问题已修正，软件及浏览器复核通过；真实 PostgreSQL 事务、持久性、并发及真实 API/浏览器联调仍未验收，整包状态保持待真实库验收。02 无业务前置依赖，已补充其细化执行计划，尚未实现。

2026-11-06：真实 PostgreSQL 验收已随 [06 完整验收](06-acceptance.md) 完成（隔离 `casetrace_test` schema，01–03 存储测试 51 passed），本包状态更新为已通过真实库验收。