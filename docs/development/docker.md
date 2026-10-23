# Docker 启动与数据保留

本页是容器路线的权威说明。使用同一 Compose 项目启动 PostgreSQL、一次性数据准备服务和 FastAPI/Web；宿主 Python 开发路线见 [PostgreSQL 说明](postgresql.md)。

## 前提与主入口

- Docker Engine/Desktop 已启动，当前用户可访问 daemon；安装 Compose 插件（需要 `config --environment`、`up --wait` 和 `service_completed_successfully`）。
- Windows 的 Docker Desktop 使用 Linux containers；宿主只需要 Bash 或 Windows PowerShell，无需 Python/uv。首次构建需要联网，保留现有全部运行依赖（含 torch），下载和构建可能较慢。
- 不要求模型密钥；本阶段启动验证只使用 `--check-only`，不调用真实模型。

在仓库根目录运行：

```bash
# Linux / macOS
bash scripts/start-demo.sh
```

```powershell
# Windows PowerShell
./scripts/start-demo.ps1
```

若本机策略禁止运行已下载的 PowerShell 脚本，可在确认仓库内容可信后，仅对这一次进程使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./scripts/start-demo.ps1
```

配置不存在时，入口创建 `.env`，生成两份不同的随机数据库密码，模型密钥留空。已有配置不会被覆盖。主流程是构建 `api` 镜像 → 等待 PostgreSQL 健康 → 显式运行 `prepare` → 启动并等待 API 健康。只有全部成功才输出地址，默认 `http://127.0.0.1:8000/`；失败返回非零并提供诊断提示。

Compose 的 `api` 还通过 `service_completed_successfully` 依赖 `prepare`，数据库健康不代表业务数据已就绪。入口每次都会显式执行准备检查，不采用旧容器的退出状态来代替本次检查。`/health` 仍只表示 HTTP 进程存活，不表示模型可用。

## 配置及旧实例迁移

| 配置 | 用途 |
|---|---|
| `CASETRACE_POSTGRES_ADMIN_PASSWORD` | PostgreSQL 首次空卷创建管理员；不进入应用容器 |
| `CASETRACE_POSTGRES_PASSWORD` | 应用账号密码，数据库与应用容器共用唯一来源 |
| `CASETRACE_POSTGRES_PORT` | 宿主数据库端口，默认 5432 |
| `CASETRACE_API_PORT` | 宿主网页/API 端口，默认 8000 |
| `DEEPSEEK_API_KEY` | 可留空；需要真实生成时填写，然后重跑入口 |

容器内由 `CASETRACE_CONTAINER=1` 和应用密码构造固定的 `postgres:5432/casetrace` 地址，并处理密码 URL 编码；不再读取旧 `CASETRACE_CONTAINER_DATABASE_URL`。旧文件可保留该项或删除，无需继续同步。宿主开发的 `CASETRACE_DATABASE_URL` / `CASETRACE_TEST_DATABASE_URL` 保持显式配置，容器不使用这两项。

Compose 的宿主环境变量优先于 env 文件；输出地址使用 Compose 实际解析的端口。不要将真实凭据放进命令行、日志或提交。`.env` 同时被 Git 和 Docker 构建上下文忽略，请私下备份。

旧 `.env` 保留两个真实数据库密码、自定义端口及模型密钥即可；仍为 `replace_with_…` 的模板值会被入口拒绝并指出键名。已有卷的密码必须保持原值，修改 `.env` 不会轮换数据库账号密码。配置遗失时应恢复原凭据；入口不会通过删卷恢复“成功”。

更改端口只需改对应配置后重跑入口，不自动选择其它端口。自定义 env 文件可从任意工作目录传入，路径相对于调用目录：

```bash
bash /path/to/case-x-hunter/scripts/start-demo.sh /tmp/casetrace-demo.env
```

```powershell
& 'C:/path/to/case-x-hunter/scripts/start-demo.ps1' -EnvFile 'C:/private/casetrace-demo.env'
```

## 首次准备与日常重启

| 数据状态 | `python -m casetrace.storage prepare` 行为 |
|---|---|
| 无业务结构 / 空实例 | 建表，在事务内导入示例并读回核对，返回 `imported` |
| 有有效快照 | 校验当前数据内容摘要，返回 `preserved`；不读取样例文件，不改导入时间或数据 |
| 无快照记录但已有业务数据 | 明确拒绝，不覆盖已有内容 |
| 不兼容结构、内容损坏或连接失败 | 明确报错，保留数据，启动入口失败 |

准备过程复用现有存储校验和事务。`init` / `import` / `verify` 仍可用于开发诊断；`verify` 比较数据库与指定源文件，不应在未来上传数据后用默认示例执行它并据此认定数据库损坏。

```bash
# 再次启动：同一个主入口会重新检查数据
bash scripts/start-demo.sh
# 正常停止或移除容器，均保留命名卷
docker compose stop
docker compose down
```

Windows 重启使用同一个 `./scripts/start-demo.ps1`，停止命令相同。`docker compose start/restart` 仅操作现有容器，不替代入口的数据检查。

默认卷仍为 `casetrace-dev_postgres_data`。`docker compose down -v` 会删除该项目数据库卷，日常停止不要带 `-v`。

无需模型凭据的业务检查：

```bash
docker compose run --rm --no-deps api casetrace answer --data-source postgres --query-id Q005 --check-only
```

浏览器打开输出的地址，页面内容和静态资源随同一镜像托管。当前网页提交调用模型；未配置密钥时可通过上述 CLI 检查证据，网页检索属于任务 02。上传后重启的端到端验收等待任务 03；本阶段准备逻辑已支持保留有效的非示例快照，但不提前扩展回答核心支持的快照范围。

## 失败怎么定位

入口打印带实际 env 文件路径的诊断命令。默认配置可用：

```bash
docker compose ps -a
docker compose logs --tail 80 postgres prepare api
```

| 现象 | 操作 |
|---|---|
| 找不到 Docker / Compose | 安装 Engine/Desktop 与 Compose 插件，重开终端后检查 `docker compose version` |
| daemon 不可访问 | 启动 Docker；Linux 检查当前用户的 socket 权限，Windows 检查 Desktop 与 Linux containers |
| 配置缺失、为空或占位值 | 根据错误键名补齐真实值；已有卷恢复原凭据 |
| 地址已占用 | 修改 `CASETRACE_API_PORT` 或 `CASETRACE_POSTGRES_PORT` 后重跑入口 |
| 镜像构建失败 | 检查原始构建错误与网络；修复后重跑以复用缓存，不更换不明镜像源 |
| PostgreSQL 健康检查失败 | 查看 postgres 日志，确认端口、原卷密码及初始化错误；不要删卷 |
| 数据准备失败 | 查看入口输出及 prepare 日志，检查结构版本、摘要或源文件错误；修复后重跑入口 |
| API 健康检查失败 | 查看 api 日志与端口；即使先前容器仍运行，也不能把本次失败当作成功 |
| `/answer` 返回 503 | 区分数据库 `service_unavailable` 与模型 `model_failed`；启动成功不代表模型凭据有效 |

临时 `run --rm prepare` 的输出保留在本次入口终端；对应临时容器会移除，后续日志主要查看 Compose 管理的 `prepare` 和 postgres。

## 隔离验证与清理

验收使用仓库外配置、独立 Compose project 和临时端口，不使用日常开发卷。env 文件中设置 `COMPOSE_PROJECT_NAME=casetrace-startup-check`，并设置两个随机密码及临时端口（例如 55473 / 18073），模型密钥留空；然后把该文件传给主入口。

后续 `ps/logs/stop/down` 都必须继续带相同配置：

```bash
docker compose --env-file /tmp/casetrace-check.env ps -a
docker compose --env-file /tmp/casetrace-check.env down
# 仅在确认属于本次独立验收、无需保留数据后清理该卷
docker compose --env-file /tmp/casetrace-check.env down -v
```

`down -v` 不清理镜像或构建缓存。先用 `docker system df -v` 检查；不要为清理本次验证而执行全局 prune。不同项目共用 `casetrace-api:dev` 镜像标签，测试期间避免同时构建不同版本。

## 新入口实际验证范围

2026-10-22：Linux x86_64（Bash 5.3.15、Docker 29.7.2、Compose 5.5.1、PostgreSQL 17.11）完成新配置首次启动、页面和无模型证据检查、重复启动、stop/down 后保留数据、端口冲突与准备失败验证。完整测试 775 passed、12 skipped、169 subtests passed，跳过项为本机缺少 PowerShell。

详细结果见 [01 启动任务记录](../plans/demo-upgrade/issues/01-startup.md#完成记录)。macOS 与 Windows PowerShell 新入口尚未实测。下方 macOS 历史记录是旧手动流程，不能当作新入口的平台通过证据。

## 历史镜像与实测身份（旧手动入口）

2026-10-06 SD1 实测（macOS arm64，Docker 29.8.1）：

| 项目 | 值 |
|---|---|
| 基础镜像 | `python:3.12-slim-bookworm` → `python@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258`（Debian 12，容器内 Python 3.12.15） |
| uv | `ghcr.io/astral-sh/uv:0.12.22` → `sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc` |
| 应用镜像 | 由 `docker compose build api` 产出并标记 `casetrace-api:dev`，本机 arm64，约 2.95GB |

标签未来可能对应更新的小版本，上表是本次实测身份，不表示以后重新拉取内容相同。

2026-10-10 构建修正：依赖安装前只复制 `pyproject.toml` 和 `uv.lock`，README 随源码复制；直接以运行用户安装依赖、用 `COPY --chown` 设置文件所有者，取消末尾递归 `chown`。修正版 arm64 镜像实测约 **1.62GB**（原约 2.95GB）；README 与源码分别发生小改动后，第三方依赖安装层均命中缓存。

## 已知限制

- 镜像保留全部运行依赖（含 torch），历史 arm64 构建约 1.62GB，实际占用依平台和 Docker 存储方式变化；尚未进行依赖分组调整。
- 容器内不承诺 `casetrace demo` 与全部 `evaluate` 输入齐备：镜像只包含 dev-v3 语料与主数据，qrels、实验记录、测试夹具与模型缓存在需要时用只读挂载提供。
- 只做本机原生架构验证；认证、连接池、CI/CD 或公开部署不在范围内。
- 回答是历史参考，不判断当前 Incident 的最终 Root Cause；软件回归通过不代表检索或回答质量结论。
