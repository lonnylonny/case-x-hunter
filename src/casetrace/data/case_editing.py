"""整案维护输入与完整候选集校验；不连接数据库，不套用合成采样规则。"""

from copy import deepcopy
from datetime import date, datetime, timezone
import re
from typing import Literal, TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from casetrace.data.csv_import import MAX_CASES, MAX_RECORDS, PLACEHOLDERS
from casetrace.data.dataset_model import Case, CaseDetail, EvidenceCheckpoint
from casetrace.data.validators import check_confirmed_evidence, validate_relations

if TYPE_CHECKING:
    from casetrace.storage.snapshot import LoadedSnapshot


def specific_text(value: str) -> str:
    if not value.strip() or value.strip().casefold() in PLACEHOLDERS or '\x00' in value:
        raise ValueError('必须填写具体内容，不接受空白或占位')
    return value


def date_value(value):
    if type(value) is date or isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return value
    raise ValueError('必须为 YYYY-MM-DD 日期')


class EditInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('*')
    @classmethod
    def reject_nul_text(cls, value):
        texts = value if isinstance(value, list) else [value]
        if any(isinstance(item, str) and '\x00' in item for item in texts):
            raise ValueError('文本不能包含 NUL 字符')
        return value


class RecordOrigin(EditInput):
    kind: Literal['report', 'synthetic']
    source_location: str = Field(min_length=1, max_length=2000)
    source_document_id: str | None = Field(default=None, max_length=2000)
    source_document_name: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def require_origin(self):
        specific_text(self.source_location)
        if self.kind == 'report':
            for value in (self.source_document_id, self.source_document_name):
                if value is None:
                    raise ValueError('报告来源需要编号与完整名称')
                specific_text(value)
            if self.description is not None:
                raise ValueError('报告来源不接受合成说明')
        else:
            if self.description is None:
                raise ValueError('合成来源需要人工合成说明')
            specific_text(self.description)
            if self.source_document_id is not None or self.source_document_name is not None:
                raise ValueError('合成来源不填写虚构报告')
        return self


class CaseFields(EditInput):
    abnormal_description: str
    root_cause: str
    corrective_action: str
    investigation_others: str | None
    abnormal_processes: list[str]
    origin: RecordOrigin | None = None


class DetailFields(EditInput):
    detail_id: str | None = Field(default=None, min_length=1)
    product_id: str
    customer_lot: str
    production_lot: str
    production_time: date
    detection_stage: str
    detection_time: date
    abnormal_types: list[str]
    affected_qty: StrictInt = Field(gt=0, le=2147483647)
    disposition: str
    origin: RecordOrigin | None = None

    _date = field_validator('production_time', 'detection_time', mode='before')(date_value)


class EvidenceFields(EditInput):
    checkpoint_id: str | None = Field(default=None, min_length=1)
    checkpoint_type: str
    custom_name: str | None
    result: str
    relevance: str
    origin: RecordOrigin | None = None


class Availability(EditInput):
    known_at: date
    basis: str = Field(min_length=1, max_length=2000)
    confirmed_complete: StrictBool

    _date = field_validator('known_at', mode='before')(date_value)
    _basis = field_validator('basis')(specific_text)

    @field_validator('confirmed_complete')
    @classmethod
    def require_confirmation(cls, value):
        if not value:
            raise ValueError('须确认整份资料在新日期前已结案且完整可用')
        return value


class CaseEditRequest(EditInput):
    expected_snapshot_id: str = Field(min_length=1, max_length=200)
    case: CaseFields
    details: list[DetailFields] = Field(max_length=MAX_RECORDS)
    evidences: list[EvidenceFields] = Field(max_length=MAX_RECORDS)
    closure_status: Literal['confirmed', 'NDF']
    availability: Availability | None = None

    _identity = field_validator('expected_snapshot_id')(specific_text)


class CaseNotFoundError(ValueError):
    """当前身份内找不到该案。"""


class CaseEditError(ValueError):
    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__('整案资料未通过校验')


def _issue(path: str, field: str, message: str, record_id: str | None = None) -> dict:
    return {'path': path, 'record_id': record_id, 'field': field, 'message': message}


def _keep_set_order(values: list[str], old: list[str]) -> list[str]:
    return list(old) if len(values) == len(old) and set(values) == set(old) else values


def _candidate_errors(
    records: dict, payload: dict, reference, known_at: date, locations: dict,
) -> list[dict]:
    """完整候选集的 CR、证据、时点与规模检查；错误带提交字段定位。"""
    errors = []
    messages = validate_relations(**records, **reference.validator_maps())
    if not messages:
        messages = check_confirmed_evidence(records['evidences'], {
            item.case_id: payload['sources'][item.case_id].get('closure_status')
            for item in records['cases']
        })
    for message in messages:
        parts = message.split(' | ', 3)
        location = parts[1] if len(parts) > 1 else 'Dataset'
        field = parts[2] if len(parts) > 2 else 'dataset'
        errors.append(_issue(locations.get(location, location), field, message, location))
    for detail in records['details']:
        if detail.detection_time > known_at:
            errors.append(_issue(
                locations.get(f'CaseDetail[{detail.detail_id}]', f'CaseDetail[{detail.detail_id}]'),
                'detection_time', '发现日期晚于整份资料完整可用日期', detail.detail_id,
            ))
    if len(records['cases']) > MAX_CASES or sum(
            len(records[key]) for key in ('cases', 'details', 'evidences')) > MAX_RECORDS:
        errors.append(_issue('dataset', 'records', '超过 500 案或 10000 条业务记录'))
    return errors


def prepare_case_edit(
    current: 'LoadedSnapshot', case_id: str, submission: CaseEditRequest,
) -> tuple[dict, dict, date, str] | None:
    """返回候选实体/payload/日期/依据；无变化返回 None，错误带提交字段定位。

    调用方须先在写事务锁内核对预期快照。这里不生成快照身份或写数据库。
    """
    old_case = next((item for item in current.records['cases'] if item.case_id == case_id), None)
    if old_case is None:
        raise CaseNotFoundError(case_id)
    records, payload = deepcopy(current.records), deepcopy(current.payload)
    changes, added, removed, errors = [], [], [], []
    locations = {f'Case[{case_id}]': 'case'}
    origins = []
    modified = []
    source = payload['sources'][case_id]
    previous_web = source.get('web_provenance', {})
    current_origins = {(item['record_type'], item['record_id']): item
                       for item in previous_web.get('origins', [])}

    def update_origin(kind, record_id, origin, is_new, path):
        key = (kind, record_id)
        if is_new and origin is None:
            errors.append(_issue(path + '.origin', 'origin', '新增记录须首次登记具体来源', record_id))
        if origin is not None:
            value = {'record_type': kind, 'record_id': record_id,
                     **origin.model_dump(exclude_none=True)}
            previous = current_origins.get(key)
            if previous is None:
                legacy = next((item for item in source.get('records', [])
                               if (item['record_type'], item['record_id']) == key), None)
                if legacy is not None:
                    previous = {'record_type': kind, 'record_id': record_id, 'kind': 'report',
                                **{field: legacy[field] for field in (
                                    'source_document_id', 'source_document_name', 'source_location')}}
            if value != previous:
                current_origins[key] = value
                changes.append(path + '.origin')
                modified.append(key)
        origins.append(key)

    values = submission.case.model_dump(exclude={'origin'})
    values['abnormal_processes'] = _keep_set_order(values['abnormal_processes'], old_case.abnormal_processes)
    new_case = Case(case_id=case_id, **values)
    for key, value in values.items():
        if value != getattr(old_case, key):
            changes.append('case.' + key)
            modified.append(('case', case_id))
    records['cases'] = [new_case if item.case_id == case_id else item for item in records['cases']]
    update_origin('case', case_id, submission.case.origin, False, 'case')

    for kind, entity_type, id_field, record_type, prefix in (
        ('details', CaseDetail, 'detail_id', 'detail', 'D'),
        ('evidences', EvidenceCheckpoint, 'checkpoint_id', 'evidence', 'E'),
    ):
        old_items = {getattr(item, id_field): item for item in current.records[kind] if item.case_id == case_id}
        used = {getattr(item, id_field) for item in current.records[kind]}
        replacements, new_ids = {}, []
        for index, item in enumerate(getattr(submission, kind)):
            path = f'{kind}[{index}]'
            record_id = getattr(item, id_field)
            is_new = record_id is None
            if is_new:
                record_id = prefix + uuid4().hex
                while record_id in used:
                    record_id = prefix + uuid4().hex
                used.add(record_id)
                new_ids.append(record_id)
                added.append({'record_type': record_type, 'record_id': record_id})
            elif record_id not in old_items or record_id in replacements:
                errors.append(_issue(path + '.' + id_field, id_field,
                                     '既有 ID 必须唯一且属于本案；新增记录请省略 ID', record_id))
                continue
            values = item.model_dump(exclude={id_field, 'origin'})
            old = old_items.get(record_id)
            if kind == 'details' and old is not None:
                values['abnormal_types'] = _keep_set_order(values['abnormal_types'], old.abnormal_types)
            replacement = entity_type(**{id_field: record_id}, case_id=case_id, **values)
            replacements[record_id] = replacement
            locations[f'{entity_type.__name__}[{record_id}]'] = path
            if not is_new:
                for key, value in values.items():
                    if value != getattr(old, key):
                        changes.append(f'{kind}[{record_id}].{key}')
                        modified.append((record_type, record_id))
            update_origin(record_type, record_id, item.origin, is_new, path)
        removed.extend({'record_type': record_type, 'record_id': key}
                       for key in old_items if key not in replacements)
        records[kind] = [replacements[getattr(item, id_field)] if item.case_id == case_id else item
                         for item in current.records[kind]
                         if item.case_id != case_id or getattr(item, id_field) in replacements]
        records[kind].extend(replacements[key] for key in new_ids)

    if submission.closure_status != source['closure_status']:
        source['closure_status'] = submission.closure_status
        changes.append('source.closure_status')
        modified.append(('case', case_id))
    known_at, basis = current.snapshot.known_at, current.snapshot.basis
    if submission.availability is not None:
        known_at, basis = submission.availability.known_at, submission.availability.basis
        if known_at != current.snapshot.known_at:
            changes.append('availability.known_at')
        if basis != current.snapshot.basis:
            changes.append('availability.basis')

    # 先拒绝 ID/来源问题，避免重复或无来源记录进入关系检查。
    if errors:
        raise CaseEditError(errors)
    errors.extend(_candidate_errors(records, payload, current.reference, known_at, locations))
    if errors:
        raise CaseEditError(errors)
    if not changes and not added and not removed:
        return None

    live = set(origins)
    if 'records' in source:
        source['records'] = [item for item in source['records'] if (item['record_type'], item['record_id']) in live]
    kept_modified = [(item['record_type'], item['record_id']) for item in previous_web.get('modified_records', [])]
    source['web_provenance'] = {
        'modified_records': [{'record_type': kind, 'record_id': key}
                             for kind, key in dict.fromkeys(kept_modified + modified) if (kind, key) in live],
        'origins': [value for key, value in current_origins.items() if key in live],
    }
    payload['user_maintained'] = True
    payload.setdefault('baseline_snapshot_id', current.snapshot.snapshot_id)
    payload['review_status'] = 'draft_pending_human_review'
    payload.setdefault('case_revisions', {}).setdefault(case_id, []).append({
        'saved_at': datetime.now(timezone.utc).isoformat(), 'operation': 'edit',
        'changed_fields': changes, 'added_records': added, 'removed_records': removed,
    })
    return records, payload, known_at, basis


def prepare_case_creation(
    current: 'LoadedSnapshot', submission: CaseEditRequest,
) -> tuple[dict, dict, date, str, str]:
    """返回新案例候选实体/payload/日期/依据/编号；错误带提交字段定位。

    调用方须先在写事务锁内核对预期快照。这里不生成快照身份或写数据库；
    不通过先写占位案再编辑实现新增，也不接受客户端指定的记录编号。
    """
    records, payload = deepcopy(current.records), deepcopy(current.payload)
    errors, locations = [], {}
    used_cases = {item.case_id for item in records['cases']}
    case_id = 'C' + uuid4().hex
    while case_id in used_cases:
        case_id = 'C' + uuid4().hex

    if submission.case.origin is None:
        errors.append(_issue('case.origin', 'origin', '新增案例须首次登记具体来源'))
    for index, item in enumerate(submission.details):
        if item.detail_id is not None:
            errors.append(_issue(f'details[{index}].detail_id', 'detail_id',
                                 '新增案例的明细编号由服务端生成，不接受客户端指定', item.detail_id))
    for index, item in enumerate(submission.evidences):
        if item.checkpoint_id is not None:
            errors.append(_issue(f'evidences[{index}].checkpoint_id', 'checkpoint_id',
                                 '新增案例的调查项编号由服务端生成，不接受客户端指定', item.checkpoint_id))
    if errors:
        raise CaseEditError(errors)

    origins = [('case', case_id, submission.case.origin)]
    records['cases'].append(Case(case_id=case_id, **submission.case.model_dump(exclude={'origin'})))
    locations[f'Case[{case_id}]'] = 'case'
    for kind, entity_type, id_field, record_type, prefix in (
        ('details', CaseDetail, 'detail_id', 'detail', 'D'),
        ('evidences', EvidenceCheckpoint, 'checkpoint_id', 'evidence', 'E'),
    ):
        used_ids = {getattr(item, id_field) for item in records[kind]}
        for index, item in enumerate(getattr(submission, kind)):
            path = f'{kind}[{index}]'
            record_id = prefix + uuid4().hex
            while record_id in used_ids:
                record_id = prefix + uuid4().hex
            used_ids.add(record_id)
            values = item.model_dump(exclude={id_field, 'origin'})
            records[kind].append(entity_type(**{id_field: record_id}, case_id=case_id, **values))
            locations[f'{entity_type.__name__}[{record_id}]'] = path
            if item.origin is None:
                errors.append(_issue(path + '.origin', 'origin', '新增记录须首次登记具体来源', record_id))
            else:
                origins.append((record_type, record_id, item.origin))
    if errors:
        raise CaseEditError(errors)

    known_at, basis = current.snapshot.known_at, current.snapshot.basis
    if submission.availability is not None:
        known_at, basis = submission.availability.known_at, submission.availability.basis
    payload.setdefault('sources', {})[case_id] = {
        'closure_status': submission.closure_status,
        'web_provenance': {
            'modified_records': [],
            'origins': [{'record_type': kind, 'record_id': record_id,
                         **origin.model_dump(exclude_none=True)}
                        for kind, record_id, origin in origins],
        },
    }
    errors.extend(_candidate_errors(records, payload, current.reference, known_at, locations))
    if errors:
        raise CaseEditError(errors)
    payload['user_maintained'] = True
    payload.setdefault('baseline_snapshot_id', current.snapshot.snapshot_id)
    payload['review_status'] = 'draft_pending_human_review'
    payload.setdefault('case_revisions', {})[case_id] = [{
        'saved_at': datetime.now(timezone.utc).isoformat(), 'operation': 'create',
        'changed_fields': [],
        'added_records': [{'record_type': kind, 'record_id': record_id}
                          for kind, record_id, _ in origins],
        'removed_records': [],
    }]
    return records, payload, known_at, basis, case_id


def prepare_case_deletion(current: 'LoadedSnapshot', case_id: str) -> tuple[dict, dict]:
    """返回删除后的候选实体/payload；当前身份内找不到该案抛 CaseNotFoundError。

    调用方须先在写事务锁内核对预期快照。这里不生成快照身份或写数据库；
    组实体保留，交给完整候选校验判断成员数是否仍然合法。
    """
    if not any(item.case_id == case_id for item in current.records['cases']):
        raise CaseNotFoundError(case_id)
    records, payload = deepcopy(current.records), deepcopy(current.payload)
    records['cases'] = [item for item in records['cases'] if item.case_id != case_id]
    records['details'] = [item for item in records['details'] if item.case_id != case_id]
    records['evidences'] = [item for item in records['evidences'] if item.case_id != case_id]
    records['memberships'] = [item for item in records['memberships'] if item.case_id != case_id]
    payload.setdefault('sources', {}).pop(case_id, None)
    revisions = payload.get('case_revisions', {})
    revisions.pop(case_id, None)
    if not revisions:
        # 与整体替换一致：没有逐案修订时不保留空键。
        payload.pop('case_revisions', None)
    errors = _candidate_errors(records, payload, current.reference, current.snapshot.known_at, {})
    if errors:
        raise CaseEditError(errors)
    payload['user_maintained'] = True
    payload.setdefault('baseline_snapshot_id', current.snapshot.snapshot_id)
    return records, payload
