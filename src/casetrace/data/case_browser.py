"""案例资料库的只读投影；不检索、不生成、不改变原始实体。"""

from collections import defaultdict
from dataclasses import asdict

from casetrace.data.reference import ReferenceData


def product_info(reference: ReferenceData, product_id: str) -> dict:
    product = reference.products[product_id]
    customer_id = reference.product_customers[product_id]
    return {
        **product, 'customer_id': customer_id,
        'customer_name': reference.customer_names[customer_id],
        'product_family': reference.product_families[product['product_family_id']],
    }


def _processes(reference: ReferenceData, ids: list[str]) -> list[dict]:
    return [{'process_id': key, 'process': reference.processes[key]} for key in ids]


def browse_cases(
    records: dict, reference: ReferenceData, *, keyword: str = '', product_id: str = '',
    customer_id: str = '', abnormal_process_id: str = '', detection_stage: str = '',
    page: int = 1, page_size: int = 20,
) -> dict:
    details = defaultdict(list)
    evidence_counts = defaultdict(int)
    for detail in records['details']:
        details[detail.case_id].append(detail)
    for evidence in records['evidences']:
        evidence_counts[evidence.case_id] += 1
    items = []
    keyword = keyword.strip().casefold()
    for case in records['cases']:
        children = details[case.case_id]
        if abnormal_process_id and abnormal_process_id not in case.abnormal_processes:
            continue
        if (product_id or customer_id or detection_stage) and not any(
            (not product_id or item.product_id == product_id)
            and (not customer_id or reference.product_customers[item.product_id] == customer_id)
            and (not detection_stage or item.detection_stage == detection_stage)
            for item in children
        ):
            continue
        if keyword and not any(keyword in value.casefold() for value in (
            case.case_id, case.abnormal_description, case.root_cause,
            *(value for item in children for value in (item.production_lot, item.customer_lot)),
        )):
            continue
        dates = [item.detection_time for item in children]
        items.append({
            'case_id': case.case_id, 'abnormal_description': case.abnormal_description,
            'products': [product_info(reference, key) for key in dict.fromkeys(
                item.product_id for item in children)],
            'abnormal_processes': _processes(reference, case.abnormal_processes),
            'detection_start': min(dates) if dates else None,
            'detection_end': max(dates) if dates else None,
            'detail_count': len(children), 'evidence_count': evidence_counts[case.case_id],
        })
    offset = (page - 1) * page_size
    return {'total_count': len(records['cases']), 'matched_count': len(items),
            'page': page, 'page_size': page_size, 'items': items[offset:offset + page_size]}


def case_detail(records: dict, reference: ReferenceData, sources: dict, case_id: str) -> dict | None:
    case = next((item for item in records['cases'] if item.case_id == case_id), None)
    if case is None:
        return None
    groups = {item.group_id: item for item in records['groups']}
    return {
        'case': {**asdict(case), 'processes': _processes(reference, case.abnormal_processes)},
        'details': [
            {**asdict(item), 'product': product_info(reference, item.product_id),
             'failure_modes': [{'failure_mode_id': key,
                                'failure_mode': reference.failure_modes[key]['failure_mode']}
                               for key in item.abnormal_types]}
            for item in records['details'] if item.case_id == case_id
        ],
        'evidences': [asdict(item) for item in records['evidences'] if item.case_id == case_id],
        'groups': [
            {**asdict(groups[link.group_id]), 'association_reason': link.association_reason,
             'member_ids': [member.case_id for member in records['memberships']
                            if member.group_id == link.group_id]}
            for link in records['memberships'] if link.case_id == case_id
        ],
        'source': sources[case_id],
    }
