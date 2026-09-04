# -*- coding: utf-8 -*-
"""
tools/fdnext_verify.py
----------------------
对拍校验：Python 引擎解码结果 vs 官方 fdnext-core 生成的 golden。

golden 由开发机一次性生成（Node + @itxtech/fdnext-core），见
tools/golden/fdnext-core-*.json；本校验工具是纯标准库，可在无 Node 环境复跑。

用法:
    python tools/fdnext_verify.py [--cache-dir DIR] [--offline]
                                  [--golden tools/golden/fdnext-core-86.json]

退出码：0 全部一致；1 存在差异。

对比范围：
    - status（ok/not_found 对齐）
    - device.vendor / chipKind / productType / partNumber（料号）与 identifier（Flash ID）
    - fields 全量（键集合 + 值）
    - meta.ruleId / meta.nandDieProfileKey / meta.lookupPartNumbers

已知边界（不计入差异）：
    - identifiers/controllers：官方由 fdb/mdb 合并而来，本项目不做索引（M1 边界）
    - 官方独有 hooks 阶段（如 FieldProfile / capabilities 等 meta）为展示用元数据
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.fdnext import load_engine  # noqa: E402

_META_KEYS_TO_COMPARE = ("ruleId", "nandDieProfileKey", "lookupPartNumbers")


def _eq(a: Any, b: Any) -> bool:
    """数值 / bool / 文本 宽松相等。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def _compare_draft(query: str, golden_draft: Dict[str, Any], py_draft: Dict[str, Any]) -> List[str]:
    """返回差异说明列表（空 = 一致）。"""
    issues: List[str] = []
    gd = golden_draft.get("device") or {}
    pd = py_draft.get("device") or {}
    for key in ("vendor", "chipKind", "productType"):
        if gd.get(key) != pd.get(key):
            issues.append("device.%s: golden=%r python=%r" % (key, gd.get(key), pd.get(key)))
    if "partNumber" in gd and gd.get("partNumber") != pd.get("partNumber"):
        issues.append("device.partNumber: golden=%r python=%r" % (gd.get("partNumber"), pd.get("partNumber")))
    if "identifier" in gd and gd.get("identifier") != pd.get("identifier"):
        issues.append("device.identifier: golden=%r python=%r" % (gd.get("identifier"), pd.get("identifier")))

    gf = golden_draft.get("fields") or {}
    pf = py_draft.get("fields") or {}
    for key in sorted(set(gf) | set(pf)):
        gv, pv = gf.get(key), pf.get(key)
        if key not in pf:
            issues.append("fields.%s: golden=%r python=<missing>" % (key, gv))
        elif key not in gf:
            issues.append("fields.%s: golden=<missing> python=%r" % (key, pv))
        elif not _eq(gv, pv):
            issues.append("fields.%s: golden=%r python=%r" % (key, gv, pv))

    gm = golden_draft.get("meta") or {}
    pm = py_draft.get("meta") or {}
    for key in _META_KEYS_TO_COMPARE:
        if key in gm and not _eq(gm.get(key), pm.get(key)):
            issues.append("meta.%s: golden=%r python=%r" % (key, gm.get(key), pm.get(key)))
    return issues


def _run_group(
    engine: Any,
    entries: Dict[str, Dict[str, Any]],
    *,
    is_part: bool,
) -> Tuple[int, int, List[str]]:
    checked = 0
    issues_found = 0
    detail: List[str] = []
    for query, expected in entries.items():
        py = engine.decode_part(query) if is_part else engine.decode_flash_id(query)
        expected_status = expected["status"]
        actual_status = py["status"]
        if expected_status != actual_status:
            issues_found += 1
            detail.append("[%s] status: golden=%s python=%s" % (query, expected_status, actual_status))
            continue
        if expected_status != "ok":
            checked += 1
            continue
        golden_draft = expected.get("draft")
        py_draft = py.get("_draft")
        if py_draft is None:
            issues_found += 1
            detail.append("[%s] python 无 draft" % query)
            continue
        checked += 1
        issues = _compare_draft(query, golden_draft, py_draft)
        if issues:
            issues_found += 1
            detail.append("[%s] %d 处差异" % (query, len(issues)))
            for issue in issues[:12]:
                detail.append("    " + issue)
    return checked, issues_found, detail


def main(argv: Optional[List[str]] = None) -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Python fdnext 解码 vs 官方 golden 对拍")
    ap.add_argument("--golden", default=os.path.join(here, "golden", "fdnext-core-86.json"))
    ap.add_argument("--cache-dir", default=None, help="规则缓存目录（默认平台缓存）")
    ap.add_argument("--offline", action="store_true", help="只读缓存，不联网")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    with open(args.golden, "r", encoding="utf-8") as f:
        golden = json.load(f)
    engine = load_engine(args.cache_dir, offline=args.offline)
    if not args.quiet:
        print("golden engine: %s" % golden.get("engine"))

    parts_checked, parts_bad, part_detail = _run_group(engine, golden["parts"], is_part=True)
    ids_checked, ids_bad, id_detail = _run_group(engine, golden["flashIds"], is_part=False)

    if not args.quiet:
        for line in part_detail + id_detail:
            print(line)
        print("-" * 60)
    print("part : %d 料号，%d 不一致" % (parts_checked, parts_bad))
    print("flash: %d ID，%d 不一致" % (ids_checked, ids_bad))
    return 1 if (parts_bad + ids_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
