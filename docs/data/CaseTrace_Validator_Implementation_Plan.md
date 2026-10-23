# Validator — Data Foundation v1 覆盖说明

Data Foundation v1 已完成并冻结，现有异常站点检查已接入。冻结及修复边界见 [业务与评估约定](../design/behavior-contracts.md#1-data-foundation-v1-冻结)。本文件记录覆盖限制，不新增规则或补齐待办；业务权威来源为[结构](CaseTrace_Data_Structure_V2_No_Scenario.md)、[CR](CaseTrace_Case_Constraint_Rules_Frozen.md)、[GR](CaseTrace_Case_Generation_Rules_V1.md)。

## 当前职责与调用边界

实现见 [validators.py](../../src/casetrace/data/validators.py)；主数据读取见 [reference.py](../../src/casetrace/data/reference.py)。

| 入口 | 覆盖 |
|---|---|
| `validate_records` | 五类实体的类型、必填、枚举与对象内部关系 |
| `validate_relations` | 字段守门、Dataset 唯一性、引用/归属、Case 子记录与 Group 最少数量、Lot 一致性、事件去重、产品/客户/异常路线、Case 异常工序路线并集与全组公共工序 |
| `validate_generation` | 字段守门及 GR-01 Detail 上限、GR-02 异常数量上限、GR-04 复用批号、GR-06 数量上限 |
| `check_confirmed_evidence` | 复用来源元数据中的显式 confirmed/NDF 判断，对 confirmed 检查至少一个 related Evidence；合成与上传共用，不从自由文本猜状态，也不证明语义支持 |

先通过 `validate_relations` 再调用 `validate_generation`；后者不重复关系与主数据检查，不能替代 CR 入口。错误返回 `list[str]`，标识规则、对象、字段与原因；不自动修复输入，前置错误导致跳过时明确报告。空列表只说明该入口负责的检查未发现问题。

关系检查须使用完整 Dataset 和同一主数据构造的映射；单 Case 子集不能证明跨 Case 唯一性、批号一致性或 Group 成员数量。Case 子记录关系以子记录的 case_id 为唯一来源，不维护冗余 ID 列表。

## 自动检查不能证明的事项

| 范围 | 覆盖限制与必要处理 |
|---|---|
| 主数据 | 当前读取 demo 所需 Product、Customer、Route、Failure Mode、工序及路线工序子集；完整 BOM、设备、工序顺序等导入检查未覆盖 |
| 映射校验 | 只检查接口形状、引用和覆盖；不能发现构造字典前已被覆盖的重复记录，也不替代完整主数据导入校验 |
| 文本语义 | CR-13、26、36、44、50 需语义审阅；CR-27～29、34、43 的非空检查不证明含义合格；CR-37 是复核提示，不是硬错误 |
| CR-35 | 先取得明确 confirmed/NDF 结案判断，再检查 confirmed 的 related Evidence 及语义支持；不通过自由文本搜“NDF”推定状态，不新增永久字段代替审阅 |
| 生成限制 | GR-02 比例、GR-05 间隔分布、GR-03、07～10 未完整自动检查；需要生成上下文、候选来源和语义审阅 |
| 来源核对 | demo 的原候选与 related Evidence 机械核对不证明改写后的原因—证据—措施逻辑，不是通用文本验收器 |
| 实际调查事实 | 结构 §6 的设备、材料/晶圆批号和参数使用生成上下文与 Evidence.result 核对；异常站点引用合法不代表调查事实已确认 |

## 保留的设计边界

- CR 与 GR 分开；date 不接受 datetime，严格整数排除 bool，ID 保持文本及前导零。原始文件解析不由 Validator 自动转换。
- 不把未规定的阈值变成硬规则：“少量”、约 90%/10%、通常半年内仍按既有生成口径处理；不凭 GR-06 把同批事件数量相加当作去重数量。
- `group_type` 的内部重复未被 CR-42 明确禁止，不擅自增加拒绝条件；混选 same_abnormal_process 仍须满足全组交集。
- 工序范围使用路线并集，包含 incoming / optional / auxiliary，不以 sequence_no 过滤；Failure Mode 的 applicable_process 仅为候选知识。缺异常工序不能静默填充。
- 冻结不认证未实施检查。完整导入、通用生成器与语义 Validator 继续延期；扩充小批样例时只补当前可信度所需事实、来源和人工审阅，不重开基础工程。
