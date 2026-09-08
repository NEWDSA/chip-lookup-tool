# -*- coding: utf-8 -*-
"""
tools/fdnext_lookup.py
----------------------
本地统一检索 CLI —— 把「规则解码」与「索引反查」合成一个入口（无 Node.js 依赖）。

分层检索（同一输入同时查多层，逐层打标来源）：
    料号 / 标记码（part 模式）：
        mdb       丝印/顶标码反查（Micron FBGA 码 / SpecTek 标记码 -> 型号）
        pn        DRAM / 受控 NAND 料号索引（料号 -> 厂商）
        catalog   本地 CSV 目录（精确命中；未精确命中且无 mdb 时给模糊候选）
        rules     fdnext 规则解码（对料号形态按规则推字段）
    Flash ID（flashid 模式）：
        rules     fdnext 规则解码（纯十六进制 2~12 位偶数长度）
        fdb       Flash 已知库（Flash ID -> 控制器 / 已知料号 / 元数据）

这样 M1 的 known-boundary（D8DKS / W25Q64JVSSIQ 等纯规则引擎不覆盖的输入）
也能给出可执行答案：D8DKS 走 mdb + 本地目录；W25Q64JVSSIQ 等走本地目录。

规则来源：仓库内置快照 src/fdnext/data/fdnext-core-3.2.0/，默认离线。
索引来源：--index-dir 指向的缓存目录（缺省平台用户缓存 chiplookup/upstream），
          可在线补齐（--refresh 强制重下）；fdb/managed-nand 为可选文件。

用法：
    python tools/fdnext_lookup.py D8DKS
    python tools/fdnext_lookup.py W25Q64JVSSIQ MT40A1G16JC-062E
    python tools/fdnext_lookup.py --json 2C84044BA900 ADDE14A2D0E0
    python tools/fdnext_lookup.py --offline --index-dir <缓存目录> D8DKS
    echo D8DKS | python tools/fdnext_lookup.py --stdin

退出码：0 = 全部查询强命中（规则/mdb/料号索引/本地目录精确/FDB）；
        1 = 存在需人工确认的查询（各层均未强命中，可能给了模糊候选）；
        2 = 参数错误；其它 >0 = 运行错误。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# 让 "python tools/fdnext_lookup.py" 也能找到 src/ 包
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.fdnext.constants import UNKNOWN  # noqa: E402
from src.fdnext.engine import FdnextEngine  # noqa: E402
from src.fdnext.indexes import FdnextIndexes  # noqa: E402

_LOOKUP_SCHEMA = "chiplookup.fdnext.lookup.v1"

# Flash ID 形态：纯十六进制、偶数位、2~12 位（与规则引擎 shape 一致）
_HEX_ID_RE = re.compile(r"^[0-9A-F]{2,12}$")


# ---------------------------------------------------------------------------
# 形态判别
# ---------------------------------------------------------------------------


def looks_like_flash_id(query: str) -> bool:
    q = query.strip().upper()
    return bool(_HEX_ID_RE.match(q)) and len(q) % 2 == 0


def pick_mode(query: str, forced: Optional[str]) -> str:
    if forced and forced != "auto":
        return forced
    return "flashid" if looks_like_flash_id(query) else "part"


def _upper(text: Any) -> str:
    return str(text or "").strip().upper()


# ---------------------------------------------------------------------------
# 命中判定（沿用 decode.py 语义：identifier 需 vendor 非 Unknown 才算命中）
# ---------------------------------------------------------------------------


def _is_hit(result: Dict[str, Any]) -> bool:
    if result.get("status") != "ok":
        return False
    if result.get("operation") == "part.decode":
        return True
    draft = result.get("_draft") or {}
    vendor = (draft.get("device") or {}).get("vendor")
    return bool(vendor) and vendor != UNKNOWN


def _rules_note(query: str, mode: str) -> str:
    """规则层未覆盖时的人类可读说明。"""
    if mode == "flashid":
        return "纯规则解码未覆盖该 Flash ID（可能需 die profile 之外的索引）"
    return (
        "纯规则解码未覆盖该料号形态；此类输入需 mdb/本地目录人工确认。"
        "官方可命中多因内置索引（如 Micron 顶标码 / SpecTek 标记码）。"
    )


# ---------------------------------------------------------------------------
# 分层检索
# ---------------------------------------------------------------------------


def _catalog_exact(idx: FdnextIndexes, query: str) -> Optional[Dict[str, Any]]:
    for rec in idx.catalog:
        if _upper(rec.get("part_number")) == _upper(query):
            return rec
    return None


def _catalog_summary(rec: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        "part_number", "model", "manufacturer", "type", "capacity",
        "bit_width", "voltage", "speed", "package", "notes",
    )
    return {k: rec.get(k, "") for k in keys}


def _engine_decode(engine: FdnextEngine, query: str, mode: str) -> Dict[str, Any]:
    if mode == "flashid":
        return engine.decode_flash_id(query)
    return engine.decode_part(query)


def _clean_result(result: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in result.items() if v is not None and k != "_draft"}
    return out


def lookup_one(
    engine: FdnextEngine,
    idx: FdnextIndexes,
    query: str,
    *,
    mode: str,
    catalog_top: int = 5,
) -> Dict[str, Any]:
    """对单个查询做分层检索，返回统一结构的结果字典。"""
    sections: List[Dict[str, Any]] = []
    strong_hit = False

    def add(source: str, label: str, data: Any, status: str, note: str = "") -> None:
        sections.append({
            "source": source, "label": label,
            "status": status, "data": data, "note": note,
        })

    if mode == "part":
        # 1) mdb 标记码反查
        marks = idx.lookup_marking(query)
        if marks:
            strong_hit = True
            add("mdb", "标记码(mdb)", marks, "hit")
        else:
            add("mdb", "标记码(mdb)", [], "miss")

        # 2) 料号索引
        vendor = idx.lookup_pn(query)
        if vendor:
            strong_hit = True
            add("pn", "料号索引", {"vendor": vendor}, "hit")
        else:
            add("pn", "料号索引", {}, "miss")

        # 3) 本地目录：先精确，未命中且 mdb/pn 都没中才给模糊候选
        exact = _catalog_exact(idx, query)
        if exact:
            strong_hit = True
            add("catalog", "本地目录", _catalog_summary(exact), "hit")
        else:
            add("catalog", "本地目录", {}, "miss")

        # 4) 规则解码
        result = _engine_decode(engine, query, "part")
        if _is_hit(result):
            strong_hit = True
            add("rules", "规则解码", _clean_result(result), "hit")
        else:
            add("rules", "规则解码", {}, "miss", note=_rules_note(query, "part"))

        # 5) 全空时给模糊候选（仅提示，不计入强命中）
        if not strong_hit:
            fuzzy = idx.fuzzy_catalog(query, top_n=catalog_top)
            if fuzzy:
                add("catalog-fuzzy", "本地目录(模糊)", fuzzy, "hint")
    else:
        # Flash ID：规则解码 + FDB 反查
        result = _engine_decode(engine, query, "flashid")
        if _is_hit(result):
            strong_hit = True
            add("rules", "规则解码", _clean_result(result), "hit")
        else:
            note = ""
            if result.get("status") == "invalid_input":
                note = "Flash ID 长度/字符不合法（应为 2~12 位偶数十进制）"
            else:
                note = _rules_note(query, "flashid")
            add("rules", "规则解码", {}, "miss", note=note)

        rows = idx.lookup_flash(query)
        if rows:
            strong_hit = True
            add("fdb", "Flash已知库", rows, "hit")
        else:
            add("fdb", "Flash已知库", [], "miss")

    return {
        "schema": _LOOKUP_SCHEMA,
        "query": query,
        "normalized": _upper(query),
        "mode": mode,
        "hit": strong_hit,
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# 文本渲染
# ---------------------------------------------------------------------------


def _fmt_bool(v: Any) -> str:
    return "true" if v is True else ("false" if v is False else str(v))


def _catalog_line(rec: Dict[str, Any]) -> str:
    bits = []
    for key in ("manufacturer", "type", "capacity", "bit_width", "speed"):
        if rec.get(key):
            bits.append(str(rec[key]))
    return "%s%s" % (
        rec.get("part_number", ""),
        ("  [" + " | ".join(bits) + "]") if bits else "",
    )


def _rules_line(data: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    device = data.get("device") or {}
    for key in ("partNumber", "identifier", "vendor", "chipKind", "productType"):
        if device.get(key) not in (None, ""):
            lines.append("%s=%s" % (key, device[key]))
    fields = data.get("fields") or {}
    shown = list(fields.items())[:8]
    for k, v in shown:
        lines.append("%s=%s" % (k, _fmt_bool(v)))
    if len(fields) > len(shown):
        lines.append("…共 %d 个字段" % len(fields))
    return lines


def _fdb_line(rec: Dict[str, Any], limit: int = 3) -> str:
    bits = []
    if rec.get("vendor"):
        bits.append(str(rec["vendor"]))
    if rec.get("pn"):
        bits.append(str(rec["pn"]))
    names = rec.get("names") or []
    if names:
        bits.append("; ".join(names[:2]))
    ctrl = rec.get("controllers") or []
    if ctrl:
        shown = ", ".join(ctrl[:limit])
        bits.append("控制器: %s%s" % (shown, "…" if len(ctrl) > limit else ""))
    meta = rec.get("meta") or {}
    if meta:
        bits.append(" ".join("%s=%s" % (k, v) for k, v in meta.items()))
    return " / ".join(bits) if bits else json.dumps(rec, ensure_ascii=False)


def _render_text(lookup: Dict[str, Any], *, verbose: bool) -> List[str]:
    lines = [
        "== %s  [%s] ==" % (lookup["query"], lookup["mode"]),
    ]
    for sec in lookup["sections"]:
        status = {
            "hit": "命中",
            "hint": "候选",
            "miss": "未中",
        }.get(sec["status"], sec["status"])
        head = "  %-14s %s  %s" % (sec["label"], status, sec["source"])
        lines.append(head)
        data = sec.get("data") or {}
        if sec["source"] == "mdb":
            for rec in data:
                lines.append("    %s -> %s" % (rec["vendor"], rec["model"]))
        elif sec["source"] == "pn":
            lines.append("    vendor = %s" % data.get("vendor", ""))
        elif sec["source"] == "catalog":
            lines.append("    " + _catalog_line(data))
        elif sec["source"] == "catalog-fuzzy":
            for rec in data[:5]:
                lines.append("    %-24s score=%.2f (%s)" % (
                    rec.get("part_number", ""), rec.get("score", 0),
                    rec.get("matched_field", "")))
            if len(data) > 5:
                lines.append("    … 共 %d 条候选" % len(data))
        elif sec["source"] == "rules":
            if sec["status"] == "hit":
                for line in _rules_line(data):
                    lines.append("    " + line)
            if verbose and data.get("meta"):
                lines.append("    meta = " + json.dumps(data["meta"], ensure_ascii=False))
        elif sec["source"] == "fdb":
            for rec in data[:5]:
                lines.append("    " + _fdb_line(rec))
            if len(data) > 5:
                lines.append("    … 共 %d 条记录" % len(data))
        if sec.get("note"):
            lines.append("    └ " + sec["note"])
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    queries: List[str] = list(args.query)
    if args.stdin:
        for raw in sys.stdin.read().split():
            if raw.strip():
                queries.append(raw.strip())
    if not queries:
        print("fdnext_lookup: 未提供查询（参数或 --stdin）", file=sys.stderr)
        return 2

    from src.fdnext import load_engine

    engine = load_engine(offline=True)
    idx, src_map = FdnextIndexes.load(
        args.index_dir,
        offline=args.offline,
        refresh=args.refresh,
        catalog_path=args.catalog,
    )

    if args.verbose:
        print("engine: rules=snapshot fdnext-core-3.2.0 (npm @itxtech/fdnext-core@3.2.0)")
        for name, src in src_map.items():
            print("index : %-22s %s" % (name, src))
        print("counts:", json.dumps(idx.counts(), ensure_ascii=False))
        print()

    lookups = [lookup_one(engine, idx, q, mode=args.mode, catalog_top=args.catalog_top)
               for q in queries]

    if args.json:
        if len(lookups) == 1:
            payload: Any = lookups[0]
        else:
            payload = {"results": lookups}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for lookup in lookups:
            for line in _render_text(lookup, verbose=args.verbose):
                print(line)

    weak = [lk for lk in lookups if not lk["hit"]]
    if weak:
        names = ", ".join('"%s"' % lk["query"] for lk in weak)
        print("fdnext_lookup: %d/%d 需人工确认：%s"
              % (len(weak), len(lookups), names), file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ChipLookup 本地统一检索：规则解码 + mdb/料号反查 + 本地目录模糊搜索",
        epilog=(
            "数据分层：\n"
            "  mdb.json       Micron FBGA 顶标码 / SpecTek 标记码 -> 型号（必需）\n"
            "  dram-pn.json   DRAM 料号索引（料号 -> 厂商，必需）\n"
            "  managed-nand-pn.json  受控 NAND 料号索引（可选）\n"
            "  fdb.json       Flash ID -> 控制器/已知料号（可选）\n"
            "  本地 CSV       默认 data/chip_database.csv（--catalog 指定）\n"
            "\n"
            "规则解码默认使用仓库内置快照 fdnext-core-3.2.0，完全离线；\n"
            "索引默认放平台用户缓存 chiplookup/upstream，--index-dir 可指定。\n"
            "纯规则不覆盖的丝印/顶标码（如 D8DKS）由 mdb 层补上；"
            "W25Q/MX25 等由本地目录补上。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="*", help="料号 / 标记码 / Flash ID，可多个")
    parser.add_argument("--mode", choices=("auto", "part", "flashid"),
                        default="auto",
                        help="查询形态；auto 按是否纯十六进制自动判别（默认 auto）")
    parser.add_argument("--stdin", action="store_true",
                        help="从 stdin 读取空白分隔的多个查询")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON（schema chiplookup.fdnext.lookup.v1）")
    parser.add_argument("--verbose", action="store_true",
                        help="额外打印引擎/索引来源与计数")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式：索引只读 --index-dir / 默认缓存目录，不联网")
    parser.add_argument("--refresh", action="store_true",
                        help="忽略缓存新鲜期强制重下索引")
    parser.add_argument("--index-dir", default=None, metavar="DIR",
                        help="上游索引 JSON 缓存目录（默认平台用户缓存 chiplookup/upstream）")
    parser.add_argument("--catalog", default=None, metavar="CSV",
                        help="本地目录 CSV（默认 data/chip_database.csv）")
    parser.add_argument("--catalog-top", type=int, default=5, metavar="N",
                        help="模糊候选最多条数（默认 5）")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (RuntimeError, ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        print("fdnext_lookup: 错误: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
