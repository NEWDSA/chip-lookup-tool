# -*- coding: utf-8 -*-
"""
src/fdnext/table.py
-------------------
DecodeTable 规范化 —— 忠实移植上游 src/decodepack/table.ts 的语义。

DecodeTable 的三种原始形态：
    1) dict（Record）            -> 原样作为查表
    2) 字符串数组                -> 每一项 "key" 映射到自身 "key"
    3) 别名条目数组（{keys, value?}）
       - 有 value 字段 -> keys 中的每个 key 映射到 value
       - 无 value 字段 -> keys 中的每个 key 映射到自身 key
非法的条目直接跳过（与上游一致）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Union


def normalize_decode_table(table: Union[Dict[str, Any], List[Any], None]) -> Dict[str, Any]:
    """把一个 DecodeTable 规范化成普通 dict。"""
    if not table:
        return {}
    if not isinstance(table, list):
        # 非数组即 Record（上游还有 isRecord 守卫，JSON 下等价于 dict）
        return dict(table) if isinstance(table, dict) else {}
    out: Dict[str, Any] = {}
    for entry in table:
        if isinstance(entry, str):
            out[entry] = entry
            continue
        if not isinstance(entry, dict):
            continue
        keys = entry.get("keys")
        if not isinstance(keys, list):
            continue
        has_value = "value" in entry
        for key in keys:
            if not isinstance(key, str):
                continue
            out[key] = entry["value"] if has_value else key
    return out


def normalize_decode_tables(
    tables: Union[Dict[str, Union[Dict[str, Any], List[Any]]], None]
) -> Dict[str, Dict[str, Any]]:
    """批量规范化 {表名: DecodeTable}。"""
    out: Dict[str, Dict[str, Any]] = {}
    for name, table in (tables or {}).items():
        out[name] = normalize_decode_table(table)
    return out
