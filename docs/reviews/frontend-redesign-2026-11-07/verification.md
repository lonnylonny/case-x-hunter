# 前端改造与验证记录

日期：2026-11-07。依据上一阶段 UX 审查及本轮实施授权，使用 ui-ux-pro-max Skill。未提交 Git。

## 已完成

- 本地托管 Tabler Core 1.4.0 CSS 和 MIT 许可证，共享原生 UI 辅助脚本。保留三页 HTML、原生 JS 和既有 API，无框架迁移。
- 搜索输入区收紧，运行参数折叠；结果首先展示异常、产品/批次和历史原因，完成后定位结果标题。增加资料/配置重新读取。
- 候选支持 2–3 案比较：异常、产品批次、历史原因、措施、调查证据。只展示当前运行快照，不改检索顺序或下载 JSON。
- 来源字段可直接展开并聚焦对应结论、批次或检查证据；未找到字段时明确提示，不替代为新快照数据。
- 资料库分为“案例浏览 / 资料管理”；导入、模板、主数据及恢复仍可使用。默认浏览不再穿插维护表单。
- 详情优先展示历史结论与证据，提供分区导航；筛选、页码、每页条数、当前案例、快照身份、管理视图及本页排序通过 URL 恢复。失效快照不静默打开当前版本。
- 已应用筛选标签可逐项清除，修改条件后提示尚未应用。本页排序明确限定范围，不冒充全库排序。
- 编辑表单增加分区导航、必填标记、多选操作帮助；保留来源、冲突、输入保护、重复提交防护及固定保存操作栏。
- 统一导航、按钮、表格、状态、日期与部分枚举展示；补充跳至主内容与焦点定位，避免无条件平滑滚动。

## 测试

- `uv run --locked --no-sync pytest -q`：**1005 passed，12 skipped，169 subtests passed**。完整测试运行后仅补充了排序 URL 恢复及少量文案/样式，相关行为另以浏览器验证；未声称真实数据库验收。
- 页面、案例浏览、搜索 API 专项：78 passed（已包含在全量测试内）。
- `node scripts/web_case_browser_check.mjs`：27 项通过，含分页 URL 与刷新恢复。
- `node scripts/web_case_editing_check.mjs`：49 项通过。
- `node scripts/web_case_creation_check.mjs`：42 项通过。
- `node scripts/web_case_deletion_check.mjs`：40 项通过。
- `node scripts/web_dataset_browser_check.mjs`：22 项通过，覆盖上传/恢复、BFCache、过期请求、版本变化与失败结果不明。
- `node scripts/web_cache_browser_check.mjs`：10 项通过。
- 新增 `scripts/web_ux_playwright_check.js`：通过 Playwright MCP 的 `browser_run_code_unsafe(filename=...)` 执行，31 项通过，覆盖示例 A/B/C、无命中、断网、比较上限、证据定位、详情与筛选 URL、前进/后退、管理刷新、失效版本、375px 与移动新增表单。
- 页面及脚本语法检查、`git diff --check` 通过。

编辑/新增初次验证发现窄屏操作栏初始化定位问题，已修复后重跑通过。另修复管理视图刷新错误切回详情，以及 API 页面缓存检查中新增子导航的选择器影响。

浏览器写操作回归全部使用现有 HTTP 夹具；UX 检查使用上一阶段的只读 `serve_audit.py`，搜索复用现有检索、生成固定回放 Q005。没有真实模型调用，也没有修改真实数据。

## 503 独立记录

真实应用本机 8027：`/dataset`、`/runtime-settings` 仍返回 503，未诊断或更改数据库。静态页面可加载；资料未就绪时不提交检索，重新读取入口可用，输入保留。不能将合成环境截图视为真实数据库成功或模型可用。

截图：[真实服务故障](service-503.png)。本项不阻塞前端交付，数据库/配置恢复另行处理。

## 改造截图

- [搜索首页](home.png)
- [候选比较](comparison.png)
- [证据定位](evidence.png)
- [案例列表](library.png)
- [案例详情](detail.png)
- [资料管理](management.png)
- [窄屏首页](mobile-home.png)、[窄屏案例](mobile-library.png)、[窄屏管理](mobile-manage.png)、[窄屏设置](mobile-settings.png)、[窄屏新增](mobile-editor.png)

数据与来源仍遵循原有边界：历史结案原因不等于当前根因，related 不等于语义已验证；资料库完整详情与回答来源快照不能静默混用。未添加 Dashboard、图表或装饰动画。
