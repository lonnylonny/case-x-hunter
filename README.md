# Case-X-Hunter

面向半导体封装质量调查的 **历史案例检索、评估与可追溯回答系统**，用于 AI Engineering 学习与求职展示。输入当前已知异常，返回值得查看的历史案例、相关依据、历史原因与检查/改善措施，并标明来源和信息缺口；不判断当前事故的最终根因。

## 核心能力与技术栈

- **检索与评估**：实现 BM25、Embedding、Hybrid（RRF）和 Rerank，在同版人工确认标签上比较 Recall、Precision、nDCG 与 MRR，保存逐 Query 排名和失败分析。最终采用 R3：BM25 + Query 侧否定小句/标签词过滤。
- **Grounded Answer**：保留原始 Query，以历史证据生成结构化回答，校验 Case / Evidence 引用；区分成功、空命中及模型、格式、引用失败，支持下载完整运行记录。
- **完整工程链路**：PostgreSQL 原子导入、固定 CSV 整体替换与快照核对，CLI / FastAPI 共用回答核心，搜索与资料设置 Web Demo，Docker Compose 启动；文件与数据库路线用固定回答回放验证一致性。

技术栈：Python 3.12、uv、rank-bm25、Sentence Transformers / PyTorch、DeepSeek（OpenAI 兼容 SDK）、PostgreSQL 17 / psycopg、FastAPI / Pydantic、原生 HTML/CSS/JavaScript、Docker、pytest。

```text
Web Demo → FastAPI ─┐
CLI ───────────────┴→ 文件 / PostgreSQL 快照 → R3 检索 → 证据上下文
                                                    → DeepSeek → 引用校验 → 回答与运行记录
独立评估 CLI：Corpus + Query + 人工确认 qrels → 检索方法 → 排名 / 指标 / 错误分析
```

## 快速启动 Web Demo

前提：Docker Desktop（或 Docker Engine + Compose 插件）已启动，当前用户可访问 Docker。首次需要联网拉取镜像与依赖；宿主无需安装 Python/uv。在仓库根目录执行：

Linux / macOS：

```bash
bash scripts/start-demo.sh
```

Windows PowerShell（Docker Desktop 使用 Linux containers）：

```powershell
./scripts/start-demo.ps1
```

入口会在 `.env` 不存在时生成两份随机数据库密码，构建镜像、初始化数据库、导入合成示例，全部成功后输出页面地址（默认 <http://127.0.0.1:8000/>）。以后仍运行同一命令；当前数据会保留。已有 `.env` 保留原配置，模板占位密码需要先填写；旧数据库卷必须继续使用原密码。容器 URL 由应用密码自动构造，无需手动同步。

网页提供焊线剥离、塑封空洞、BGA 缺球三个合成示例，也可自填问题。点击“搜索历史案例”无需模型密钥，可查看候选和证据原文。需要生成调查参考时，在 `.env` 填写 `DEEPSEEK_API_KEY`，再运行入口；页面会显示配置状态，生成由单独按钮触发并可能计费。搜索使用当前数据集的登记时点（内置示例为 `2026-09-15`），高级 `top_k` 默认 4；交互式 API 文档在 `/docs`。

进入「资料设置」可下载唯一 CSV 模板、完整复杂案例示例与查看合法主数据。模板支持每案多个 Detail 和多个调查项目，逐条来源必填；上传填写完整可用日期及依据，成功后整体替换当前资料，失败保留旧资料，重启继续保留。上传资料维持 draft，不继承旧评估指标。填写规则见 [CSV 上传说明](docs/data/upload-format.md)。

无需模型凭据的链路检查：

```bash
docker compose run --rm --no-deps api casetrace answer --data-source postgres --query-id Q005 --check-only
```

日常停止用 `docker compose stop` 或 `docker compose down`，保留数据库卷。入口参数、自定义端口、已有配置迁移、故障排查及实际平台验证范围见 [Docker 说明](docs/development/docker.md)；接口契约见 [API 说明](docs/development/api.md)。

## 本地开发与复现

前提：Python 3.12 和 uv；以下命令在源码仓库根目录运行。检索 demo、BM25 评估和 `--check-only` 不需要数据库或模型凭据。

```bash
uv sync --locked --inexact
uv run --locked pytest -q
uv run --locked casetrace demo --query "塑封空洞 molding void" --top-k 3
uv run --locked casetrace answer --query-id Q005 --check-only
uv run --locked casetrace evaluate --qrels data/evaluation/dev-v3/qrels.json \
  --method bm25_drop_negation_labels --output /tmp/casetrace-r3-recheck.json
uv run --locked python scripts/m3_07_final_recheck.py /tmp/casetrace-r3-recheck.json
```

`--inexact` 保留本地额外安装的学习工具。真实 PostgreSQL 集成测试需启动数据库并设置 `CASETRACE_TEST_DATABASE_URL`，否则显式跳过，配置见 [数据库说明](docs/development/postgresql.md)。

`demo` 默认是六案 BM25，`evaluate` 默认 dev-v2 / BM25，回答检索固定为 R3，文件路线保留 dev-v3；数据库路线也支持独立登记的当前上传快照。四类方法的配置、模型准备与复现命令见 [实验入口](results/README.md)。无凭据的已保存回答回放及浏览器验证见 [Web Demo 说明](docs/development/web-demo.md#离线验收不调用模型不计费)。

## 实验结果与边界

| 数据集 | 规模与用途 | 结果入口 |
|---|---|---|
| Development dev-v3 | 9 Case × 5 Query，45 对用户确认标签（旧相关性口径）；用于调试和选型 | [四方法比较与 R3 选型](results/dev-v3-m3-07-selection.md) |
| Locked Test v1 | 独立合成 9 Case × 5 Query，45 对用户确认标签；固定 R3 单次链路检查 | [指标、两个漏检与限制](results/locked-test-v1/README.md) |
| Grounded Answer v10 | 五条保存的真实回答，主要字段与引用另行审阅 | [回答质量与已知措辞问题](results/dev-v3-m4-answer-evaluation.md) |

R3 在旧口径 Development 上 Recall@4=0.96；Hybrid / Rerank 未体现收益，因此保留简单方案。Locked Test 单次 Recall@4=0.8833，存在两个漏检；两套数据的规模、口径与用途不同，不能直接计算提升。原始产物和失败记录均保留。

内置数据规模小、全部为合成案例，源 Case 仍为 draft，不证明现场泛化。回答只使用登记快照的可用时点，不提供通用历史时点过滤；用户上传的可用性与来源声明需人工核实。引用可定位不代表语义完全正确，软件测试不代替 AI 质量评估。Locked Test 的一条标签存在用户裁决与通用规则的差异，详见[数据说明](data/evaluation/locked-test-v1/README.md)。本项目按本机学习展示交付，Docker 平台实测范围见 [Docker 说明](docs/development/docker.md)，未实现认证或生产部署。

代码与测试位于 `src/casetrace/`、`tests/`；数据定义见 [docs/data](docs/data/)，业务与评估边界见 [业务与评估约定](docs/design/behavior-contracts.md)。
