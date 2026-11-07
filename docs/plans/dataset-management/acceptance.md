# 资料库升级整体验收记录（2026-11-06）

状态：01–05 已验证接缝、04/05 新功能与 06 隔离真实实例闭环完成；日常运行实例已构建并仅更新 API，更新前后资料身份一致。未 commit/push，未调用付费模型，未改 Locked Test。

## 结果一览

| 项目 | 结果 | 证据 |
|---|---|---|
| 01 恢复九案 | 真实库与浏览器夹具通过 | [01 记录](issues/01-demo-dataset.md) |
| 02 浏览 | 读取 25 项浏览器夹具、真实库测试通过 | [02 记录](issues/02-case-browser.md) |
| 03 编辑与修订 | 真实库 7 项、浏览器 49 项、回答回放通过 | [03 记录](issues/03-case-editing.md) |
| 04 新增完整案例 | API 20 项、真实库 5 项、浏览器 42 项通过 | [04 记录](issues/04-case-creation.md) |
| 05 删除与空库 | API 11 项、真实库 6 项、浏览器 34 项通过 | [05 记录](issues/05-case-deletion.md) |
| 06 隔离实例闭环 | 23 项通过（真实 PostgreSQL + 真实 API + Chromium） | [06 记录](issues/06-acceptance.md) |
| 全量软件回归 | `1005 passed, 12 skipped, 169 subtests passed` | 本页下方命令 |
| 运行实例部署 | 仅重建 API，healthy，身份与计数一致 | 本页「部署记录」 |

## 环境

- 测试库：本机 Compose 的 `casetrace_test`（owner `casetrace`），`CASETRACE_TEST_DATABASE_URL` 指向 `127.0.0.1:5432`；每个用例自建独占 schema 并自清，未连接日常 `casetrace` 库做写操作。
- 真实实例验收：临时 schema `casetrace_accept_<随机>`，`storage prepare --test --schema` 导入九案，uvicorn 指向测试库、模型密钥置空，真实 Chromium 操作；结束只删除本次 schema。
- 日常实例：Compose 项目 `casetrace-dev`（PostgreSQL 5432、API 8000），更新前为九案快照 `edit-71318b9b36a44d36bc4516d0f81172bf`、内容摘要 `6f2d54ad…`。

## 实际命令与结果

```bash
# 真实库存储与 API 测试（CASETRACE_TEST_DATABASE_URL 已配置）
uv run --locked --no-sync pytest -q tests/storage/test_case_browser.py tests/storage/test_case_editing.py \
  tests/storage/test_dataset_upload.py tests/storage/test_dataset_restore.py -rs   # 51 passed
uv run --locked --no-sync pytest -q tests/api/test_case_creation_api.py tests/api/test_case_deletion_api.py \
  tests/api/test_search_api.py tests/api/test_answer_api.py                        # 通过（05 空库状态在删除用例内）
uv run --locked --no-sync pytest -q tests/storage/test_case_creation.py \
  tests/storage/test_case_deletion.py -rs                                          # 5 + 6 passed

# 浏览器专项（HTTP 夹具）
node scripts/web_case_browser_check.mjs        # 25 checks passed
node scripts/web_case_editing_check.mjs        # 49 checks passed
node scripts/web_case_creation_check.mjs       # 42 checks passed
node scripts/web_case_deletion_check.mjs       # 34 checks passed
node scripts/web_dataset_browser_check.mjs     # 22 focused checks passed
node scripts/web_cache_browser_check.mjs       # 10 checks passed（含空库搜索页）

# 隔离真实实例闭环
CASETRACE_TEST_DATABASE_URL=... node scripts/web_dataset_acceptance_check.mjs \
  docs/plans/dataset-management/evidence/06    # 23 项通过，模型调用 0

# 全量回归
uv run --locked --no-sync pytest -q            # 1005 passed, 12 skipped, 169 subtests passed
git diff --check                               # 通过
```

12 个跳过项均有 `-rs` 的明确原因（平台/环境专用），不写作通过。跳过项不包含真实 PostgreSQL 事务、并发与空库路径——这些已在本轮实际执行。

## 部署记录

| 时点 | 内容 |
|---|---|
| 更新前 | API 镜像 `sha256:cc19a70f…`，healthy；资料 `edit-71318b9b…`，9 案 / 9 明细 / 9 证据，`known_at=2026-09-15`，内容摘要 `6f2d54ad…` |
| 构建 | `docker compose build api` 成功，新镜像 `sha256:e6aa2787…` |
| 更新 | `docker compose up -d --no-deps --force-recreate --wait api` 成功，仅重建 API；未重启数据库、未重跑 prepare |
| 更新后 | `/health` 200；`/dataset` 快照身份、计数、名称、审阅状态与内容摘要全部一致；三页共用一个 UI 版本 `ec2b9486bd90273d`；`POST /cases`、`DELETE /case` 对非法请求返回 422（新代码已生效，未写数据）；`/?refresh_ui=1` 返回 `Clear-Site-Data: "cache"`；只读 `/search` 返回 200 |
| 回滚 | 原镜像 `sha256:cc19a70f…` 仍在本地；如需回退，以原镜像标签重建 API 即可，本次未触发 |

日常实例只做健康、读取与页面核对；增删改、CSV 替换与恢复只在隔离实例执行。

## 截图索引

- 01/02/03：`evidence/01/`、`evidence/02/`、`evidence/03/`（历史阶段证据，03 截图已随本轮 UI 重新生成）。
- 04 新增：[桌面表单](evidence/04/create-desktop.png)、[窄屏表单](evidence/04/create-narrow.png)、[新增成功](evidence/04/create-success-desktop.png)、[字段错误](evidence/04/create-error-narrow.png)。
- 05 删除：[桌面确认](evidence/05/delete-confirm-desktop.png)、[窄屏拦截](evidence/05/delete-error-narrow.png)。
- 06 闭环：[初始九案](evidence/06/01-initial-list.png)、[编辑后详情](evidence/06/02-edited-detail.png)、[新增表单](evidence/06/03-create-form.png)、[新增详情](evidence/06/04-created-detail.png)、[冲突草稿](evidence/06/05-conflict-draft.png)、[删除确认](evidence/06/06-delete-confirm.png)、[删除拦截](evidence/06/07-delete-blocked.png)、[空库](evidence/06/08-empty-library.png)、[空库搜索](evidence/06/09-empty-search.png)、[恢复九案](evidence/06/10-restored-nine.png)。

## 未完成与限制

- Windows/macOS 未实测；真实实例闭环使用桌面视口，窄屏证据来自 HTTP 夹具（03/04/05 包）。
- 隔离实例闭环不调用模型；本轮不产生新的回答质量结论，旧指标不适用于新维护资料。
- 独立 1-6 展示准备仍待用户确认，未开始。
- 未 commit/push；工作树包含 01–06 的全部改动，回滚以 git 与镜像标签为准。
