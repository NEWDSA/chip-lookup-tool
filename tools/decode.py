# -*- coding: utf-8 -*-
"""
tools/decode.py
---------------
纯 Python 料号 / Flash ID 解码 CLI —— 包装 src.fdnext 引擎（无 Node.js 依赖）。

输入两种形态（--mode 可强制指定，默认 auto 自动判别）：
    1) 料号   ：如 MT29F512G08EBHAFJ4 / H5AN8G8NCJR / K9F2G08U0C
    2) Flash ID：十六进制字节串，如 2C84044BA900 / 983A98A376E0 / ADDE14A2D0E0

输出：
    - 默认：人类可读的分组文本
    - --json：结构化 JSON，schema 为 chiplookup.fdnext.decode.v1
      单个查询 -> 直接输出该条 result；多个查询 -> {"results": [{mode, result}, ...]}

规则来源：仓库内置快照 src/fdnext/data/fdnext-core-3.2.0/
（对应官方 npm @itxtech/fdnext-core@3.2.0 / engine 86），默认完全离线。

边界说明（M1 设计边界）：
    本引擎只做「规则解码」，不内置 mdb 索引。以下输入在官方站点能命中，
    靠的是 mdb（丝印/标记码 -> 型号）反查索引，本工具一律返回 not_found：
        D8DKS / W25Q128JVSSIQ / W25Q64JVSSIQ
        MX25L12835FMI-10G / MX25L6433FZNI-08G
    此类请改用 ChipLookup 自带库检索（人工确认）或上游 fm.itxtech.org/parts。

用法：
    python tools/decode.py MT29F512G08EBHAFJ4
    python tools/decode.py --json 2C84044BA900
    python tools/decode.py H5AN8G8NCJR --verbose
    echo K9F2G08U0C | python tools/decode.py --stdin

退出码：0 = 全部命中；1 = 存在未命中（not_found / invalid_input / 规则未覆盖）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

# 让 "python tools/decode.py" 也能找到 src/ 包
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.fdnext import load_engine  # noqa: E402
from src.fdnext.constants import UNKNOWN  # noqa: E402

# 官方 mdb 索引能命中、但规则解码不覆盖的样本（详见模块 docstring）
KNOWN_MDB_BOUNDARY = (
    "D8DKS",
    "W25Q128JVSSIQ",
    "W25Q64JVSSIQ",
    "MX25L12835FMI-10G",
    "MX25L6433FZNI-08G",
)

_HEX_ID_RE = re.compile(r"^[0-9A-F]{2,12}$")  # Flash ID 形如纯十六进制（2~12 位偶数）


# ---------------------------------------------------------------------------
# 输入判别
# ---------------------------------------------------------------------------


def looks_like_flash_id(query: str) -> bool:
    """auto 模式启发式：纯十六进制短串按 Flash ID 处理。"""
    q = query.strip().upper()
    if not _HEX_ID_RE.match(q):
        return False
    # 既排除料号又限定偶数位长度
    return len(q) % 2 == 0


def pick_mode(query: str, forced: Optional[str]) -> str:
    if forced and forced != "auto":
        return forced
    return "flashid" if looks_like_flash_id(query) else "part"


# ---------------------------------------------------------------------------
# 命中判定
# ---------------------------------------------------------------------------


def _draft(result: Dict[str, Any]) -> Dict[str, Any]:
    return result.get("_draft") or {}


def is_hit(result: Dict[str, Any]) -> bool:
    """判断本次解码是否真正命中规则。

    part.decode：status=ok 即命中（否则引擎返回 not_found / invalid_input）。
    identifier.decode：status 恒为 ok，需 vendor 不再是 Unknown 才算命中。
    """
    if result.get("status") != "ok":
        return False
    if result.get("operation") == "part.decode":
        return True
    device = _draft(result).get("device") or {}
    vendor = device.get("vendor")
    return bool(vendor) and vendor != UNKNOWN


def boundary_note(query: str) -> str:
    """命中 known mdb 边界时给出针对性提示（同时作为 --help epilog 素材）。"""
    upper = query.upper()
    if upper in KNOWN_MDB_BOUNDARY:
        return (
            "%s 属于 mdb 索引类输入（丝印/标记码或索引料号），"
            "本工具是纯规则解码、未内置 mdb 索引，返回 not_found 属预期；"
            "建议用 ChipLookup 库检索或 fm.itxtech.org/parts 确认。" % upper
        )
    return (
        "未命中任何解码规则。若输入的是丝印/标记码（如 W25Q/MX25/D8DK* 等短码），"
        "需 mdb 索引支持（本工具未内置，M1 边界）；请人工确认或用上游站点检索。"
    )


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def _fmt_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _render_text(result: Dict[str, Any], *, verbose: bool) -> List[str]:
    lines: List[str] = []
    op = result.get("operation")
    status = result.get("status")
    query = result.get("query", "")
    lines.append("== %s  [%s] ==" % (query, op))
    lines.append("  status    : %s" % status)
    lines.append("  normalized: %s" % result.get("normalized", ""))

    if status != "ok":
        lines.append("  note      : " + boundary_note(query))
        return lines

    if not is_hit(result):  # identifier：status 恒 ok，vendor 仍为 Unknown 属规则未覆盖
        lines.append("  note      : " + boundary_note(query))
        return lines

    draft = _draft(result)
    device = draft.get("device") or {}
    for key in (
        "partNumber", "identifier", "vendor", "chipKind", "productType",
        "domain", "idScheme",
    ):
        if key in device and device.get(key) not in (None, ""):
            lines.append("  %-10s : %s" % (key, _fmt_value(device.get(key))))

    fields = draft.get("fields") or {}
    if fields:
        lines.append("  fields:")
        width = max(len(k) for k in fields)
        for key in sorted(fields):
            lines.append("    %-*s = %s" % (width, key, _fmt_value(fields.get(key))))

    if verbose:
        meta = draft.get("meta") or {}
        if meta:
            lines.append("  meta      : " + json.dumps(meta, ensure_ascii=False))
        for key in ("identifiers", "controllers", "components"):
            value = draft.get(key)
            if value:
                lines.append("  %-10s : %s" % (key, json.dumps(value, ensure_ascii=False)))
        if draft.get("warnings"):
            lines.append("  warnings  : " + json.dumps(draft["warnings"], ensure_ascii=False))
    return lines


def _clean_result(result: Dict[str, Any], *, include_draft: bool) -> Dict[str, Any]:
    out = {k: v for k, v in result.items() if v is not None}
    if not include_draft:
        out.pop("_draft", None)
    return out


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
        print("decode: 未提供查询输入（参数或 --stdin）", file=sys.stderr)
        return 2

    engine = load_engine(args.cache_dir, offline=args.offline, refresh=args.refresh)
    if args.verbose:
        print("engine: rules=snapshot fdnext-core-3.2.0 (npm @itxtech/fdnext-core@3.2.0)")
        print("mode  : %s" % (args.mode or "auto"))

    results: List[Dict[str, Any]] = []
    for query in queries:
        mode = pick_mode(query, args.mode)
        result = (
            engine.decode_flash_id(query)
            if mode == "flashid"
            else engine.decode_part(query)
        )
        results.append(result)

    bad = 0
    if args.json:
        payload: Any
        if len(results) == 1:
            payload = _clean_result(results[0], include_draft=args.draft)
        else:
            payload = {
                "results": [
                    {
                        "mode": pick_mode(r.get("query", ""), args.mode),
                        "result": _clean_result(r, include_draft=args.draft),
                    }
                    for r in results
                ]
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        bad = sum(1 for r in results if not is_hit(r))
    else:
        for r in results:
            for line in _render_text(r, verbose=args.verbose):
                print(line)
            if not is_hit(r):
                bad += 1
            print()

    if bad:
        print("decode: %d/%d 未命中" % (bad, len(results)), file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ChipLookup 料号 / Flash ID 解码 CLI（纯 Python，无 Node.js）",
        epilog=(
            "规则来源：src/fdnext/data/fdnext-core-3.2.0 快照（默认离线，对应 "
            "npm @itxtech/fdnext-core@3.2.0 / engine 86）。\n\n"
            "已知边界（mdb 索引类，规则解码返回 not_found）：\n  "
            + "\n  ".join(KNOWN_MDB_BOUNDARY)
            + "\n\n此类建议用 ChipLookup 库检索或 fm.itxtech.org/parts 人工确认。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="*", help="料号或 Flash ID，可多个")
    parser.add_argument("--mode", choices=("auto", "part", "flashid"),
                        default="auto",
                        help="解码形态；auto 按是否纯十六进制自动判别（默认 auto）")
    parser.add_argument("--stdin", action="store_true",
                        help="从 stdin 读取空白分隔的多个查询")
    parser.add_argument("--json", action="store_true",
                        help="输出 JSON（schema chiplookup.fdnext.decode.v1）")
    parser.add_argument("--draft", action="store_true",
                        help="JSON 输出时附带内部 _draft 字段")
    parser.add_argument("--verbose", action="store_true",
                        help="额外打印 meta / identifiers / controllers")
    parser.add_argument("--cache-dir", default=None,
                        help="规则缓存目录（快照存在时忽略；默认平台缓存）")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式：只读缓存（快照存在时默认即离线）")
    parser.add_argument("--refresh", action="store_true",
                        help="忽略缓存新鲜期强制重下（仅无快照回退时有效）")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        print("decode: 错误: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
