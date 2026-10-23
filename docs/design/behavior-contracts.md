# CaseTrace 业务与评估约定

本文件定义现行业务行为与评估边界；字段与约束以数据定义为准，运行入口见 [README](../../README.md)。开发排期、教学及验收记录在本地维护。

## 1. Data Foundation v1 冻结

Schema、dataclass、CR / GR、Validator 和 reference data 已完成并冻结。冻结不代表全部语义检查已实现或通过，也不表示 PostgreSQL 已建库。

| 权威内容 | 位置 |
|---|---|
| 字段、关系与 M5 数据库映射 | [数据结构](../data/CaseTrace_Data_Structure_V2_No_Scenario.md) |
| 业务约束与生成边界 | [CR](../data/CaseTrace_Case_Constraint_Rules_Frozen.md)、[GR](../data/CaseTrace_Case_Generation_Rules_V1.md) |
| 校验覆盖与人工审阅缺口 | [Validator 说明](../data/CaseTrace_Validator_Implementation_Plan.md) |
| 主数据与生成来源 | [reference data](../../data/reference/)、[主数据决定](../data/CaseTrace_Structure_Logic_Audit_Confirmation.md) |

只修复影响运行、评估可信度、数据泄漏或来源追溯的问题。业务规则变更须由用户确认并更新其权威来源；已知覆盖缺口不自动形成待办。现行冻结模型已包含必填异常站点及同站点分组规则（CR-48～51），调查事实仍需语义审阅。

Query、qrels、split 与实验记录独立于历史 Case；不恢复 Scenario 实体，不从 CaseGroup 推导标签或数据划分。持久化按冻结模型实现，不重开数据设计。

## 2. Retrieval / Ground Truth / Evaluation 约定

检索问题：**仅根据当前 Incident 当时已知的信息，哪些已结案历史 Case 值得查看？** 术语见 [CONTEXT.md](../../CONTEXT.md)。

### 相关性与阅读优先级

- **统一规则（2026-10-03 用户确认，覆盖旧背景充分条件）：** 是否 Relevant / 采用，依据当前已知异常与历史案例的调查参考价值。理由须由双侧已知现象及历史记录支持，不要求措辞逐字相同或根因相同。
- 同批次、同产品、同产品族、同客户等仅为背景补充/辅助信息，均不单独决定相关性、不强制采用或另列背景案例；背景不同也不能单独否定异常参考价值。“类似产品”仍指同 Product Family，仅同 Package Route 不等于类似产品。不新增硬编码权重或必选条件。
- **采用后再说明背景关系。** 明确标注同批次，分别写生产批和客户批的匹配字段/值，不凭一项相同推定另一项也相同；当前未给批号不从历史反填。产品、产品族、客户等已知背景关系在已采用 Case 中简明说明，不能替代异常参考理由。未采用 Case 不因这些关系相同被判漏选，也不单独摘出。
- 不新增“同异常类别”判定层，共享 failure_mode_id 不自动等于同异常。仅同异常站点也不足以单独 Relevant，须结合已知异常或背景；不得从历史记录反填当前未知工序。
- Repeat / Recurrence 指同产品且异常相同或高度相似；Incident / Context Linkage 描述产品、批次等背景联系；Technical Analog 描述异常的技术参考关系。这些名称不自动生成相关性标签。
- 仅有通用调查方法不自动 Relevant；CaseGroup、相同 Root Cause、Evidence 的 `related` 也不能自动生成 qrels。异常参考价值须按当前已知信息说明，不新增“共享代码即同异常”的捷径。
- 阅读期望为优先完全相同异常；其它排序变化须有实验依据，不转换为未经确认的分级 qrels 或权重。旧 Q001×C002 的同产品/同批次正例、Q001×C004 与 Q003×C005 的同族依据不能继续作为新规则裁决，须复核；不自动改标签。C004 不因与 C001 同站点而成为完全相同异常。
- dev-v2 三条 Query 未提供批号，不以历史 Case 间同批推断 Query 同批；同批次只作采用后的背景标注，不作为独立纳入条件。
- Query 必须写明当时已知的客户、产品、生产批号与客户批号（2026-09-25 用户决定，自 dev-v3 起执行）：取值与主数据及语料一致——客户由产品经 `customer_product_map` 推导，复用的批号须与语料中该批的产品、投批时间、客户批一致，新批号不得与既有 Case 重复；批号为模拟值，主数据无批号表。dev-v3 的五条 Query 已按此补足，见 [dev-v3 版本说明](../../data/evaluation/dev-v3/README.md)。

### Ground Truth、时点与隔离

- Ground Truth 单位是 Query × Historical Case；Agent 起草标签、理由和证据，**用户最终确认**。未确认或 Ambiguous 保持 draft；未标注不能当 Not Relevant，正式 evaluator 拒绝不完整或未确认输入。
- 2026-10-03 规则改变后，既有 qrels 与实验按旧口径保留；其 `human_confirmed` 不等于新口径确认，不自动重标。新 GT 仍由用户确认；若未来发布新口径指标，再版本化并同基准重评。该复核不作为学习展示或工程推进的前置任务，不用旧指标冒充新结论。
- Locked Test v1 已记录用户逐对裁决 `LQ003×LC002=1`，采用同产品/同生产批/同客户批背景，与上述通用相关性规则有差异；按实际裁决保留，见 [确认记录](../../data/evaluation/locked-test-v1/README.md#用户裁决记录2026-10-08)。该记录不推广为全局自动标签规则，也不将本集合描述为完全依照新口径的质量结论。
- [dev-v2](../../data/evaluation/dev-v2/README.md) 的 6 Case × 3 Query、18 对二值标签已于 2026-09-19 最终确认，无需再次确认。qrels 确认不改变源 Case 的 draft 审阅状态；新版本的新增或语义改变配对另行确认。
- Query 只含当时已知事实；只检索在 Query 时点前已结案且完整可用的历史内容。dev-v2 的六案完整内容被用户设定为在 2026-09-15 前可用；这是模拟快照约定，不能由 detection_time 推导，也不表示已实现通用结案/可用时间过滤。扩充数据必须记录自己的快照依据。
- 索引限于允许的历史内容与主数据背景，排除 qrels、标注理由、生成审阅信息和候选知识库全文。
- Development 用于调试、模型选择与错误分析。Locked Test 按来源 / 近重复家族隔离；已参与讨论和调参的样例不能再充当 Locked Test。固定模型与参数后执行预先约定的最终对比，不能持续用 Locked Test 调参。

### 指标与可比较性

采用 Recall-first，主 K=4，同时报告 K=1、3 的 Recall / Precision / 二值 nDCG 与 MRR@4。保留全部实际返回排名，不补分或补名次。runner 在计分前校验完整标注、排名 ID 有效且唯一。

| 口径 | 固定规则 |
|---|---|
| K | 正整数，拒绝 bool 与小数 |
| Recall@K | 前 K 命中数 / 全部正例数 |
| Precision@K | 前 K 命中数 / K；返回不足 K 不缩分母 |
| RR@4 / MRR@4 | 前四条首个相关结果名次 r 的 1/r，再跨 Query 等权平均 |
| nDCG@K | 二值 gain，以 log2(rank + 1) 折扣；IDCG 使用全部正例与 K |
| 无正例 Query | Recall / RR / nDCG 为 None 并排除对应均值；Precision 为 0 并参与均值；单列原因 |
| 有正例但未命中（含空返回） | 各指标为 0 |
| 汇总 | Query 等权，保存各指标参与数与排除原因；无参与项为 null |

二值指标不衡量 Relevant 内部的阅读顺序。四类方法必须共享 Corpus / Query / qrels 与指标版本；任一变化后在同一新版重跑，不能跨版本声称提升。保存逐 Query 排名、配置、模型/数据/源码/依赖版本及耗时；计时注明边界、硬件、冷启动与缓存条件。复现约定与产物见 [results](../../results/README.md)。

### Grounded Answer 边界

只用检索到的历史内容支持事实陈述；相关理由对应 Query 已知事实与历史原文，关键原因和检查结果保留 Case / Evidence 来源。缺证据时明确不足，区分历史结论与当前未知。先复用结构化字段；检索质量与回答忠实度分开验证。

上传 Case 的 `closure_status` 沿用来源元数据，明确登记 confirmed/NDF，与合成入口共用 CR-35 的 confirmed 至少一项 related Evidence 检查；这不是语义支持的证明，也不升级 draft。

## 3. Grounded Answer 核心约定

1. **共用核心：** CLI/API 只适配输入、输出及错误；复用现有检索、上下文、生成和引用校验，不复制业务逻辑。数据库按 §1 冻结模型完整存取，保留 ID、关系、原文、来源和 review 状态；固定输入迁移前后排名、上下文及来源一致。
2. **保持原始 Query：** R3 过滤只影响检索词，生成器仍收到完整 Query（含否定、客户、产品与批号）与证据；`known_at` 用于快照检查。Query 提及产品的主数据查表只补名称/产品族/标准路线，不做 Incident 字段抽取或反填当前未知。
3. **快照与隔离：** 文件路线保留 `dev-v3-2026-09-15` 的已记录可用性快照，文件身份同时绑定语料与主数据。2026-10-23 用户确认：网页可用唯一 CSV 模板整体替换当前运行数据；每案支持多个 Detail、多个 Case 级调查项目。上传登记独立快照、原始 CSV 字节摘要、主数据身份和整份资料在登记日期前已结案且完整可用的声明依据，不用 detection_time 冒充结案时间。数据库回答使用该登记时点，仍不提供通用历史时点过滤。上传按适用 CR 校验，不执行合成采样 GR 和合成候选库来源核对；原合成资料入口保持原检查。每条上传记录必填具体报告编号、名称和位置，保留文件名与 CSV 行号；这些来源可进入证据上下文，审阅元数据和 qrels/理由不进入。上传统一 draft，不继承旧指标。Development / Locked Test、原始样例及实验记录继续隔离。替换失败全部回滚，重启保留当前数据，已取得快照的回答不中断。
4. **输出与来源：** 输出相关历史案例、双侧相关依据、历史原因/检查/改善措施、来源和具体缺口。保留 Case、Detail、checkpoint 与字段原文、否定和不确定性；采用依据遵守 §2，不由背景关系强制纳入。历史结论不成为当前 Root Cause 或当前已验证措施。
5. **状态与记录：** 成功、空命中、模型失败、格式失败、引用失败分开；空命中不调用模型，失败不包装成成功。记录原始输入、排名、上下文、实际发送消息、模型/提示词/源码/数据身份及可用的响应、耗时和用量；凭据不入产物，批量复跑不覆盖旧证据。
6. **生成与质量边界：** 当前直接用 OpenAI 兼容 SDK 接 DeepSeek `deepseek-flash` 非思考 JSON Output，提示词 `grounded_answer_v10`；单次生成，无重试、路由或 Agent 工作流。引用守卫只证明可定位，语义支持另行审阅；软件回归不替代 AI 评估。模型别名不等于不可变版本，不承诺逐字复现。接受既有学习展示限制，不在存储/API 迁移中调模型或重新选型。
