# V2 构建最终审查修复

日期：2026-10-23。用户确认按最小方案修复最终代码审查的两项 P2，并补齐状态响应交错与迟到回答的归属处理。

## 修改

- CSV 表头用一次 `Counter` 计数，重复列名只报告一次，消除平方级扫描。缺列、额外列检查及合法列乱序保持可用；数据库事务和锁范围未改动。
- 搜索页从 BFCache 恢复时重新读取数据状态，读取开始即阻止提交，失败继续保持未就绪。按 `snapshot_id` 判断资料变化：变化时清除旧结果与下载、保留输入；未变化保留结果。
- 数据状态读取用递增序号排除过期成功和失败响应。进行中的搜索／回答继续完成；迟到结果与当前数据集不同时，页面明确标注此前资料归属，下载记录保留原快照。不自动重发请求。

## 针对性验证

- CSV 新增回归先失败后通过：5000 个重复列名原先产生重复错误，修复后同名只报一次。另检查缺列、额外列及合法乱序列，解析测试共 5 项通过。
- 真实 PostgreSQL 上传、数据 API、网页资源及解析相关回归共 **60 passed，0 skipped**。包含大重复表头被拒绝后原数据保持不变、上传替换、回滚、重启和设置保留等现有检查。
- 真实无头 Linux Chromium 共 **13 项通过**。使用真实静态页面和固定 HTTP 响应，覆盖真实 `history.back()`／BFCache、日期变化、同日期换快照、输入及下载保留、刷新中阻止提交、读取失败、旧成功／失败响应交错，以及单次迟到回答的提示和下载。此检查未访问真实数据库或模型。
- 原异常表头离线复测（未将机器相关耗时写成测试阈值）：

| 重复列数 | 文件大小 | 修复前耗时 | 修复后耗时 |
|---|---|---|---|
| 5000 | 40 KB | 0.408 秒 | 0.0010 秒 |
| 10000 | 80 KB | 1.639 秒 | 0.0017 秒 |
| 20000 | 160 KB | 6.551 秒 | 0.0035 秒 |

标准复跑（数据库测试需配置 `CASETRACE_TEST_DATABASE_URL`）：

```bash
uv run --locked --no-sync pytest -q tests/data/test_csv_import.py tests/storage/test_dataset_upload.py tests/api/test_dataset_api.py tests/api/test_web.py
CASETRACE_CHROME_BINARY=/usr/bin/chromium node scripts/web_dataset_browser_check.mjs
```

本机数据库验证实际沿用 04 的临时入口 `/tmp/casetrace-upload-tests.py`，仅为测试进程构造测试库 URL，不输出凭据、不修改 `.env`。

## 增量复审与边界

Standards 与 Spec 两路独立增量审查均为 0 项实质问题。JavaScript 语法检查和 `git diff --check` 通过。

按已确认方案未重复完整验收，未执行真实模型调用或 Windows 实测。这是软件缺陷修复验证，不构成回答质量结论。未提交 Git 或发布。
