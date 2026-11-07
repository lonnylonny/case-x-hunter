# Case-X-Hunter

面向半导体封装质量调查的历史案例检索与 RAG 应用。

输入当前已知异常，查找历史案例、核对证据，并生成带来源的调查参考。历史结论不代表当前异常的最终根因。

## 可以做什么

- **搜索案例**：无需模型密钥，查看候选案例与证据原文。
- **生成参考**：使用 DeepSeek 整理相关依据、历史原因、检查与措施，标明信息缺口，支持下载运行记录。
- **维护资料**：在案例资料库浏览、筛选、编辑、新增、删除案例，或用固定 CSV 模板整体替换；校验失败保留原数据，重启后数据仍在。
- **空库重建**：允许合法删到空库；空库时搜索/生成返回明确业务状态，可新增、导入或恢复九案演示库。
- **调整设置**：网页可保存 Top K 与回答输出上限，下一次请求生效。


## 快速启动

先安装并启动 Docker Desktop，或 Docker Engine + Compose。在仓库根目录运行：

```bash
# Linux / macOS
bash scripts/start-demo.sh
```

```powershell
# Windows PowerShell（Docker Desktop 使用 Linux containers）
./scripts/start-demo.ps1
```

首次启动会生成 `.env`、构建镜像并准备合成示例。浏览器打开终端输出的地址，默认是 <http://127.0.0.1:8000/>。

1. 在搜索页填入示例问题，点击“搜索历史案例”。
2. 需要生成回答时，在 `.env` 填写 `DEEPSEEK_API_KEY`，重新运行启动命令，再点击“生成调查参考”（会产生 API 费用）。
3. 在案例资料库浏览、筛选、编辑、新增、删除案例，或上传/恢复资料；在设置页调整参数。上传与恢复会整体替换当前案例。

再次启动使用同一命令；停止使用 `docker compose stop`，保留数据。已有 `.env` 和数据库卷时保留原密码。

首次构建需要联网，依赖较多，可能较慢。新启动入口已在 Linux 实测；macOS / Windows 尚未实测。配置、端口与排错见 [Docker 说明](docs/development/docker.md)。

资料库支持已有案例的中文整案编辑、新增完整案例、合法删除及折叠修订记录；修改沿用原来源，新增记录首次登记来源，保存通过后立即供新搜索使用。删除前展示影响并说明无回收站，违反分组规则会被拦住；空库可新增、导入或恢复演示。用法见[资料库操作](docs/development/web-demo.md#案例资料库浏览)。

## 检索选择与项目边界

实现并比较了 BM25、Embedding、Hybrid 和 Rerank，最终保留 **BM25 + Query 侧否定小句／标签词过滤（R3）**。选型依据、指标与失败案例见 [检索实验](results/dev-v3-m3-07-selection.md)、[Locked Test](results/locked-test-v1/README.md) 和 [回答质量审阅](results/dev-v3-m4-answer-evaluation.md)。

数据规模小，内置案例均为合成资料。引用可定位不等于语义正确，软件测试通过不代表回答可靠；旧实验指标不适用于新上传资料。

## 本地开发

需要 Python 3.12+ 和 uv：

```bash
uv sync --locked --inexact
uv run --locked pytest -q
uv run --locked casetrace answer --query-id Q005 --check-only
```

`--check-only` 查看证据，不调用模型。PostgreSQL 测试需要单独配置 `CASETRACE_TEST_DATABASE_URL`，否则跳过，见 [数据库说明](docs/development/postgresql.md)。

更多说明：[网页操作](docs/development/web-demo.md) · [CSV 格式](docs/data/upload-format.md) · [API](docs/development/api.md) · [实验复现](results/README.md) · [业务约定](docs/design/behavior-contracts.md)
