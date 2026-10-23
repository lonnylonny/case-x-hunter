# 本机 PostgreSQL 环境

M5 使用 Docker 中的 PostgreSQL 17，Python 仍通过项目 `uv` 在本机运行。配置见 [`compose.yaml`](../../compose.yaml)；本页介绍环境、业务建表、导入与核对；数据映射见[数据结构](../data/CaseTrace_Data_Structure_V2_No_Scenario.md#5-数据库实现)。

M6-01 起同一个 `compose.yaml` 还提供 `api` 服务：容器路线的完整运行步骤以 [Docker 运行链路](docker.md) 为准，本页保留宿主 Python 的连接与导入说明。

2026-10-05 M5-01 已验收：PostgreSQL 17.11 / arm64，Psycopg 3.3.6；开发库和测试库各保存一份 dev-v3 快照。初始化、原子导入、完整读回与核对均可运行。当前按学习展示范围使用同步连接、固定建表 SQL 和单快照，不建设通用迁移或多版本语料平台。

## 配置

| 项目 | 本机配置 | 目的 |
|---|---|---|
| PostgreSQL 镜像 | 官方 `postgres:17-bookworm`，本次实际 17.11 / arm64；digest 见下文 | 固定大版本，使用 Apple Silicon 原生镜像 |
| 监听地址 | `127.0.0.1:5432`，可由 `CASETRACE_POSTGRES_PORT` 调整 | 供本机 Python 连接 |
| 开发数据库 | `casetrace` | 保存后续导入的开发案例 |
| 测试数据库 | `casetrace_test` | 测试使用自己的 schema，与开发数据隔离 |
| 应用账号 | `casetrace`，拥有上述两库；非超级用户、不能创建数据库/账号 | 应用与测试进行普通建表及数据操作 |
| 管理员账号 | `postgres`，单独密码 | 首次初始化和必要的数据库管理 |
| 数据卷 | `casetrace-dev_postgres_data` | 容器重建后保留数据库内容 |
| Python 驱动 | `psycopg[binary]`，精确版本见 `uv.lock` | 包含 libpq，无需另装本机 PostgreSQL 客户端库 |

两个密码、端口和开发/测试 URL 保存在未提交的项目根 `.env`。推荐先用 [Docker 启动入口](docker.md) 自动生成数据库配置；仅使用宿主 Python 时，按 `.env.example` 的注释额外配置 `CASETRACE_DATABASE_URL` 和 `CASETRACE_TEST_DATABASE_URL`（使用应用密码的 URL 编码和映射端口）。配置项模板见 [`.env.example`](../../.env.example)。保留已有 DeepSeek 配置；不要把真实密码或 URL 写入报告、日志或 Git。应用命令可调用 `casetrace.env.load_local_env()` 加载，环境变量优先；Compose 自动读取根目录 `.env`。

官方镜像的初始化脚本只在数据卷为空时运行。`docker/postgres/init/01-databases.sql` 创建账号和两个空库，不创建 CaseTrace 业务表。本机首次执行 Shell 初始化文件遇到解释器 Permission denied，已改成由 psql 直接读取的 SQL 文件；健康检查使用应用账号和密码实际连接测试库执行 `SELECT 1`，避免只检查进程启动。后续修改 `.env` 的密码不会自动修改已有数据库账号密码；需要单独执行账号密码修改并同步连接配置。

本次下载镜像的 RepoDigest：`postgres@sha256:639ab7ceb90e13123085b741fb31ef493fba25463002f6da665352e7b534b652`。Compose 使用固定大版本标签；标签未来可能对应更新的小版本，上述 digest 记录本次实测身份，不表示未来重新 pull 仍为相同内容。

## 启动与检查

先打开 Docker Desktop，再在项目根目录执行：

```bash
docker compose config --quiet
docker compose pull postgres
docker compose up -d --wait --wait-timeout 60 postgres
docker compose ps
uv sync --locked --inexact
```

已有镜像且希望避免联网时：

```bash
docker compose up -d --pull never --wait --wait-timeout 60 postgres
```

进入开发库或测试库（客户端使用容器内的 Unix socket）：

```bash
docker compose exec postgres psql -U casetrace -d casetrace
docker compose exec postgres psql -U casetrace -d casetrace_test
```

从本机 Python 验证密码和 TCP 连接，不输出 URL：

```bash
uv run --locked python - <<'PY'
import os
import psycopg
from casetrace.env import load_local_env

load_local_env()
for key in ("CASETRACE_DATABASE_URL", "CASETRACE_TEST_DATABASE_URL"):
    with psycopg.connect(os.environ[key], connect_timeout=5) as conn:
        row = conn.execute(
            "SELECT current_database(), current_user, current_setting('server_version')"
        ).fetchone()
        print(row)
PY
```

停止容器并保留数据：`docker compose stop`。删除容器但保留卷：`docker compose down`。`docker compose down -v` 会删除本项目数据库数据，只在明确需要重置时使用。

## 建表、导入与核对

在项目根目录依次执行：

```bash
uv run --locked python -m casetrace.storage init
uv run --locked python -m casetrace.storage import
uv run --locked python -m casetrace.storage verify
```

加 `--test` 使用测试库；加 `--schema casetrace_test_example` 可指定独立 schema。

- `init` 执行 [`storage/sql/schema.sql`](../../src/casetrace/storage/sql/schema.sql)，建立 15 张业务表和 `schema_version`、`snapshot_metadata`、`runtime_settings` 三张辅助表。重复执行保留数据；实例设置不参与快照摘要或上传替换，重复初始化保留已保存值。结构标记 `002` 与既有库兼容，不再按迁移台账逐版本升级。
- `import` 核对已记录的语料与主数据文件哈希，一次读取 Excel，复用已有 CR/GR/来源校验，在一个事务内写入主数据、实体、关系及元数据。数据库负责必填、主外键、唯一性、枚举、数量和日期约束。
- 已导入相同快照时，`import` 复用读回内容检查后返回 `no_op`。不同快照、已改动内容或未登记的业务数据明确拒绝；失败整体回滚。当前 schema 的元数据表锁让两个导入顺序执行。
- `verify` 是显式诊断命令，报告文件身份、完整快照身份（含可用时点与依据）和完整内容是否一致；失败返回 1。文件身份不符时直接报告，无需继续解析。逐实体、`ReferenceData` 与检索文档的对照由集成测试覆盖。

`load_snapshot(connection)` 从关系表重建五类 dataclass、`ReferenceData` 与完整 payload。源列表和关系列表的顺序按 `snapshot_metadata.ordering` 恢复；来源、draft、split、家族、Query 和可用性依据保持原样。候选知识只入库，不进入检索文本；qrels 不导入。

内容检查集中在读回路径：对日期 ISO 化、字典键排序、列表保留顺序的完整内容求一次 SHA-256，核对已存摘要。保留现有 `content-digest-v2` 格式，使已导入数据库可直接使用；无需清库或重新导入。每次独立读回核对一次摘要，后续回答入口复用此结果，不叠加逐字段核查。

当前快照 `dev-v3-2026-09-15` 共 **244 行业务数据**：9 Case / 9 Detail / 9 Evidence / 1 Group / 3 Membership；主数据为 5 客户 / 7 产品族 / 3 路线 / 18 产品 / 15 工序 / 36 路线工序 / 37 Failure Mode / 74 适用路线。开发库与测试库的三条命令均已通过。

对 Python 调用方，推荐使用公开接口：

```python
from casetrace.storage import connect, load_snapshot

with connect() as connection:
    loaded = load_snapshot(connection)
# loaded.records / loaded.reference / loaded.payload / loaded.snapshot
```

## 回答入口使用数据库

M5-02 起 `casetrace answer` 可选择数据源，两条路线共用同一检索、证据上下文、生成与引用守卫：

```bash
uv run --locked casetrace answer --query-id Q005 --check-only
uv run --locked casetrace answer --data-source postgres --query-id Q005 --check-only
uv run --locked casetrace answer --data-source postgres \
  --query "焊线脱落，已排除运输碰伤" --known-at 2026-09-15 --check-only
```

- 默认 `--data-source file` 行为不变（读 `--data` / `--reference`）。
- `--data-source postgres` 用 `CASETRACE_DATABASE_URL` 连接，`--db-schema` 选择 schema（默认 `casetrace`）；
  `--data` / `--reference` 不参与读取，示例 Query 也来自库内 payload。
- 一次运行只读回一次快照：短连接读回后立即关闭，再检索与调用模型；读回失败按运行前提失败处理
  （stderr 报错、退出码 2、不调用模型、不写回答记录），不会自动建表、导入或退回文件。
- 回答记录在 `corpus` 中区分 `data_source`：文件路线写 `file`，数据库路线写 `postgres` 并附
  `schema`、`content_digest`、`digest_version` 与原始导入路径（表示来源位置，不代表本次读了文件）。
- 旧的 v10 运行记录没有 `data_source`，按文件来源理解，不重写历史产物。

`demo` 与 `evaluate` 仍只使用文件；回答核心的数据库接缝由 M5-02 提供，M5-03 的 FastAPI 复用同一个回答核心。M6-01 起同一批命令可改为在容器内执行，见 [Docker 运行链路](docker.md)。

## 下载失败后的手动恢复

出现镜像或 Python 包下载失败时停止该下载，保留已完成配置，不自动替换不明镜像源或反复重试。

**镜像：** 在能连接 Docker Hub 的网络上执行 `docker pull --platform linux/arm64 postgres:17-bookworm`（本机为 Apple Silicon）。若通过其它机器下载，执行 `docker save -o postgres-17-arm64.tar postgres:17-bookworm`，传到本机后执行 `docker load -i /实际路径/postgres-17-arm64.tar`；再按上面的 `--pull never` 启动命令继续。若 Compose 已固定 digest，离线 tar 导入后应核对镜像 ID/架构，并按实际 tag/digest 调整配置，不能假设 `docker load` 保留 registry digest。

**Python 包：** 按 `uv.lock` 中 Psycopg 和 psycopg-binary 的实际版本下载与本机 Python/架构匹配的 wheel，放到本机目录；缺失的传递依赖也需下载。已有完整 lock 时可执行 `uv sync --locked --inexact --offline --find-links /实际路径/wheels`。若锁文件尚未成功生成，应先恢复依赖解析，不把临时 pip 安装冒充可复现交付。

FastAPI 与应用 HTTP 服务在 M5-03 配置，目前无需全局安装。镜像参数依据 [PostgreSQL 官方镜像说明](https://hub.docker.com/_/postgres)；binary 驱动依据 [Psycopg 官方安装说明](https://www.psycopg.org/psycopg3/docs/basic/install.html)。
