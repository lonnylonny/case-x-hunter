"""固定 CSV 边界：异常表头及时拒绝，合法列顺序不受限制。"""

import csv
from datetime import date
import io
import json

import pytest

from casetrace.data.csv_import import COLUMNS, CsvImportError, csv_bytes, parse_csv
from casetrace.data.reference import load_reference


def parse(raw, reference_path):
    return parse_csv(raw, reference=load_reference(reference_path),
                     known_at=date(2026, 9, 15), filename="synthetic.csv")


def test_many_duplicate_header_columns_report_the_name_once(reference_path):
    raw = (",".join([*COLUMNS, *(["case_id"] * 5000)]) + "\n").encode()
    with pytest.raises(CsvImportError) as caught:
        parse(raw, reference_path)
    assert caught.value.errors == [
        {"row": 1, "column": "case_id", "message": "列名重复"},
    ]


@pytest.mark.parametrize("header,column,message", [
    ([name for name in COLUMNS if name != "case_id"], "case_id", "缺少模板列"),
    ([*COLUMNS, "extra"], "extra", "不是模板列"),
    ([*COLUMNS, "case_id"], "case_id", "列名重复"),
])
def test_invalid_headers_remain_locatable(reference_path, header, column, message):
    with pytest.raises(CsvImportError) as caught:
        parse((",".join(header) + "\n").encode(), reference_path)
    assert caught.value.errors == [{"row": 1, "column": column, "message": message}]


def test_valid_reordered_columns_preserve_records_and_sources(reference_path, demo_path):
    payload = json.loads(demo_path.read_text())
    rows = []
    for entity, record_type in (("cases", "case"), ("details", "detail"),
                                ("evidences", "evidence")):
        for item in payload[entity]:
            row = {key: ";".join(value) if isinstance(value, list) else value
                   for key, value in item.items()}
            row.update(record_type=record_type, source_document_id="SYN-REPORT-01",
                       source_document_name="人工合成调查报告", source_location="第1页")
            if record_type == "case":
                row["closure_status"] = "confirmed"
            rows.append(row)
    original = parse(csv_bytes(rows), reference_path)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(reversed(COLUMNS)))
    writer.writeheader()
    writer.writerows(rows)
    assert parse(output.getvalue().encode(), reference_path) == original
