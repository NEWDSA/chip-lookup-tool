# -*- coding: utf-8 -*-
"""
src/fdnext/postprocess.py
-------------------------
Flash ID / 料号草稿后处理 —— 移植官方 src/flashid/postprocess.ts
（npm @itxtech/fdnext-core@3.2.0 中 createDefaultIdentifierPostprocessor，engine 86）。

官方把该后处理器作为 internalDecodeHooks 的默认成员，解码后先跑这里、再做
derive(enrich)（见 engine.ts applyPartInfoHooks / applyIdentifierInfoHooks）：

    part 路径      : postprocessor.partInfo(info)      -> deriveNandDensityFromDieStack
    identifier 路径 : postprocessor.identifierInfo(info) -> deriveNandDensityFromDieStack

各厂商 identifierInfo 逻辑（字段删除/覆盖语义与上游逐条一致）：
  - micron / intel / spectek : Pr —— Flash ID 前缀表命中则覆盖 die_codename 并删公共字段
  - samsung                 : Nr —— QLC die_codename 归一(SSVx->SSVxQ) + 删公共字段
  - skhynix                 : Fr —— 见下
  - ymtc                    : Ir —— Flash ID 前缀表命中则覆盖 die_codename 并删公共字段
  - partInfo                : samsung QLC 料号同样归一 die_codename

skhynix(Fr) 具体规则（本模块移植重点，ADDE14A2D0E0 依赖）：
  1) byte6 命中 SKHYNIX_BYTE6_DIESTACK 且 density/die_count 均已解出且 die_count>1：
     density = density * die_count
  2) 若 simultaneously_programmed_pages 为有限正数：将其写入 plane_count
  3) byte6 >= 0x80：删除 REMOVE_SKHYNIX_EXT 中的全部扩展字段
     （block_size/blocks_per_lun/pages_per_block/simultaneously_programmed_pages/
       redundant_area_size/timing_mode_async/edo/interleave/cache/ecc_level/
       revision/enterprise/interface_type）
  4) 整 ID 命中 SKHYNIX_SPECIAL_IDS：强制 die_codename=HYV9Q、density=2097152、
     删除 die_count，再删 REMOVE_PUBLIC 公共字段
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .draft import delete_draft_field, draft_field, draft_vendor, set_draft_field
from .identifier_compiler import byte_at
from .postprocess_data import (
    PREFIX_DIE_CODENAME_MICRON,
    PREFIX_DIE_CODENAME_YMTC,
    QLC_SAMSUNG_DIE,
    REMOVE_PUBLIC,
    REMOVE_SKHYNIX_EXT,
    SKHYNIX_BYTE6_DIESTACK,
    SKHYNIX_SPECIAL_IDS,
)


# ----------------------------------------------------------------------
# 草稿拷贝（对应官方 Or/kr：浅拷贝 + 内层字典/数组重建，避免改动原草稿）
# ----------------------------------------------------------------------

def _clone_identifier(draft: Dict[str, Any]) -> Dict[str, Any]:
    """官方 Or：identifier 草稿结构拷贝（identifiers 仅保留 partNumbers）。"""
    out: Dict[str, Any] = {k: v for k, v in draft.items()}
    device = draft.get("device")
    out["device"] = dict(device) if isinstance(device, dict) else {}
    fields = draft.get("fields")
    out["fields"] = dict(fields) if isinstance(fields, dict) else {}
    identifiers = draft.get("identifiers")
    if identifiers:
        parts = identifiers.get("partNumbers") or []
        out["identifiers"] = {"partNumbers": [*parts]}
    controllers = draft.get("controllers")
    if controllers:
        out["controllers"] = [*controllers]
    meta = draft.get("meta")
    if meta:
        out["meta"] = dict(meta)
    warnings = draft.get("warnings")
    if warnings:
        out["warnings"] = [*warnings]
    return out


def _clone_part(draft: Dict[str, Any]) -> Dict[str, Any]:
    """官方 kr：料号草稿结构拷贝（identifiers 含 flashIds+partNumbers、components 逐个拷贝）。"""
    out: Dict[str, Any] = {k: v for k, v in draft.items()}
    device = draft.get("device")
    out["device"] = dict(device) if isinstance(device, dict) else {}
    fields = draft.get("fields")
    out["fields"] = dict(fields) if isinstance(fields, dict) else {}
    identifiers = draft.get("identifiers")
    if identifiers:
        flash_ids = identifiers.get("flashIds") or []
        parts = identifiers.get("partNumbers") or []
        out["identifiers"] = {"flashIds": [*flash_ids], "partNumbers": [*parts]}
    controllers = draft.get("controllers")
    if controllers:
        out["controllers"] = [*controllers]
    components = draft.get("components")
    if components:
        out["components"] = [
            {
                "device": dict(c.get("device")) if isinstance(c.get("device"), dict) else None,
                "fields": dict(c.get("fields")) if isinstance(c.get("fields"), dict) else None,
            }
            for c in components
        ]
    meta = draft.get("meta")
    if meta:
        out["meta"] = dict(meta)
    warnings = draft.get("warnings")
    if warnings:
        out["warnings"] = [*warnings]
    return out


# ----------------------------------------------------------------------
# 基础工具（对应官方 Tr/Er/Dr/Ar/jr）
# ----------------------------------------------------------------------

def _longest_prefix_die_codename(identifier: str, entries: Any) -> Optional[str]:
    """官方 Tr/Er：start*2 处匹配 hex 前缀，取 hex 最长者返回 dieCodename。"""
    text = identifier.upper()
    best: Any = None
    for start, hex_text, die_codename in entries:
        pos = start * 2
        if text[pos:pos + len(hex_text)] == hex_text:
            if best is None or len(hex_text) > len(best[1]):
                best = (die_codename, hex_text)
    return best[0] if best else None


def _field_number(draft: Dict[str, Any], key: str) -> Optional[float]:
    """官方 Dr：有限数值（数字或可解析数字串）才返回。"""
    value = draft_field(draft, key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        import math

        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, str):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        import math

        return number if math.isfinite(number) else None
    return None


def _is_qlc_cell_level(value: Any) -> bool:
    """官方 Ar：cell_level==4 或字符串 '4'/'QLC'（去空白大写）。"""
    if value == 4:
        return True
    if isinstance(value, str) and value.strip().upper() in ("4", "QLC"):
        return True
    return False


def _samsung_qlc_die_codename(draft: Dict[str, Any]) -> Optional[str]:
    """官方 jr：samsung + QLC + die_codename 命中映射表 -> 归一值。"""
    if draft_vendor(draft) != "samsung":
        return None
    if not _is_qlc_cell_level(draft_field(draft, "cell_level")):
        return None
    die_codename = draft_field(draft, "die_codename")
    if not isinstance(die_codename, str):
        return None
    return QLC_SAMSUNG_DIE.get(die_codename.strip().upper())


def _remove_fields(draft: Dict[str, Any], keys: Any) -> None:
    for key in keys:
        delete_draft_field(draft, key)


# ----------------------------------------------------------------------
# 各厂商 identifierInfo（官方 Pr/Nr/Fr/Ir）+ partInfo（官方 Mr）
# ----------------------------------------------------------------------

def _post_micron(draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """官方 Pr：micron/intel/spectek —— 前缀表命中才改写。"""
    identifier = (draft.get("device") or {}).get("identifier")
    if not isinstance(identifier, str):
        return None
    die_codename = _longest_prefix_die_codename(identifier, PREFIX_DIE_CODENAME_MICRON)
    if not die_codename:
        return None
    out = _clone_identifier(draft)
    set_draft_field(out, "die_codename", die_codename)
    _remove_fields(out, REMOVE_PUBLIC)
    return out


def _post_samsung(draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """官方 Nr：samsung identifierInfo。"""
    target = _samsung_qlc_die_codename(draft)
    if not target:
        return None
    out = _clone_identifier(draft)
    set_draft_field(out, "die_codename", target)
    _remove_fields(out, REMOVE_PUBLIC)
    return out


def _post_skhynix(draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """官方 Fr：skhynix identifierInfo（byte6 规则见模块 docstring）。"""
    identifier = (draft.get("device") or {}).get("identifier")
    if not isinstance(identifier, str):
        return None
    changed = False
    out = _clone_identifier(draft)
    byte6 = None
    try:
        byte6 = byte_at(identifier, 6)
    except Exception:
        byte6 = None
    density = _field_number(draft, "density")
    die_count = _field_number(draft, "die_count")
    if byte6 in SKHYNIX_BYTE6_DIESTACK and density is not None and die_count is not None and die_count > 1:
        set_draft_field(out, "density", density * die_count)
        changed = True
    spp = draft_field(draft, "simultaneously_programmed_pages")
    if isinstance(spp, (int, float)) and not isinstance(spp, bool):
        import math

        if math.isfinite(float(spp)) and float(spp) > 0:
            set_draft_field(out, "plane_count", spp)
            changed = True
    if byte6 is not None and byte6 >= 0x80:
        _remove_fields(out, REMOVE_SKHYNIX_EXT)
        changed = True
    if identifier.upper() in SKHYNIX_SPECIAL_IDS:
        set_draft_field(out, "die_codename", "HYV9Q")
        set_draft_field(out, "density", 2097152)
        delete_draft_field(out, "die_count")
        _remove_fields(out, REMOVE_PUBLIC)
        changed = True
    return out if changed else None


def _post_ymtc(draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """官方 Ir：ymtc —— 前缀表命中才改写。"""
    identifier = (draft.get("device") or {}).get("identifier")
    if not isinstance(identifier, str):
        return None
    die_codename = _longest_prefix_die_codename(identifier, PREFIX_DIE_CODENAME_YMTC)
    if not die_codename:
        return None
    out = _clone_identifier(draft)
    set_draft_field(out, "die_codename", die_codename)
    _remove_fields(out, REMOVE_PUBLIC)
    return out


def _post_part_samsung(draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """官方 Mr：samsung partInfo（QLC 料号 die_codename 归一 + 删公共字段）。"""
    target = _samsung_qlc_die_codename(draft)
    if not target:
        return None
    out = _clone_part(draft)
    set_draft_field(out, "die_codename", target)
    _remove_fields(out, REMOVE_PUBLIC)
    return out


# ----------------------------------------------------------------------
# 工厂（官方 Lr / createDefaultIdentifierPostprocessor）
# ----------------------------------------------------------------------

def create_default_identifier_postprocessor() -> Dict[str, Any]:
    """返回 {part_info, identifier_info} 后处理钩子（无内部状态，可复用）。"""

    def identifier_info(draft: Dict[str, Any]) -> Dict[str, Any]:
        vendor = draft_vendor(draft)
        result: Optional[Dict[str, Any]] = None
        if vendor in ("micron", "intel", "spectek"):
            result = _post_micron(draft)
        elif vendor == "samsung":
            result = _post_samsung(draft)
        elif vendor == "skhynix":
            result = _post_skhynix(draft)
        elif vendor == "ymtc":
            result = _post_ymtc(draft)
        return result if result is not None else draft

    def part_info(draft: Dict[str, Any]) -> Dict[str, Any]:
        if draft_vendor(draft) == "samsung":
            result = _post_part_samsung(draft)
            if result is not None:
                return result
        return draft

    return {"part_info": part_info, "identifier_info": identifier_info}


_DEFAULT_POSTPROCESSOR = create_default_identifier_postprocessor()


def apply_identifier_postprocess(draft: Dict[str, Any]) -> Dict[str, Any]:
    """对 identifier 解码草稿执行默认后处理（供 engine 在 enrich/derive 前调用）。"""
    return _DEFAULT_POSTPROCESSOR["identifier_info"](draft)


def apply_part_postprocess(draft: Dict[str, Any]) -> Dict[str, Any]:
    """对料号解码草稿执行默认后处理（供 engine 在 enrich/derive 前调用）。"""
    return _DEFAULT_POSTPROCESSOR["part_info"](draft)
