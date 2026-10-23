# 固定 CSV 上传格式 v1

从搜索页进入「资料设置」，下载空白模板或完整合成示例。上传将整体替换当前运行案例，成功后新查询只检索当前数据；失败保留原数据。原始样例、主数据文件、评估集和实验记录不被修改。

## 填写

CSV 使用 UTF-8，可带 BOM；所有模板列必须存在且不能重复，不接受额外列，允许调整列顺序。引号、逗号和换行按标准 CSV 转义。日期为 `YYYY-MM-DD`；列表以 `;` 分隔。ID、批号按文本保存，保留前导零。最多 5 MiB、500 个 Case、10000 条记录。

每行 `record_type` 为 `case`、`detail` 或 `evidence`。每条记录填写自身字段，其余类型的列留空；三种记录不必按固定顺序排列。Case 至少有一个 Detail 和一个 Evidence；调查项目只挂 Case，不关联 Detail，数量无需相同。CaseGroup / Membership 不在上传格式中。

| 类型 | 必填字段 | 可选或条件必填 |
|---|---|---|
| case | case_id、abnormal_description、root_cause、corrective_action、abnormal_processes、closure_status | investigation_others 可空 |
| detail | detail_id、case_id、product_id、customer_lot、production_lot、production_time、detection_stage、detection_time、abnormal_types、affected_qty、disposition | 无 |
| evidence | checkpoint_id、case_id、checkpoint_type、result、relevance | checkpoint_type=Other 时 custom_name 必填，其余可空 |
| 所有行 | record_type、source_document_id、source_document_name、source_location | 无 |

各类型主键分别在文件内唯一，子记录 `case_id` 必须引用文件内 Case。与被替换资料使用相同 ID 是允许的，不执行增量合并或跨上传关联。

Case 行的 `closure_status` 沿用已有来源元数据，明确填写 `confirmed` 或 `NDF`，其余行留空；不向冻结实体增加字段，不从根因自由文本猜状态。`confirmed` 至少有一个 `related` Evidence（复用既有 CR-35 机械门槛）；NDF 无此标签要求，但仍须有具体结案结论、措施和已确认异常站点。结案声明不等于通过人工审阅，上传仍是 draft。

来源分别填写报告编号、完整报告名称和页码／章节／表格行号／调查项编号，不用“同上”“见报告”“未知”等占位。系统另存原上传文件名与物理起始行号（带换行的单元格会影响后续物理行号）。结构检查不能证明来源真实或位置确实支持结论，需人工审阅。

## 主数据与检查

设置页展开合法主数据，查看 `product_id` 的客户与路线、`failure_mode_id` 的适用路线、`process_id` 的路线工序映射及全部枚举。网页复用当前实例的主数据；上传不能新增或改写主数据。

- 使用现有 `validate_relations` 的字段、关系、主数据、批号、事件唯一性和工序适配检查。异常工序须已确认，不从发现阶段或 Failure Mode 的候选知识推断。
- 原合成资料和上传资料共用 `check_confirmed_evidence` 的 CR-35 门槛；标签存在不证明调查结论受到语义支持。
- 不施加合成数据的采样规则，例如必须出现复用批号、每案最多三条 Detail、处置数量上限等 GR。原合成入口继续保留这些检查。
- 根因、措施不要求来自候选库；保留结案原文与独立来源。现有 CR 校验未覆盖的调查语义不因导入通过而视为已验证。
- 错误在可定位时显示物理行号、列名及原因；跨记录错误可能同时涉及多行，文件级错误可能没有具体行号。任何错误均不部分导入。

## 身份与时点

上传填写名称、整份资料完整可用日期、可用性依据，并确认全部案例在该日期前已结案且所有内容完整可用。不能把发现时间当结案时间；系统只能检查发现日期不晚于登记时点，不能核实声明真实性。合成资料须明确说明是人为设定的可用性快照。

完整示例为 1 Case、2 Detail、3 Evidence，全部为合成内容；可用日期可登记 `2026-09-15`，依据示例为「人工合成快照：SYN-REPORT-001 的全部案例被设定为在 2026-09-15 前已结案且内容完整可用，不代表真实结案日期」。

每次成功上传取得独立 `upload-*` 身份，保留原始 CSV 字节 SHA-256、主数据身份、内容摘要和更新时间。上传审阅状态固定为 `draft_pending_human_review`，不继承 Development / Locked Test 标签、Query 或旧评估指标。当前数据保存在 PostgreSQL；不要求重新读取原 CSV 才能重启。

搜索页自动使用当前快照的登记日期，不支持任意历史日期过滤。已开始的回答沿原快照完成，其下载记录和来源仍属于原快照；上传不取消它，也不自动重新调用模型。新请求与上传交错时不增加额外的产品排队策略，多表读取和写入通过数据库事务及元数据锁保持一致。
