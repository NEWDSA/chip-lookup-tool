# -*- coding: utf-8 -*-
"""
chip_lookup.main
----------------
程序入口。可直接：
    python src/main.py
也可被 src/ 当包运行：
    python -m chip_lookup (假设已 PYTHONPATH=src)
"""

from __future__ import annotations

import argparse
import io
import os
import sys

# 兜底：让打包后 exe 的 stdout/stderr 是 UTF-8，避免中文乱码
try:
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr and hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.normpath(os.path.join(HERE)))

from database import ChipDatabase, default_database_path  # noqa: E402
from paths import ensure_user_database, resolve_database_path  # noqa: E402
from ui import run as run_ui  # noqa: E402


def _parse_args():
    parser = argparse.ArgumentParser(
        description="ChipLookup - 跨平台芯片料号查询器",
    )
    parser.add_argument(
        "--db",
        help="CSV 数据库路径（默认会自动定位到用户级 data/chip_database.csv）",
        default=None,
    )
    parser.add_argument(
        "--headless-stats",
        action="store_true",
        help="不启动 UI，只打印数据库统计信息后退出",
    )
    parser.add_argument(
        "--screenshot",
        help="截图调试：启动 UI → 跑查询 → 截图保存到该路径后退出（需要 Pillow）",
    )
    parser.add_argument(
        "--screenshot-query",
        help="截图前自动填入并查询的料号",
    )
    parser.add_argument(
        "--screenshot-stitch",
        action="store_true",
        help="截图模式（拼接）：截两张（顶部+滚到底部）并垂直拼成一张完整图",
    )
    parser.add_argument(
        "--screenshot-mode",
        choices=["paginate", "load_more"],
        help="截图前设置分页模式",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.db:
        db_path = args.db
    else:
        # 自动定位：优先用户级（exe 旁边 data/），缺则从 bundle 拷贝种子
        db_path = ensure_user_database()
    db = ChipDatabase(db_path)
    if args.screenshot_stitch:
        setattr(db, "_screenshot_mode", "stitch")
    print(f"[ChipLookup] 数据库路径：{db.csv_path}")
    if args.headless_stats:
        recs = db.list_records()
        print(f"[stats] 共 {len(recs)} 条记录，源文件：{db.csv_path}")
        vendors: dict[str, int] = {}
        types: dict[str, int] = {}
        for r in recs:
            v = r.get("manufacturer") or "未知"
            t = r.get("type") or "未知"
            vendors[v] = vendors.get(v, 0) + 1
            types[t] = types.get(t, 0) + 1
        for k, v in sorted(vendors.items(), key=lambda x: -x[1]):
            print(f"  厂商 {k}: {v}")
        for k, v in sorted(types.items(), key=lambda x: -x[1]):
            print(f"  类型 {k}: {v}")
        return 0
    run_ui(
        db,
        screenshot_to=args.screenshot,
        screenshot_query=args.screenshot_query,
        screenshot_mode=args.screenshot_mode,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
