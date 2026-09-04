# -*- coding: utf-8 -*-
"""
src/fdnext/field_normalization.py
---------------------------------
解码草稿的字段归一化/修剪 —— 移植上游 src/engine/field-normalization.ts。

包含：
    - get_human_readable_density / parse_die_density_mbit
    - canonical_nand_die_profile_key（按厂商 + cell_level 规范化 die profile key）
    - apply_dram_classification / apply_dram_public_type
    - prune_redundant_fields（去重冗余字段，贴近站点展示）
    - is_known_classification_value / collect_decoder_profile_tables
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

from .constants import UNKNOWN
from .draft import (
    delete_draft_field,
    draft_field,
    draft_fields,
    draft_vendor,
    set_draft_field,
)

GBIT_TO_MBIT = 1024
TBIT_TO_MBIT = GBIT_TO_MBIT * GBIT_TO_MBIT

_GBIT_OR_TBIT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([gmt])b(?:it)?\s*$", re.IGNORECASE)

# 上游 vendorAliases
_VENDOR_ALIASES: Dict[str, List[str]] = {
    "biwin": ["biwin"],
    "esmt": ["esmt", "elite semiconductor"],
    "etron": ["etron", "etron technology"],
    "gigadevice": ["gigadevice", "giga device", "gd", "兆易创新"],
    "intel": ["intel"],
    "issi": ["issi"],
    "kingston": ["kingston"],
    "kioxia": ["kioxia", "toshiba"],
    "longsys": ["longsys", "foresee", "lexar"],
    "micron": ["micron"],
    "samsung": ["samsung"],
    "siliconmotion": ["silicon motion", "smi"],
    "sndk": ["sandisk", "western digital", "wd"],
    "skhynix": ["sk hynix", "skhynix"],
    "spectek": ["spectek"],
    "winbond": ["winbond"],
    "ymtc": ["ymtc"],
}


def get_human_readable_density(density: float, use_byte: bool = False) -> str:
    unit = ["MB", "GB", "TB"] if use_byte else ["Mb", "Gb", "Tb"]
    numeric = density / 8 if use_byte else density
    idx = 0
    while numeric >= 1024 and idx + 1 < len(unit):
        numeric /= 1024
        idx += 1
    return "%s%s" % (numeric, unit[idx])


def parse_die_density_mbit(value: Any) -> Optional[float]:
    if not isinstance(value, str):
        return None
    match = _GBIT_OR_TBIT_RE.match(value)
    if not match:
        return None
    numeric = float(match.group(1))
    unit = match.group(2).lower()
    if not math.isfinite(numeric) or numeric <= 0:
        return None
    if unit == "m":
        return round(numeric)
    if unit == "g":
        return round(numeric * GBIT_TO_MBIT)
    if unit == "t":
        if 1.32 < numeric < 1.34:
            return 1365 * GBIT_TO_MBIT
        return round(numeric * TBIT_TO_MBIT)
    return None


def normalize_info_text(value: Any) -> str:
    """normalizeInfoText：小写、非字母数字转空格、特殊缩略词规整。"""
    if not isinstance(value, str):
        return ""
    text = value.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"\be\s+mmc\b", "emmc", text)
    text = re.sub(r"\be\s+mcp\b", "emcp", text)
    text = re.sub(r"\bu\s+mcp\b", "umcp", text)
    text = re.sub(r"\bv(?=\d)", "", text)
    text = re.sub(r"\s+", " ", text.strip())
    return text


def aliases_for_vendor(vendor: Any) -> List[str]:
    if not isinstance(vendor, str):
        return []
    return _VENDOR_ALIASES.get(vendor, [vendor])


def remove_vendor_prefix(value: str, vendor: Any) -> str:
    normalized = normalize_info_text(value)
    for alias in aliases_for_vendor(vendor):
        alias_text = normalize_info_text(alias)
        if alias_text and normalized.startswith(alias_text + " "):
            normalized = normalized[len(alias_text) + 1:]
            break
    return normalized


def part_type_text(info: Dict[str, Any]) -> str:
    device = info.get("device") or {}
    return normalize_info_text(
        device.get("productType")
        or draft_field(info, "product_type")
        or draft_field(info, "dram_type")
        or device.get("chipKind")
    )


def is_managed_nand_type(info: Dict[str, Any]) -> bool:
    device = info.get("device") or {}
    return (
        device.get("chipKind") == "managed_nand"
        or part_type_text(info)
        in ("emmc", "ufs", "sata", "sas", "nvme", "emcp", "umcp", "e2nand", "e3nand")
    )


def is_nand_die_profile_type(info: Dict[str, Any]) -> bool:
    device = info.get("device") or {}
    return (
        device.get("chipKind") == "raw_nand"
        or is_managed_nand_type(info)
        or device.get("idScheme") == "nand.flash_id"
    )


def _is_redundant_managed_family(value: Any, info: Dict[str, Any], extra: Dict[str, Any]) -> bool:
    text = normalize_info_text(value)
    if not text:
        return False
    return text == part_type_text(info) or text == normalize_info_text(extra.get("product_family"))


def _matches_die_codename(value: Any, info: Dict[str, Any]) -> bool:
    text = normalize_info_text(value)
    die_codename = normalize_info_text(draft_field(info, "die_codename"))
    return bool(text and die_codename and text == die_codename)


def _is_redundant_nand_technology(value: Any, info: Dict[str, Any], extra: Dict[str, Any]) -> bool:
    text = normalize_info_text(value)
    if not text:
        return False
    if _matches_die_codename(value, info) or text == normalize_info_text(extra.get("generation_info")):
        return True
    die_codename = normalize_info_text(draft_field(info, "die_codename"))
    return text == "bics flash" and "bics" in die_codename


def public_dram_type(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    type_text = re.sub(r"\s+(?:sdram|sgram)$", "", value.strip(), flags=re.IGNORECASE)
    return type_text if type_text else None


def is_ddr_family_dram_type(value: Any) -> bool:
    type_text = normalize_info_text(value)
    return bool(re.fullmatch(r"(?:ddr[2-5]?|lpddr[2-5]?x?|gddr[2-7]?x?)(?: sdram| sgram)?", type_text))


def is_plain_ddr_dram_type(value: Any) -> bool:
    type_text = normalize_info_text(value)
    return bool(re.fullmatch(r"ddr[2-5]?(?: sdram)?", type_text))


def is_known_classification_value(value: Any) -> bool:
    """上游 isKnownClassificationValue：null/-1/UNKNOWN 视为未知。"""
    if value is None or value == -1 or value == UNKNOWN:
        return False
    if isinstance(value, str):
        normalized = normalize_info_text(value)
        return bool(normalized and normalized != normalize_info_text(UNKNOWN))
    return True


def collect_decoder_profile_tables(
    explicit: Optional[Dict[str, Any]],
    decoders: List[Any],
) -> Dict[str, Any]:
    tables: Dict[str, Any] = dict(explicit or {})
    for decoder in decoders:
        for table_name, table in (getattr(decoder, "profile_tables", None) or {}).items():
            tables.setdefault(table_name, table)
    return tables


def normalize_nand_cell_level(value: Any) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return {1: "SLC", 2: "MLC", 3: "TLC", 4: "QLC"}.get(value, "")
    if not isinstance(value, str):
        return ""
    normalized = value.strip().upper()
    if normalized == "1":
        return "SLC"
    if normalized == "2":
        return "MLC"
    if normalized == "3":
        return "TLC"
    if normalized == "4":
        return "QLC"
    for level in ("SLC", "MLC", "TLC", "QLC"):
        if re.search(r"\b%s\b" % level, normalized):
            return level
    return ""


# 上游 kioxiaSandiskBicsProfileByCell
_KIOXIA_SANDISK_BICS_BY_CELL: Dict[str, Dict[str, str]] = {
    "KBICS2": {"MLC": "KBiCS2M"},
    "KBICS3": {"MLC": "KBiCS3M"},
    "KBICS4": {"SLC": "KBiCS4S", "MLC": "KBiCS4M", "QLC": "KBiCS4Q"},
    "KBICS4.5": {"MLC": "KBiCS4.5M", "QLC": "KBiCS4.5Q"},
    "KBICS5": {"MLC": "KBiCS5M", "QLC": "KBiCS5Q"},
    "KBICS6": {"MLC": "KBiCS6M", "QLC": "KBiCS6Q"},
    "SBICS2": {"MLC": "SBiCS2M"},
    "SBICS3": {"MLC": "SBiCS3M"},
    "SBICS4": {"MLC": "SBiCS4M", "QLC": "SBiCS4Q"},
    "SBICS4.5": {"MLC": "SBiCS4.5M", "QLC": "SBiCS4.5Q"},
    "SBICS5": {"MLC": "SBiCS5M", "QLC": "SBiCS5Q"},
    "SBICS6": {"MLC": "SBiCS6M", "QLC": "SBiCS6Q"},
}

_SS_VARIANT_RE = re.compile(r"^SS(?:14|16|19|21|27)$")
_SSV_VARIANT_RE = re.compile(r"^SSV[1-9](?:HS)?$")


def canonical_nand_die_profile_key(
    die_codename: str,
    info: Dict[str, Any],
    has_profile_key: Any = None,
) -> str:
    """上游 canonicalNandDieProfileKey：厂商 + cell_level 决定 key 是否补 M/S/Q 后缀。"""
    key = die_codename.strip()
    upper = key.upper()
    cell_level = normalize_nand_cell_level(draft_field(info, "cell_level"))

    def known_profile(candidate: str) -> Optional[str]:
        if has_profile_key is None or has_profile_key(candidate):
            return candidate
        return None

    vendor = draft_vendor(info)
    if vendor in ("kioxia", "sndk"):
        bics_profile = _KIOXIA_SANDISK_BICS_BY_CELL.get(upper, {}).get(cell_level)
        if bics_profile and known_profile(bics_profile):
            return bics_profile
    if vendor == "skhynix":
        if cell_level == "MLC":
            candidate = upper if upper.endswith("M") else upper + "M"
            if known_profile(candidate):
                return candidate
        if cell_level == "QLC":
            candidate = upper if upper.endswith("Q") else upper + "Q"
            if known_profile(candidate):
                return candidate
    if vendor == "samsung":
        if cell_level == "MLC":
            if _SS_VARIANT_RE.match(upper) or _SSV_VARIANT_RE.match(upper):
                found = known_profile(upper + "M")
                return found if found else key
        if cell_level == "QLC":
            if _SSV_VARIANT_RE.match(upper):
                found = known_profile(upper + "Q")
                return found if found else key
        if cell_level == "SLC":
            if _SS_VARIANT_RE.match(upper) or _SSV_VARIANT_RE.match(upper):
                found = known_profile(upper + "S")
                return found if found else key
    return key


def _has_dram_stack_layout_option(value: Any) -> bool:
    return bool(re.search(r"\bstack(?:ed)?\b", normalize_info_text(value)))


def apply_dram_classification(info: Dict[str, Any]) -> None:
    """applyDramClassification：DDR 默认补 die_count/cs_count=1（无 stack 选项时）。"""
    device = info.get("device") or {}
    if device.get("chipKind") != "dram":
        return
    extra = draft_fields(info)
    has_explicit_dram_die_count = is_known_classification_value(extra.get("dram_die_count"))
    has_explicit_cs_count = is_known_classification_value(extra.get("cs_count"))
    has_stack_layout_option = _has_dram_stack_layout_option(extra.get("special_option"))
    default_die_classification = is_ddr_family_dram_type(extra.get("dram_type"))
    default_cs_classification = is_plain_ddr_dram_type(extra.get("dram_type"))
    meta = info.get("meta") or {}
    # 上游用 ?? 空值合并：meta 显式 false 时不得回退到 package 判断
    recognized = meta.get("dramTopologyTokenRecognized")
    topology_token_recognized = (
        recognized if recognized is not None else is_known_classification_value(extra.get("package"))
    )
    if (not default_die_classification and not default_cs_classification) or not topology_token_recognized:
        return
    if (
        not has_explicit_cs_count
        and not has_stack_layout_option
        and default_die_classification
        and not is_known_classification_value(draft_field(info, "dram_die_count"))
    ):
        set_draft_field(info, "dram_die_count", 1)
    if (
        not has_explicit_dram_die_count
        and default_cs_classification
        and not is_known_classification_value(draft_field(info, "cs_count"))
    ):
        set_draft_field(info, "cs_count", 1)


def apply_dram_public_type(info: Dict[str, Any]) -> None:
    device = info.get("device") or {}
    if device.get("chipKind") != "dram":
        return
    extra = draft_fields(info)
    type_text = public_dram_type(extra.get("dram_type"))
    if type_text:
        set_draft_field(info, "dram_type", type_text)


def prune_redundant_fields(info: Dict[str, Any]) -> None:
    """pruneRedundantFields：删除与既有字段重复的冗余展示字段。"""
    extra = info.get("fields")
    if not isinstance(extra, dict):
        return

    product_version = extra.get("product_version")
    storage_interface = extra.get("storage_interface")
    product_family = extra.get("product_family")
    managed_nand_type = is_managed_nand_type(info)

    if _is_redundant_managed_family(extra.get("managed_family"), info, extra):
        delete_draft_field(info, "managed_family")
    if managed_nand_type and _matches_die_codename(extra.get("generation_info"), info):
        delete_draft_field(info, "generation_info")
    if managed_nand_type and _is_redundant_nand_technology(extra.get("nand_technology"), info, extra):
        delete_draft_field(info, "nand_technology")

    product_version_text = normalize_info_text(product_version)
    if product_version_text and (
        product_version_text == normalize_info_text(storage_interface)
        or product_version_text == part_type_text(info)
    ):
        delete_draft_field(info, "product_version")

    product_family_text = remove_vendor_prefix(str(product_family or ""), draft_vendor(info))
    if product_family_text and (
        product_family_text == normalize_info_text(product_version)
        or product_family_text == normalize_info_text(storage_interface)
        or product_family_text == part_type_text(info)
    ):
        delete_draft_field(info, "product_family")

    if managed_nand_type and normalize_info_text(storage_interface) == part_type_text(info):
        delete_draft_field(info, "storage_interface")

    if is_nand_die_profile_type(info):
        delete_draft_field(info, "process_node")
