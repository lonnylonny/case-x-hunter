"""storage 命令入口：init 建表，import 导入，verify 核对，prepare 启动准备。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

import psycopg

from casetrace.answer.context import DEV_V3_SNAPSHOT
from casetrace.storage.connection import connect
from casetrace.storage.schema import DEFAULT_SCHEMA, initialize_schema
from casetrace.storage.snapshot import import_snapshot, prepare_snapshot, verify_snapshot

DEFAULT_DATASET = Path("data/dev/demo-v3.json")
DEFAULT_REFERENCE = Path("data/reference/封装异常_failure_modes_db_structured_v5_engineering_audited-2.xlsx")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m casetrace.storage", description="CaseTrace PostgreSQL 存储",
    )
    commands = parser.add_subparsers(dest="command")
    for command, help_text in (
        ("init", "建立 CaseTrace 专用 schema（可重复执行）"),
        ("import", "原子导入 dev-v3 完整数据"),
        ("verify", "比较数据库与源文件的完整内容"),
        ("prepare", "启动前准备数据：空库导入示例，已有快照核对并保留"),
    ):
        subparser = commands.add_parser(command, help=help_text)
        subparser.add_argument("--test", action="store_true", help="使用 CASETRACE_TEST_DATABASE_URL")
        subparser.add_argument("--schema", default=DEFAULT_SCHEMA, help="专用 schema 名")
        if command != "init":
            subparser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET, help="语料文件")
            subparser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE, help="主数据 Excel")
    return parser


def run_init(args: argparse.Namespace) -> int:
    with connect(test=args.test, autocommit=True) as connection:
        result = initialize_schema(connection, schema=args.schema)
    print(f"schema {result['schema']} 已就绪，版本 {result['version']}")
    print(f"表（{len(result['tables'])}）：{'、'.join(result['tables'])}")
    return 0


def run_import(args: argparse.Namespace) -> int:
    with connect(test=args.test, autocommit=True) as connection:
        result = import_snapshot(
            connection, args.dataset, args.reference,
            snapshot=DEV_V3_SNAPSHOT, schema=args.schema,
        )
    counts = "、".join(f"{table}={count}" for table, count in result.counts.items())
    print(f"快照 {result.snapshot_id} → schema {result.schema}：{result.status}")
    print(f"内容摘要 {result.content_digest}（{result.digest_version}）")
    print(f"行数：{counts}")
    if result.status == "no_op":
        print("库内内容与本次请求一致，未重复写入、未改动已有数据。")
    else:
        print("主数据、实体、关系与快照元数据已在同一事务内提交。")
    return 0


def run_verify(args: argparse.Namespace) -> int:
    with connect(test=args.test, autocommit=True) as connection:
        report = verify_snapshot(
            connection, dataset_path=args.dataset, reference_path=args.reference,
            snapshot=DEV_V3_SNAPSHOT, schema=args.schema,
        )
    counts = "、".join(f"{table}={count}" for table, count in report.counts.items())
    print(f"快照 {report.snapshot_id} ← schema {report.schema}")
    print(f"内容摘要 {report.content_digest}（{report.digest_version}）与库内记录一致")
    print(f"行数：{counts}")
    for name, passed in report.checks.items():
        print(f"  {'通过' if passed else '未通过'}  {name}")
    for problem in report.problems:
        print(f"问题：{problem}", file=sys.stderr)
    print("数据库完整内容与源文件一致。" if report.ok else "核对未通过。")
    return 0 if report.ok else 1


def run_prepare(args: argparse.Namespace) -> int:
    with connect(test=args.test, autocommit=True) as connection:
        result = prepare_snapshot(
            connection, args.dataset, args.reference,
            snapshot=DEV_V3_SNAPSHOT, schema=args.schema,
        )
    print(f"快照 {result.snapshot_id} → schema {result.schema}：{result.status}")
    print(f"内容摘要 {result.content_digest}（{result.digest_version}）已核对")
    print("示例数据已原子导入。" if result.status == "imported"
          else "当前快照已保留，未重新导入示例。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    runners = {"init": run_init, "import": run_import, "verify": run_verify,
               "prepare": run_prepare}
    try:
        return runners[args.command](args)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, psycopg.Error) as error:
        print(f"错误：{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
