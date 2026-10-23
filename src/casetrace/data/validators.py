"""V1 数据的确定性校验；规则来源与覆盖范围见 docs/data 的实现清单。

validate_records：只检查字段。
validate_relations：先检查字段，再检查完整 Dataset 的 CR 关系、一致性和主数据引用。
validate_generation：字段守门后检查已实现的 GR，调用前应先通过 validate_relations。

返回 list[str]，空列表仅表示该入口负责的检查未发现错误，不代表语义或完整 GR 通过。
不转换、不修复输入；辅助函数的前置条件见各自注释。
"""

from datetime import date

from casetrace.data.constants import CHECKPOINT_TYPES, DETECTION_STAGES, GROUP_TYPES, RELEVANCE_VALUES
from casetrace.data.dataset_model import Case, CaseDetail, CaseGroup, EvidenceCheckpoint, Membership

# 1. 字段和对象内部检查


def check_required_text(
    value: object,
    *,
    field: str,
    location: str,
    rule: str,
) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return [
            f"{rule} | {location} | {field} | 必须是非空字符串"
        ]

    return []


def check_choice(
    value: object,
    *,
    allowed_values: tuple[str, ...],
    field: str,
    location: str,
    rule: str,
) -> list[str]:
    """单选检查包含文本检查；调用方无需先重复检查非空。"""
    errors = check_required_text(value, field=field, location=location, rule=rule)
    if errors:
        return errors
    if value not in allowed_values:
        return [
            f"{rule} | {location} | {field} | "
            f"必须是以下值之一：{' / '.join(allowed_values)}"
        ]
    return []


def check_text_list(
    value: object,
    *,
    field: str,
    location: str,
    rule: str,
) -> list[str]:
    """检查非空字符串列表；引用、枚举和重复规则由调用方决定。"""
    if not isinstance(value, list) or not value:
        return [f"{rule} | {location} | {field} | 必须是非空列表"]

    errors: list[str] = []
    for index, item in enumerate(value):
        errors.extend(check_required_text(
            item, field=f"{field}[{index}]", location=location, rule=rule,
        ))
    return errors


def check_optional_text(
    value: object, *, field: str, location: str, rule: str,
) -> list[str]:
    if value is not None and not isinstance(value, str):
        return [f"{rule} | {location} | {field} | 必须是字符串或 None"]
    return []


def check_required_text_fields(
    record: object, *, fields: dict[str, str], location: str,
) -> list[str]:
    """仅检查指定的必填文本；日期、数量、枚举等由各实体函数处理。"""
    errors: list[str] = []
    for field, rule in fields.items():
        errors.extend(check_required_text(
            getattr(record, field), field=field, location=location, rule=rule,
        ))
    return errors


def validate_detail_fields(
    detail: CaseDetail,
    *,
    location: str,
) -> list[str]:
    """检查 Detail 本地字段；不检查主数据引用、跨记录规则或文本语义。"""
    required_fields = {
        "detail_id": "结构 §2、§5",
        "case_id": "结构 §2、§5",
        "product_id": "CR-05",
        "customer_lot": "CR-07",
        "production_lot": "CR-06",
        "disposition": "CR-27",
    }

    errors = check_required_text_fields(detail, fields=required_fields, location=location)

    # bool 是 int 的子类，这里只接受 int；生成上限由 GR-06 单独检查。
    if type(detail.affected_qty) is not int or detail.affected_qty <= 0:
        errors.append(
            f"CR-23 | {location} | affected_qty | 必须是正整数"
        )

    date_fields = {
        "production_time": "CR-19",
        "detection_time": "CR-20",
    }
    dates_valid = True

    for field, rule in date_fields.items():
        # datetime 是 date 的子类；这里要求只有日期的 date 对象。
        if type(getattr(detail, field)) is not date:
            errors.append(
                f"{rule} | {location} | {field} | 必须是 date 日期"
            )
            dates_valid = False

    # 只依赖日期类型是否正确，不受文本或数量错误影响。
    if dates_valid and detail.detection_time < detail.production_time:
        errors.append(
            f"CR-21 | {location} | detection_time | 不能早于 production_time"
        )

    errors.extend(check_choice(
        detail.detection_stage, allowed_values=DETECTION_STAGES,
        field="detection_stage", location=location, rule="结构 §2",
    ))

    abnormal_errors = check_text_list(
        detail.abnormal_types, field="abnormal_types", location=location, rule="CR-14",
    )
    errors.extend(abnormal_errors)
    # 确认每项是字符串后才构造 set，避免非法元素引发 TypeError。
    if not abnormal_errors and len(set(detail.abnormal_types)) != len(detail.abnormal_types):
        errors.append(
            f"CR-11 | {location} | abnormal_types | 内部不得重复"
        )

    return errors


def validate_evidence_fields(evidence: EvidenceCheckpoint, *, location: str) -> list[str]:
    errors = check_required_text_fields(evidence, location=location, fields={
        "checkpoint_id": "结构 §3、§5", "case_id": "结构 §3、§5", "result": "CR-32",
    })
    errors.extend(check_choice(
        evidence.checkpoint_type, allowed_values=CHECKPOINT_TYPES,
        field="checkpoint_type", location=location, rule="CR-32",
    ))
    errors.extend(check_choice(
        evidence.relevance, allowed_values=RELEVANCE_VALUES,
        field="relevance", location=location, rule="CR-33",
    ))
    if evidence.checkpoint_type == "Other":
        errors.extend(check_required_text(
            evidence.custom_name, field="custom_name", location=location, rule="CR-34",
        ))
    else:
        errors.extend(check_optional_text(
            evidence.custom_name, field="custom_name", location=location, rule="结构 §3",
        ))
    return errors


def validate_case_fields(case: Case, *, location: str) -> list[str]:
    errors = check_required_text_fields(case, location=location, fields={
        "case_id": "结构 §2、§5", "abnormal_description": "CR-12",
        "root_cause": "CR-28", "corrective_action": "CR-29",
    })
    errors.extend(check_optional_text(
        case.investigation_others, field="investigation_others", location=location, rule="结构 §2",
    ))
    process_errors = check_text_list(
        case.abnormal_processes, field="abnormal_processes", location=location, rule="CR-48",
    )
    errors.extend(process_errors)
    if not process_errors and len(set(case.abnormal_processes)) != len(case.abnormal_processes):
        errors.append(f"CR-48 | {location} | abnormal_processes | 内部不得重复")
    return errors


def validate_group_fields(group: CaseGroup, *, location: str) -> list[str]:
    errors = check_required_text_fields(group, location=location, fields={
        "group_id": "结构 §4、§5", "description": "结构 §4、§5",
    })
    type_errors = check_text_list(group.group_type, field="group_type", location=location, rule="CR-42")
    errors.extend(type_errors)
    if not type_errors:
        for index, value in enumerate(group.group_type):
            errors.extend(check_choice(
                value, allowed_values=GROUP_TYPES,
                field=f"group_type[{index}]", location=location, rule="CR-42",
            ))

    # 检查实际包含关系；其他元素错误不应掩盖已选 other 的必填要求。
    if isinstance(group.group_type, list) and "other" in group.group_type:
        errors.extend(check_required_text(
            group.other_type_description, field="other_type_description", location=location, rule="CR-43",
        ))
    else:
        errors.extend(check_optional_text(
            group.other_type_description, field="other_type_description", location=location, rule="结构 §4",
        ))
    return errors


def validate_membership_fields(membership: Membership, *, location: str) -> list[str]:
    return check_required_text_fields(membership, location=location, fields={
        "group_id": "结构 §4、§5", "case_id": "结构 §4、§5", "association_reason": "CR-41",
    })


def validate_records(
    *, cases: list[Case], details: list[CaseDetail], evidences: list[EvidenceCheckpoint],
    groups: list[CaseGroup], memberships: list[Membership],
) -> list[str]:
    """只汇总字段错误；空列表不代表引用、跨记录或语义审查通过。"""
    errors: list[str] = []
    # 集中处理容器类型与错误定位，各实体函数只负责自己的字段。
    checks = (
        ("cases", cases, Case, "case_id", validate_case_fields),
        ("details", details, CaseDetail, "detail_id", validate_detail_fields),
        ("evidences", evidences, EvidenceCheckpoint, "checkpoint_id", validate_evidence_fields),
        ("groups", groups, CaseGroup, "group_id", validate_group_fields),
        ("memberships", memberships, Membership, None, validate_membership_fields),
    )
    for name, records, record_type, id_field, validator in checks:
        if not isinstance(records, list):
            errors.append(f"结构 | Dataset | {name} | 必须是列表")
            continue
        for index, record in enumerate(records):
            location = f"{record_type.__name__}[{index}]"
            if not isinstance(record, record_type):
                errors.append(f"结构 | {location} | record | 必须是 {record_type.__name__} 对象")
                continue
            record_id = getattr(record, id_field) if id_field is not None else None
            if isinstance(record_id, str) and record_id.strip():
                location = f"{record_type.__name__}[{record_id}]"
            errors.extend(validator(record, location=location))
    return errors


# 2. 完整 Dataset 的关系与一致性检查


def build_unique_index(
    records: list, *, id_field: str, object_name: str,
) -> tuple[dict[str, object], list[str]]:
    """输入须已通过字段检查；建立 ID 索引，遇到重复时保留第一条并报错。"""
    index: dict[str, object] = {}
    first_positions: dict[str, int] = {}
    errors: list[str] = []
    for position, record in enumerate(records):
        record_id = getattr(record, id_field)
        if record_id in index:
            errors.append(
                f"CR-03 | {object_name}[{position}] | {id_field} | "
                f"ID {record_id!r} 与 {object_name}[{first_positions[record_id]}] 重复"
            )
            continue
        index[record_id] = record
        first_positions[record_id] = position
    return index, errors


def check_case_children(
    cases_by_id: dict[str, Case],
    children_by_id: dict[str, CaseDetail] | dict[str, EvidenceCheckpoint],
    *, field: str, object_name: str, owner_rule: str, minimum_rule: str,
) -> list[str]:
    """输入须通过字段与 ID 唯一性检查；仅按子记录 case_id 检查归属和最少数量。"""
    errors: list[str] = []
    cases_with_children: set[str] = set()
    for child_id, child in children_by_id.items():
        if child.case_id not in cases_by_id:
            errors.append(
                f"{owner_rule} | {object_name}[{child_id}] | case_id | "
                f"引用的 Case[{child.case_id}] 不存在"
            )
        else:
            cases_with_children.add(child.case_id)

    for case_id in cases_by_id:
        if case_id not in cases_with_children:
            errors.append(
                f"{minimum_rule} | Case[{case_id}] | {field} | "
                f"至少需要一个实际归属本 Case 的 {object_name}"
            )
    return errors


def check_detail_consistency(details: list[CaseDetail]) -> list[str]:
    """输入须通过字段检查与 ID 唯一性检查；核对 CR-08～11。

    首条记录仅用于发现矛盾、定位重复，不据此决定哪条记录是真实正确的。
    """
    errors: list[str] = []
    first_by_lot: dict[str, CaseDetail] = {}
    first_by_event: dict[tuple, str] = {}
    fixed_fields = {
        "product_id": "CR-08",
        "customer_lot": "CR-09",
        "production_time": "CR-10",
    }

    for detail in details:
        first = first_by_lot.get(detail.production_lot)
        if first is None:
            first_by_lot[detail.production_lot] = detail
        else:
            for field, rule in fixed_fields.items():
                if getattr(detail, field) != getattr(first, field):
                    errors.append(
                        f"{rule} | CaseDetail[{detail.detail_id}] | {field} | "
                        f"同一 production_lot {detail.production_lot!r} "
                        f"与 CaseDetail[{first.detail_id}] 不一致："
                        f"{getattr(detail, field)!r} != {getattr(first, field)!r}"
                    )

        # frozenset 忽略异常顺序，且能作为元组键的一部分；不修改原列表。
        event_key = (
            detail.case_id,
            detail.production_lot,
            detail.detection_stage,
            detail.detection_time,
            frozenset(detail.abnormal_types),
        )
        if event_key in first_by_event:
            errors.append(
                f"CR-11 | CaseDetail[{detail.detail_id}] | event | "
                f"与 CaseDetail[{first_by_event[event_key]}] 的事件组合重复"
            )
        else:
            first_by_event[event_key] = detail.detail_id
    return errors


def check_reference_maps(
    *, product_customers: dict[str, str], product_routes: dict[str, str],
    failure_mode_routes: dict[str, set[str]],
) -> list[str]:
    """检查映射接口形状与 Product 覆盖一致性，不代替完整主数据导入校验。"""
    errors: list[str] = []
    for name, mapping in (
        ("product_customers", product_customers),
        ("product_routes", product_routes),
        ("failure_mode_routes", failure_mode_routes),
    ):
        if not isinstance(mapping, dict):
            errors.append(f"主数据 | Reference | {name} | 必须是字典")
            continue
        for key, value in mapping.items():
            location = f"{name}[{key!r}]"
            errors.extend(check_required_text(key, field="id", location=location, rule="主数据"))
            if name == "failure_mode_routes":
                if not isinstance(value, set):
                    errors.append(f"主数据 | {location} | routes | 必须是路线字符串集合 set")
                else:
                    for route in sorted(value, key=repr):
                        errors.extend(check_required_text(route, field="routes", location=location, rule="主数据"))
            else:
                errors.extend(check_required_text(value, field="value", location=location, rule="主数据"))
    if errors:
        return errors

    # 两张 Product 映射必须来自同一份完整主数据，不能只查到客户却查不到路线。
    for product_id in sorted(product_routes.keys() - product_customers.keys()):
        errors.append(f"主数据 | Product[{product_id}] | customer_id | 缺少客户映射")
    for product_id in sorted(product_customers.keys() - product_routes.keys()):
        errors.append(f"主数据 | Product[{product_id}] | package_route | 缺少路线映射")
    return errors


def check_detail_references(
    details: list[CaseDetail], *, product_customers: dict[str, str],
    product_routes: dict[str, str], failure_mode_routes: dict[str, set[str]],
) -> list[str]:
    """Detail 须通过字段与实体关系检查；映射由主数据导入层提供。

    输入是规范化映射，不解析 Excel 分隔符，不从 Product Family 推导路线。
    """
    errors = check_reference_maps(
        product_customers=product_customers, product_routes=product_routes,
        failure_mode_routes=failure_mode_routes,
    )
    if errors:
        errors.append("依赖 | Reference | relations | 主数据映射不完整或格式错误，主数据关联检查未执行")
        return errors

    customers_by_case: dict[str, set[str]] = {}
    for detail in details:
        location = f"CaseDetail[{detail.detail_id}]"
        product_exists = detail.product_id in product_routes
        if product_exists:
            customers_by_case.setdefault(detail.case_id, set()).add(product_customers[detail.product_id])
        else:
            errors.append(f"CR-05 | {location} | product_id | Product[{detail.product_id}] 不存在")

        # Product 无效不影响检查 Failure Mode 是否存在；但此时不能判断路线适配。
        for index, failure_mode_id in enumerate(detail.abnormal_types):
            field = f"abnormal_types[{index}]"
            if failure_mode_id not in failure_mode_routes:
                errors.append(f"CR-15 | {location} | {field} | Failure Mode[{failure_mode_id}] 不存在")
            elif product_exists:
                route = product_routes[detail.product_id]
                if route not in failure_mode_routes[failure_mode_id]:
                    errors.append(
                        f"CR-16 | {location} | {field} | Failure Mode[{failure_mode_id}] "
                        f"不适用于 Product[{detail.product_id}] 的路线 {route}"
                    )

    for case_id, customers in customers_by_case.items():
        if len(customers) > 1:
            errors.append(
                f"CR-04 | Case[{case_id}] | product_id | "
                f"Detail 引用的 Product 属于不同客户：{', '.join(sorted(customers))}"
            )
    return errors


def check_process_maps(
    *, processes: dict[str, str], route_processes: dict[str, set[str]],
    product_routes: dict[str, str],
) -> list[str]:
    """检查工序映射形状、引用与产品路线覆盖；product_routes 须已通过主数据检查。"""
    errors: list[str] = []
    for name, mapping in (("processes", processes), ("route_processes", route_processes)):
        if not isinstance(mapping, dict) or not mapping:
            errors.append(f"主数据 | Reference | {name} | 必须是非空字典")
            continue
        for key, value in mapping.items():
            location = f"{name}[{key!r}]"
            errors.extend(check_required_text(key, field="id", location=location, rule="主数据"))
            if name == "processes":
                errors.extend(check_required_text(value, field="process", location=location, rule="主数据"))
            elif not isinstance(value, set) or not value:
                errors.append(f"主数据 | {location} | processes | 必须是非空工序字符串集合 set")
            else:
                for process_id in sorted(value, key=repr):
                    errors.extend(check_required_text(
                        process_id, field="processes", location=location, rule="主数据",
                    ))
    if errors:
        return errors
    for route in sorted(set(product_routes.values()) - route_processes.keys()):
        errors.append(f"主数据 | Route[{route}] | processes | 缺少工序映射")
    for route, process_ids in route_processes.items():
        for process_id in sorted(process_ids - processes.keys()):
            errors.append(f"主数据 | Route[{route}] | processes | 引用了未知工序 {process_id}")
    return errors


def check_case_processes(
    cases: list[Case], details: list[CaseDetail], *, processes: dict[str, str],
    product_routes: dict[str, str], route_processes: dict[str, set[str]],
) -> list[str]:
    """字段、关系、产品引用和主数据映射须有效；仅核对 CR-49，不推断确认事实。"""
    allowed_by_case = {case.case_id: set() for case in cases}
    for detail in details:
        allowed_by_case[detail.case_id].update(route_processes[product_routes[detail.product_id]])
    errors: list[str] = []
    for case in cases:
        for index, process_id in enumerate(case.abnormal_processes):
            location = f"CR-49 | Case[{case.case_id}] | abnormal_processes[{index}]"
            if process_id not in processes:
                errors.append(f"{location} | Process[{process_id}] 不存在")
            elif process_id not in allowed_by_case[case.case_id]:
                errors.append(f"{location} | Process[{process_id}] 不属于本 Case 产品路线的工序并集")
    return errors


def check_confirmed_evidence(
    evidences: list[EvidenceCheckpoint], closure_statuses: dict[str, str | None],
) -> list[str]:
    """CR-35 的机械门槛：结案状态由显式来源提供，不从根因文本猜测。

    仅检查 confirmed 至少一个 related；证据是否支持结论仍需语义审阅。
    """
    supported = {item.case_id for item in evidences if item.relevance == "related"}
    return [
        f"CR-35 | Case[{case_id}] | evidences | confirmed 草稿至少需要一个 related Evidence"
        for case_id, status in closure_statuses.items()
        if status == "confirmed" and case_id not in supported
    ]


def validate_relations(
    *, cases: list[Case], details: list[CaseDetail], evidences: list[EvidenceCheckpoint],
    groups: list[CaseGroup], memberships: list[Membership],
    product_customers: dict[str, str], product_routes: dict[str, str],
    failure_mode_routes: dict[str, set[str]],
    processes: dict[str, str], route_processes: dict[str, set[str]],
) -> list[str]:
    """检查完整 Dataset 的字段、实体关系、Detail 一致性及主数据关联。

    五份主数据映射必传；映射格式检查不代替完整主数据导入校验。当前不覆盖 GR 或语义。
    不可用单个 Case 的子集判断跨 Case 的 Group 成员数或批号一致性。
    """
    errors = validate_records(
        cases=cases, details=details, evidences=evidences,
        groups=groups, memberships=memberships,
    )
    if errors:
        errors.append("依赖 | Dataset | relations | 字段检查未通过，关系检查未执行")
        return errors

    indexes = {}
    for name, records, id_field, object_name in (
        ("cases", cases, "case_id", "Case"),
        ("details", details, "detail_id", "CaseDetail"),
        ("evidences", evidences, "checkpoint_id", "EvidenceCheckpoint"),
        ("groups", groups, "group_id", "CaseGroup"),
    ):
        index, index_errors = build_unique_index(records, id_field=id_field, object_name=object_name)
        indexes[name] = index
        errors.extend(index_errors)
    if errors:
        errors.append("依赖 | Dataset | relations | 实体 ID 不唯一，后续关系检查未执行")
        return errors

    cases_by_id = indexes["cases"]
    groups_by_id = indexes["groups"]
    errors.extend(check_case_children(
        cases_by_id, indexes["details"], field="details",
        object_name="CaseDetail", owner_rule="CR-02", minimum_rule="CR-01",
    ))
    errors.extend(check_case_children(
        cases_by_id, indexes["evidences"], field="evidences",
        object_name="EvidenceCheckpoint", owner_rule="CR-31", minimum_rule="CR-30",
    ))

    seen_memberships: set[tuple[str, str]] = set()
    members_by_group = {group_id: set() for group_id in groups_by_id}
    for position, membership in enumerate(memberships):
        location = f"Membership[{position}]"
        key = (membership.group_id, membership.case_id)
        if key in seen_memberships:
            errors.append(f"CR-40 | {location} | (group_id, case_id) | 组合 {key!r} 重复")
        seen_memberships.add(key)

        group_exists = membership.group_id in groups_by_id
        case_exists = membership.case_id in cases_by_id
        if not group_exists:
            errors.append(
                f"CR-39 | {location} | group_id | 引用的 CaseGroup[{membership.group_id}] 不存在"
            )
        if not case_exists:
            errors.append(
                f"CR-39 | {location} | case_id | 引用的 Case[{membership.case_id}] 不存在"
            )
        # 只计入引用有效的不同 Case；重复记录不能凑足最少成员数。
        if group_exists and case_exists:
            members_by_group[membership.group_id].add(membership.case_id)

    for group_id, member_ids in members_by_group.items():
        if len(member_ids) < 2:
            errors.append(
                f"CR-38 | CaseGroup[{group_id}] | memberships | "
                f"至少需要两个不同的有效 Case，当前为 {len(member_ids)} 个"
            )
    # 此处依赖字段和 ID 合法，但不依赖所有引用都合法；可以继续收集独立错误。
    errors.extend(check_detail_consistency(details))
    if errors:
        errors.append("依赖 | Dataset | references | Dataset 关系或一致性检查未通过，主数据关联检查未执行")
        return errors
    errors.extend(check_detail_references(
        details, product_customers=product_customers, product_routes=product_routes,
        failure_mode_routes=failure_mode_routes,
    ))
    if errors:
        errors.append("依赖 | Dataset | abnormal_processes | 主数据关联检查未通过，异常工序与同站点分组检查未执行")
        return errors
    errors.extend(check_process_maps(
        processes=processes, route_processes=route_processes, product_routes=product_routes,
    ))
    if errors:
        errors.append("依赖 | Reference | abnormal_processes | 工序映射无效，异常工序与同站点分组检查未执行")
        return errors
    errors.extend(check_case_processes(
        cases, details, processes=processes, product_routes=product_routes,
        route_processes=route_processes,
    ))
    process_groups = [group for group in groups if "same_abnormal_process" in group.group_type]
    if errors:
        if process_groups:
            errors.append("依赖 | Dataset | groups | 异常工序检查未通过，同站点分组检查未执行")
        return errors
    for group in process_groups:
        member_processes = [
            set(cases_by_id[case_id].abnormal_processes)
            for case_id in sorted(members_by_group[group.group_id])
        ]
        # 成员数与引用已经通过检查；必须全组共有，不能以两两交集替代。
        if not set.intersection(*member_processes):
            errors.append(
                f"CR-51 | CaseGroup[{group.group_id}] | memberships | 全组成员须至少共享一个已确认异常工序"
            )
    return errors


# 3. GR 生成限制的确定性检查


def check_case_detail_count(cases: list[Case], details: list[CaseDetail]) -> list[str]:
    """GR-01：每个 Case 的 Detail 数量上限为 3。

    下限 1 由 CR-01 负责，不在这里重复实现。输入须已通过字段检查；悬空 case_id 属于
    CR 范围，不为它造出一个“0 个 Detail 的 Case”。
    """
    counts: dict[str, int] = {case.case_id: 0 for case in cases}
    for detail in details:
        if detail.case_id in counts:
            counts[detail.case_id] += 1
    return [
        f"GR-01 | Case[{case_id}] | details | 每 Case 最多 3 个 Detail，当前 {count} 个"
        for case_id, count in counts.items()
        if count > 3
    ]


def check_detail_generation_limits(details: list[CaseDetail]) -> list[str]:
    """GR-02 的异常数量上限和 GR-06 的数量上限，逐 Detail 检查。

    GR-02 的“约 90% 单异常、10% 双异常”没有容差，只限制采样范围不设比例门槛；
    下限（CR-14 至少一个异常、CR-23 正整数）由 CR 入口负责。GR-06 不把同批
    Detail 的数量求和，重叠实物不能累加成批次总量。
    """
    errors: list[str] = []
    for detail in details:
        location = f"CaseDetail[{detail.detail_id}]"
        if len(detail.abnormal_types) > 2:
            errors.append(
                f"GR-02 | {location} | abnormal_types | "
                f"生成只采样单异常或双异常，当前 {len(detail.abnormal_types)} 个"
            )
        if detail.affected_qty > 5000:
            errors.append(
                f"GR-06 | {location} | affected_qty | 每 Detail 上限 5000 ea，当前 {detail.affected_qty}"
            )
    return errors


def check_production_lot_reuse(details: list[CaseDetail]) -> list[str]:
    """GR-04：整个 Dataset 只有 1～2 个被复用的 production_lot，各自至少出现两次。

    只统计出现次数，不限制单个复用批号的出现次数上限，也不判断同批事件是否覆盖相同实物。
    需要整份 Dataset 参与统计，单个 Case 的子集不足以判断。
    """
    counts: dict[str, int] = {}
    for detail in details:
        counts[detail.production_lot] = counts.get(detail.production_lot, 0) + 1
    reused_lots = sorted(lot for lot, count in counts.items() if count >= 2)
    if 1 <= len(reused_lots) <= 2:
        return []

    listed_lots = f"：{', '.join(reused_lots)}" if reused_lots else ""
    return [
        "GR-04 | Dataset | production_lot | "
        f"复用批号（出现至少两次）应为 1～2 个，当前 {len(reused_lots)} 个{listed_lots}"
    ]


def validate_generation(
    *, cases: list[Case], details: list[CaseDetail], evidences: list[EvidenceCheckpoint],
    groups: list[CaseGroup], memberships: list[Membership],
) -> list[str]:
    """在完整生成 Dataset 上执行可确定的 GR 检查：当前为 GR-01、02、04、06。

    期望输入已通过 validate_relations；本入口不重复关系、一致性和主数据检查，只保留字段
    守门，使 GR 检查不会因字段类型错误中断。需要生成上下文（GR-03、07～10）或分布统计
    （GR-02 比例、GR-05 时间间隔）的要求不在这里判断，返回空列表不代表这些部分通过。
    """
    errors = validate_records(
        cases=cases, details=details, evidences=evidences,
        groups=groups, memberships=memberships,
    )
    if errors:
        errors.append("依赖 | Dataset | generation | 字段检查未通过，GR 检查未执行")
        return errors

    errors.extend(check_case_detail_count(cases, details))
    errors.extend(check_detail_generation_limits(details))
    errors.extend(check_production_lot_reuse(details))
    return errors
