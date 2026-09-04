# -*- coding: utf-8 -*-
"""
src/fdnext/normalize.py
-----------------------
输入规范化 —— 移植上游 src/utils/normalize.ts（+ string.ts removeChars）。

这些函数是「dispatch trie / engine 顶层 decode」的输入处理基准，
与 spec 内 normalize steps 是两个不同的层级。
"""

from __future__ import annotations

import re

# PN_REMOVALS（注意：normalizePartNumber 会去掉 '.'，但保留 '-' 与 ':'）
_PN_REMOVALS = [" ", ",", "&", ".", "|"]
_PN_TOKEN_SEPARATORS = [":", "-"]

_H25_X_RE = re.compile(r"^(H25[A-Z0-9]+)-X([0-9A-Z]+)(?:-([A-Z0-9]+))?$")


def remove_chars(input_str: str, chars) -> str:
    out = input_str
    for char in chars or []:
        out = out.replace(char, "")
    return out


def _normalize_part_number_alias(part_number: str) -> str:
    """normalizePartNumberAlias：EMT29F 前缀替换 + H25…-X… 接线写法折叠。"""
    if part_number.startswith("EMT29F"):
        part_number = "MT29F" + part_number[len("EMT29F"):]
    match = _H25_X_RE.match(part_number)
    if match:
        base, suffix, tail = match.group(1), match.group(2), match.group(3)
        return "%sX%s%s" % (base, suffix, tail or "")
    return part_number


def normalize_part_number(part_number: str) -> str:
    return _normalize_part_number_alias(
        remove_chars(part_number.upper().replace("\ufffd", "-"), _PN_REMOVALS)
    )


def normalize_part_number_token_key(part_number: str) -> str:
    return remove_chars(normalize_part_number(part_number), _PN_TOKEN_SEPARATORS)


def normalize_flash_id(id_str: str) -> str:
    return re.sub(r"[^0-9A-F]", "", id_str.upper())


def pad_flash_id(id_str: str, length: int = 12) -> str:
    if len(id_str) >= length:
        return id_str
    return id_str + "0" * (length - len(id_str))
