# -*- coding: utf-8 -*-
"""
tools/import_export.py
-----------------------
命令行工具：不开 UI 也能批量维护数据。

用法：
    python tools/import_export.py export <out_csv>
    python tools/import_export.py import <in_csv> [--replace]
    python tools/import_export.py add  <part_number> <model> [--vendor ...] ...
    python tools/import_export.py del  <part_number>
    python tools/import_export.py show <part_number>
    python tools/import_export.py stats

所有命令都会自动写回 data/chip_database.csv。
"""

from __future__ import annotations

import argparse
import os
import sys

# 让 "python tools/import_export.py" 也能找到 src/ 包
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, ROOT)

from src.database import ChipDatabase, default_database_path  # noqa: E402


def _csv_path_or_default(p: str | None) -> str:
    return p if p else default_database_path()


def cmd_export(db: ChipDatabase, args) -> int:
    dst = args.out_csv
    n = db.export_csv(dst)
    print(f"[export] 导出 {n} 条 -> {dst}")
    return 0


def cmd_import(db: ChipDatabase, args) -> int:
    src = args.in_csv
    n = db.import_csv(src, replace=args.replace)
    db.save()
    print(f"[import] 已写入 {n} 条 <- {src} (replace={args.replace})")
    return 0


def cmd_add(db: ChipDatabase, args) -> int:
    fields = {
        "part_number": args.part_number,
        "model": args.model or "",
    }
    # 透传其它可选字段
    for k in (
        "manufacturer", "type", "capacity", "bit_width", "voltage", "speed",
        "package", "dimensions", "die_count", "cs_count", "die_revision",
        "op_temp", "notes",
    ):
        v = getattr(args, k, None)
        if v:
            fields[k] = v
    db.upsert(fields)
    db.save()
    print(f"[add] 已写入: {fields['part_number']} ({fields['model']})")
    return 0


def cmd_del(db: ChipDatabase, args) -> int:
    if db.delete(args.part_number):
        db.save()
        print(f"[del] 已删除 {args.part_number}")
    else:
        print(f"[del] 未找到 {args.part_number}")
    return 0


def cmd_show(db: ChipDatabase, args) -> int:
    r = db.get(args.part_number)
    if not r:
        print(f"[show] 未找到 {args.part_number}")
        return 1
    for k, v in r.items():
        print(f"  {k:<14}: {v}")
    return 0


def cmd_stats(db: ChipDatabase, args) -> int:
    recs = db.list_records()
    print(f"[stats] 共 {len(recs)} 条记录")
    # 按厂商统计
    vendor_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for r in recs:
        v = (r.get("manufacturer") or "未知").strip() or "未知"
        t = (r.get("type") or "未知").strip() or "未知"
        vendor_counts[v] = vendor_counts.get(v, 0) + 1
        type_counts[t] = type_counts.get(t, 0) + 1
    print("[stats] 厂商分布:")
    for k, v in sorted(vendor_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<14}: {v}")
    print("[stats] 类型分布:")
    for k, v in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {k:<14}: {v}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ChipLookup 数据维护工具",
    )
    parser.add_argument(
        "--db",
        help="数据库路径，默认 data/chip_database.xlsx",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="导出全部数据到 CSV")
    p_exp.add_argument("out_csv", help="输出 CSV 路径")
    p_exp.set_defaults(func=cmd_export)

    p_imp = sub.add_parser("import", help="从 CSV 导入（合并或替换）")
    p_imp.add_argument("in_csv", help="输入 CSV 路径")
    p_imp.add_argument("--replace", action="store_true", help="替换整个库（危险！）")
    p_imp.set_defaults(func=cmd_import)

    p_add = sub.add_parser("add", help="新增/更新一条记录")
    p_add.add_argument("part_number", help="料号（唯一主键）")
    p_add.add_argument("model", help="型号")
    for k in (
        "manufacturer", "type", "capacity", "bit_width", "voltage", "speed",
        "package", "dimensions", "die_count", "cs_count", "die_revision",
        "op_temp", "notes",
    ):
        p_add.add_argument(f"--{k.replace('_', '-')}", dest=k, default="")
    p_add.set_defaults(func=cmd_add)

    p_del = sub.add_parser("del", help="删除一条记录")
    p_del.add_argument("part_number")
    p_del.set_defaults(func=cmd_del)

    p_show = sub.add_parser("show", help="查看一条记录")
    p_show.add_argument("part_number")
    p_show.set_defaults(func=cmd_show)

    p_stat = sub.add_parser("stats", help="统计信息")
    p_stat.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    db = ChipDatabase(_csv_path_or_default(args.db))
    return args.func(db, args)


if __name__ == "__main__":
    sys.exit(main())
