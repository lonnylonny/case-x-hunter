"""唯一上传格式：Case、Detail、Evidence 分行；只适用 CR，不施加合成采样限制。"""

from collections import Counter
from dataclasses import fields
from datetime import date
import csv
import io
import re

from casetrace.data.dataset_model import Case, CaseDetail, EvidenceCheckpoint
from casetrace.data.reference import ReferenceData
from casetrace.data.validators import check_confirmed_evidence, validate_relations

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_CASES = 500
MAX_RECORDS = 10000
IMPORT_FORMAT = "casetrace-csv-v1"
RECORD_MODELS = {
    "case": ("cases", Case, "case_id"),
    "detail": ("details", CaseDetail, "detail_id"),
    "evidence": ("evidences", EvidenceCheckpoint, "checkpoint_id"),
}
SOURCE_COLUMNS = ("source_document_id", "source_document_name", "source_location")
COLUMNS = tuple(dict.fromkeys([
    "record_type", *[field.name for _, model, _ in RECORD_MODELS.values() for field in fields(model)],
    *SOURCE_COLUMNS, "closure_status",
]))
OPTIONAL_COLUMNS = {"investigation_others", "custom_name"}
LIST_COLUMNS = {"abnormal_processes", "abnormal_types"}
DATE_COLUMNS = {"production_time", "detection_time"}
PLACEHOLDERS = {"同上", "见报告", "见附件", "未知", "待补", "无", "不详", "n/a", "na", "none", "null", "unknown", "tbd", "-", "?"}
# 整份文件已有 5 MiB 上限，允许在此范围内保留长调查原文。
csv.field_size_limit(MAX_UPLOAD_BYTES)


class CsvImportError(ValueError):
    """可给使用者定位的输入错误；不包含数据库异常。"""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("CSV 未通过校验")


def csv_bytes(rows=()) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def parse_csv(raw: bytes, *, reference: ReferenceData, known_at: date,
              filename: str) -> tuple[dict, dict]:
    """完整解析后一次执行现有 CR；任何错误都不产生可写入数据。"""
    errors = []

    def error(row, column, message):
        errors.append({"row": row, "column": column, "message": message})

    if len(raw) > MAX_UPLOAD_BYTES:
        raise CsvImportError([{"row": None, "column": "file", "message": "文件超过 5 MiB"}])
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CsvImportError([{"row": None, "column": "file", "message": "必须使用 UTF-8 编码"}]) from None
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, [])
        header_counts = Counter(header)
        for column in COLUMNS:
            if column not in header_counts:
                error(1, column, "缺少模板列")
        for column, count in header_counts.items():
            if column not in COLUMNS:
                error(1, column, "不是模板列")
            elif count > 1:
                error(1, column, "列名重复")
        if errors:
            raise CsvImportError(errors)
        records = dict(cases=[], details=[], evidences=[], groups=[], memberships=[])
        origins = {}
        locations = {}
        closure_statuses = {}
        count = 0
        while True:
            line = reader.line_num + 1
            values = next(reader, None)
            if values is None:
                break
            count += 1
            if count > MAX_RECORDS:
                error(line, "file", f"最多 {MAX_RECORDS} 条记录")
                break
            if len(values) != len(header):
                error(line, "file", f"列数不符：需要 {len(header)} 列，实际 {len(values)} 列")
                continue
            row = dict(zip(header, values))
            record_type = row["record_type"]
            if record_type not in RECORD_MODELS:
                error(line, "record_type", "只接受 case、detail、evidence")
                continue
            entity, model, id_field = RECORD_MODELS[record_type]
            applicable = {field.name for field in fields(model)}
            allowed = applicable | set(SOURCE_COLUMNS) | {"record_type"}
            if record_type == "case":
                allowed.add("closure_status")
            start_errors = len(errors)
            for column, value in row.items():
                if "\x00" in value:
                    error(line, column, "不允许 NUL 字符")
                if column not in allowed and value:
                    error(line, column, f"{record_type} 行不填写此列")
            for column in SOURCE_COLUMNS:
                value = row[column].strip()
                if not value or value.casefold() in PLACEHOLDERS:
                    error(line, column, "必须填写具体来源，不接受空白或占位内容")
            if record_type == "case" and row["closure_status"] not in ("confirmed", "NDF"):
                error(line, "closure_status", "Case 必填 confirmed 或 NDF，不能从根因文本推定")
            data = {}
            for column in applicable:
                value = row[column]
                if not value.strip() and column not in OPTIONAL_COLUMNS:
                    error(line, column, "必填，不能是空白")
                    continue
                try:
                    if column in LIST_COLUMNS:
                        value = [part.strip() for part in value.split(";")]
                    elif column in DATE_COLUMNS:
                        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                            raise ValueError("日期必须为 YYYY-MM-DD")
                        value = date.fromisoformat(value)
                        if column == "detection_time" and value > known_at:
                            raise ValueError("发现日期晚于声明的完整可用日期")
                    elif column == "affected_qty":
                        if not re.fullmatch(r"[0-9]+", value):
                            raise ValueError("必须为正整数")
                        value = int(value)
                        if value > 2147483647:
                            raise ValueError("超过数据库 integer 可保存的最大数量 2147483647")
                    elif column in OPTIONAL_COLUMNS and not value.strip():
                        value = None
                    elif column.endswith("_id") and value != value.strip():
                        raise ValueError("标识前后不能有空白")
                except ValueError as exc:
                    error(line, column, str(exc))
                data[column] = value
            if len(errors) != start_errors:
                continue
            item = model(**data)
            records[entity].append(item)
            key = f"{model.__name__}[{getattr(item, id_field)}]"
            if key in locations:
                error(line, id_field, f"ID 重复，首次出现在第 {locations[key][0]} 行")
            locations.setdefault(key, []).append(line)
            if record_type == "case":
                closure_statuses[item.case_id] = row["closure_status"]
            origins.setdefault(item.case_id, []).append({
                "record_type": record_type, "record_id": getattr(item, id_field),
                **{column: row[column] for column in SOURCE_COLUMNS},
                "filename": filename, "csv_line": line,
            })
    except csv.Error as exc:
        error(reader.line_num or 1, "file", f"CSV 解析失败：{exc}")
    if errors:
        raise CsvImportError(errors)
    if not records["cases"] or len(records["cases"]) > MAX_CASES:
        error(None, "case_id", f"必须有 1～{MAX_CASES} 个 Case")
    messages = validate_relations(**records, **reference.validator_maps())
    if not messages:
        messages = check_confirmed_evidence(records["evidences"], closure_statuses)
    for message in messages:
        parts = message.split(" | ", 3)
        column = parts[2] if len(parts) >= 3 else "file"
        rows = locations.get(parts[1], []) if len(parts) >= 2 else []
        for line in rows or [None]:
            error(line, column, message)
    if errors:
        raise CsvImportError(errors)
    return records, {
        "split": "user_upload", "review_status": "draft_pending_human_review",
        "import_format": IMPORT_FORMAT,
        "sources": {case.case_id: {"records": origins[case.case_id],
                                   "closure_status": closure_statuses[case.case_id]}
                    for case in records["cases"]},
        "queries": [],
    }
