# -*- coding: utf-8 -*-
"""
src/fdnext/draft.py
-------------------
解码草稿(draft)访问器 —— 移植上游 src/draft.ts。

对草稿 info = { device, fields?, identifiers?, controllers?, ... } 提供统一读写。
注意：上游 setDraftField 只允许写注册字段；Python 侧为保证 decode 中间过程
不受限（规则可能引用非常规键），本模块保持语义：写前仅校验 key 是否注册。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .constants import UNKNOWN
from .field_registry import is_fdnext_field_key


def draft_fields(draft: Dict[str, Any]) -> Dict[str, Any]:
    draft.setdefault("fields", {})
    fields = draft["fields"]
    if not isinstance(fields, dict):
        fields = {}
        draft["fields"] = fields
    return fields


def draft_field(draft: Dict[str, Any], key: str) -> Any:
    fields = draft.get("fields")
    if not isinstance(fields, dict):
        return None
    return fields.get(key)


def set_draft_field(draft: Dict[str, Any], key: str, value: Any) -> None:
    """与上游一致：未注册字段或值为 undefined 时忽略。"""
    if not is_fdnext_field_key(key) or value is None:
        return
    draft_fields(draft)[key] = value


def delete_draft_field(draft: Dict[str, Any], key: str) -> None:
    fields = draft.get("fields")
    if isinstance(fields, dict):
        fields.pop(key, None)


def _merge_dedup(current: List[str], values: List[Any]) -> Optional[List[str]]:
    """上游 mergeDraftStringArray：先 current 后 values，各自按出现顺序去重。"""
    merged: List[str] = []
    seen = set()
    for item in current or []:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            merged.append(text)
    for item in values or []:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            merged.append(text)
    return merged if merged else None


def merge_draft_string_array(
    current: Optional[List[str]], values: Optional[List[Any]]
) -> Optional[List[str]]:
    """上游 mergeDraftStringArray：去重、去空白。"""
    return _merge_dedup(current or [], values or [])


def draft_vendor(draft: Dict[str, Any]) -> str:
    vendor = (draft.get("device") or {}).get("vendor")
    if isinstance(vendor, str) and vendor.strip():
        return vendor.strip()
    return UNKNOWN


def draft_part_number(draft: Dict[str, Any]) -> str:
    return (draft.get("device") or {}).get("partNumber")


def draft_identifier(draft: Dict[str, Any]) -> str:
    return (draft.get("device") or {}).get("identifier")


def known_draft_number(value: Any) -> Optional[float]:
    """上游 knownDraftNumber：>0 有限数才算。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and _isfinite(value) and value > 0:
        return float(value)
    return None


def _isfinite(value: float) -> bool:
    import math

    return math.isfinite(value)


def draft_density(draft: Dict[str, Any]) -> Optional[float]:
    """上游 draftDensity：dram 优先 dram_density，其次 storage_density/density。"""
    preferred = (
        draft_field(draft, "dram_density")
        if (draft.get("device") or {}).get("chipKind") == "dram"
        else None
    )
    return (
        known_draft_number(preferred)
        or known_draft_number(draft_field(draft, "storage_density"))
        or known_draft_number(draft_field(draft, "density"))
        or known_draft_number(draft_field(draft, "dram_density"))
    )


MANAGED_PRODUCT_TYPES = ("emmc", "ufs", "sata", "sas", "nvme", "emcp", "umcp", "e2nand", "e3nand")


def managed_product_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in MANAGED_PRODUCT_TYPES else None


def dram_product_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    import re

    normalized = re.sub(r"[^a-z0-9]+", "", value.strip().lower())
    return normalized if re.match(r"^(?:sdr|lpsdr|lpddr|ddr|gddr|rldram)", normalized) else None


def chip_kind_from_legacy_type(value: Any) -> Optional[str]:
    """把旧 type 字段归类为 chipKind（dram/raw_nand/managed_nand）。"""
    if not isinstance(value, str):
        return None
    import re

    normalized = re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()
    if normalized == "dram":
        return "dram"
    if normalized == "nand":
        return "raw_nand"
    if managed_product_type(normalized):
        return "managed_nand"
    if dram_product_type(normalized):
        return "dram"
    return None


def normalize_draft_controllers(draft: Dict[str, Any]) -> None:
    merged = merge_draft_string_array([], draft.get("controllers"))
    draft["controllers"] = merged
