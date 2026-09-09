# -*- coding: utf-8 -*-
"""
tools/decode_db.py
------------------
把 fdnext 规则解码结果合并进 ChipLookup 的 CSV 数据库（只填空，不覆盖人工字段）。

与 tools/sync_upstream.py 同一套合并语义：
    1) 已填写的字段永不被覆盖 —— 人工维护的数据优先级最高；
    2) 只有「解码器能确认的字段」才参与填空（见下方映射表）；
    3) 全程纯 Python 标准库，无 Node.js / 无第三方 PyPI 包。

字段映射（仅当 CSV 对应列为空时填写）：
    manufacturer  <- device.vendor（映射为厂商显示名，如 micron -> Micron）
    type          <- fields.dram_type（DDR3/DDR4/DDR5…）
    capacity      <- fields.dram_density / die_density（Mbit -> Gb 串，如 "16Gb"）
    bit_width     <- fields.dram_width / device_width（-> "x8" 形）
    voltage       <- fields.dram_voltage（DRAM；NAND 的 voltage 为长描述串，不自动填）
    package       <- fields.package（如 "FBGA-78"）
    die_count     <- fields.die_count / dram_die_count
    cs_count      <- fields.cs_count
    die_revision  <- fields.die_revision

不覆盖 / 不猜测：speed / op_temp / dimensions / notes / model（保持人工维护），
以及一切解码拿不到或含糊的字段。

边界说明：part_number 本身是丝印/标记码（如 D8DKS、W25Q128JVSSIQ 等）时，
规则解码返回 not_found，本工具不会尝试瞎填 —— 这类靠 mdb 索引补全的任务
仍由 tools/sync_upstream.py（标记码 -> model/manufacturer）负责。

用法（默认 dry-run，只预览不写盘）：
    python tools/decode_db.py                        # 预览可补字段
    python tools/decode_db.py --write                # 预览 + 写回（仍只填空）
    python tools/decode_db.py --db <other.csv>       # 指定目标库
    python tools/decode_db.py --verbose              # 逐条打印解码摘要

退出码：0 成功；1 运行错误（未命中不视为错误）。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, List, Optional

# 让 "python tools/decode_db.py" 也能找到 src/ 包
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.database import ChipDatabase, default_database_path  # noqa: E402
from src.fdnext import load_engine  # noqa: E402
from src.fdnext.constants import UNKNOWN  # noqa: E402

# 复用 sync_upstream 的厂商显示名映射（如 skhynix -> SK Hynix），避免两套表漂移
sys.path.insert(0, HERE)
from sync_upstream import VENDOR_DISPLAY  # noqa: E402

# 解码结果里可能携带这些列以外字段，忽略即可
_TARGET_COLUMNS = (
    "manufacturer", "type", "capacity", "bit_width",
    "voltage", "package", "die_count", "cs_count", "die_revision",
)


def _gb_string(mbit: Any) -> Optional[str]:
    """Mbit -> 本地习惯的容量串（如 16384 -> "16Gb"）。"""
    if not isinstance(mbit, (int, float)) or mbit <= 0:
        return None
    gb = float(mbit) / 1024.0
    if gb.is_integer():
        return "%dGb" % int(gb)
    return "%.1fGb" % gb


def _x_width(value: Any) -> Optional[str]:
    """位宽 -> "x8" 形；已是 xN 形则原样返回。"""
    if not isinstance(value, (int, float, str)):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("x"):
        return text
    if text.isdigit():
        return "x" + text
    return None


def _display_vendor(vendor: str) -> str:
    if not vendor or vendor == UNKNOWN:
        return ""
    return VENDOR_DISPLAY.get(vendor.lower(), vendor)


def _fills_for(result: Dict[str, Any]) -> Dict[str, str]:
    """从单条解码结果计算可填写的 CSV 字段（键固定为列名）。"""
    fills: Dict[str, str] = {}
    if result.get("status") != "ok":
        return fills
    draft = result.get("_draft") or {}
    device = draft.get("device") or {}
    fields = draft.get("fields") or {}
    chip_kind = device.get("chipKind") or ""

    vendor = _display_vendor(device.get("vendor"))
    if vendor:
        fills["manufacturer"] = vendor

    if chip_kind == "dram":
        dram_type = fields.get("dram_type")
        if dram_type:
            fills["type"] = str(dram_type)
        gb = _gb_string(fields.get("dram_density"))
        if gb:
            fills["capacity"] = gb
        w = _x_width(fields.get("dram_width"))
        if w:
            fills["bit_width"] = w
        voltage = fields.get("dram_voltage")
        if voltage and str(voltage).strip().upper() != "UNKNOWN":
            fills["voltage"] = str(voltage)
        if fields.get("dram_die_count"):
            fills["die_count"] = str(fields["dram_die_count"])
    elif chip_kind == "raw_nand":
        gb = _gb_string(fields.get("density"))
        if gb:
            fills["capacity"] = gb
        w = _x_width(fields.get("device_width"))
        if w:
            fills["bit_width"] = w
        if fields.get("die_count"):
            fills["die_count"] = str(fields["die_count"])
    else:
        # 其它 chipKind（eMMC / UFS / logic…）：仅补厂商，规格留给人工
        return {"manufacturer": vendor} if vendor else {}

    if fields.get("package"):
        fills["package"] = str(fields["package"])
    if fields.get("cs_count"):
        fills["cs_count"] = str(fields["cs_count"])
    if fields.get("die_revision"):
        fills["die_revision"] = str(fields["die_revision"])
    return fills


def plan_record(rec: Dict[str, Any], engine: Any) -> Dict[str, Any]:
    """对单条记录返回 {fills, status, note}。fills 只含 CSV 当前为空的目标列。"""
    pn = (rec.get("part_number") or "").strip()
    result = engine.decode_part(pn) if pn else None

    if result is None or result.get("status") != "ok":
        return {"fills": {}, "status": "not_found", "note": ""}

    fills = _fills_for(result)
    # 只填空：已有值的列绝不覆盖
    fills = {k: v for k, v in fills.items()
             if k in rec and not (rec.get(k) or "").strip()}
    return {"fills": fills, "status": "ok", "note": ""}


def run(args: argparse.Namespace) -> int:
    db = ChipDatabase(args.db)
    records = db.list_records()
    print("[decode-db] 本地记录 %d 条 <- %s" % (len(records), db.csv_path))

    engine = load_engine(args.cache_dir, offline=args.offline, refresh=args.refresh)

    # 单次遍历：能解码且有空可填的记录进 plan；未命中计数独立统计
    plan: List[Dict[str, Any]] = []
    not_found = 0
    for rec in records:
        pn = (rec.get("part_number") or "").strip()
        if not pn:
            continue
        res = plan_record(rec, engine)
        if res["status"] == "not_found":
            not_found += 1
            continue
        if res["fills"]:
            plan.append({"part_number": pn, **res})

    col_fills: Dict[str, int] = {}
    changed = 0
    for p in plan:
        fills = p["fills"]
        for k in fills:
            col_fills[k] = col_fills.get(k, 0) + 1
        if args.verbose:
            detail = ", ".join("%s=%s" % (k, v) for k, v in sorted(fills.items()))
            print("  - %-20s -> %s" % (p["part_number"], detail))
        if not args.write:
            continue
        rec = db.get(p["part_number"]) or {}
        for k, v in fills.items():
            if not (rec.get(k) or "").strip():
                rec[k] = v
        db.upsert(rec)
        changed += 1

    if col_fills:
        summary = ", ".join("%s %d" % (k, n) for k, n in sorted(col_fills.items()))
    else:
        summary = "无"

    print("[decode-db] 可解码记录 %d 条；本次可补字段 -> %s" % (len(plan), summary))
    print("[decode-db] 未命中 %d 条（多为丝印/标记码，需 mdb 索引，见 tools/sync_upstream.py）" % not_found)

    if args.write:
        if changed:
            db.save()
            print("[decode-db] 已只填空合并 %d 条并写回 %s" % (changed, db.csv_path))
        else:
            print("[decode-db] 无可填空字段，未写盘（保持不变）。")
    else:
        print("[decode-db] dry-run：以上为预览，未写盘（加 --write 才写回）。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="fdnext 解码结果 -> ChipLookup CSV 只填空合并（纯 Python，无 Node）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", default=None,
                        help="目标数据库，默认 data/chip_database.xlsx")
    parser.add_argument("--write", action="store_true",
                        help="写回 CSV（默认 dry-run 只预览）")
    parser.add_argument("--verbose", action="store_true",
                        help="逐条打印将填写的内容")
    parser.add_argument("--cache-dir", default=None,
                        help="规则缓存目录（快照存在时忽略）")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式（快照存在时默认即离线）")
    parser.add_argument("--refresh", action="store_true",
                        help="忽略缓存新鲜期强制重下（仅无快照回退时有效）")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.db = args.db or default_database_path()
    try:
        return run(args)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print("[decode-db] 错误: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
