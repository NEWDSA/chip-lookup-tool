# -*- coding: utf-8 -*-
"""
src/fdnext/engine.py
--------------------
顶层解码引擎 —— 移植上游 src/engine.ts 的「规则解码」子集。

上游完整引擎还依赖 mdb/fdb/catalog/FBGA 等索引（见 M1 边界，本项目不做），
因此这里实现等价链路：

    decode_part(query):
        normalize_part_number -> dispatch trie 候选（按 priority 降序）
        -> decoder.match -> decode -> normalize_part_draft（补默认 device/数组字段）
        -> applyPartInfoHooks（die profile enrich + die_stack 推导 density）
        -> applyDramClassification / applyDramPublicType / pruneRedundantFields
        -> ok

    decode_flash_id(query):
        normalizeFlashId/padFlashId -> identifier 解码器按 priority 降序取首个命中
        -> device 默认补齐 -> applyIdentifierInfoHooks -> ok

上游带 opts.projection 的快速路径、marking/FBGA、fdb 合并均不在本模块范围。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from .constants import UNKNOWN
from .dispatch import PartDecoderDispatch
from .draft import draft_density, draft_field, draft_vendor, set_draft_field
from .field_normalization import (
    apply_dram_classification,
    apply_dram_public_type,
    canonical_nand_die_profile_key,
    parse_die_density_mbit,
    prune_redundant_fields,
)
from .field_registry import is_fdnext_field_key
from .normalize import (
    normalize_flash_id,
    normalize_part_number,
    pad_flash_id,
)

_RESULT_SCHEMA = "chiplookup.fdnext.decode.v1"


def _copy_draft(decoded: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in decoded.items()}


def _clean_fields(fields: Any) -> Dict[str, Any]:
    """剔除值为 None 的字段 —— 对应上游 JSON 序列化时 undefined 键被丢弃。"""
    if not isinstance(fields, dict):
        return {}
    return {k: v for k, v in fields.items() if v is not None}


def normalize_part_draft(part_number: str, decoded: Dict[str, Any]) -> Dict[str, Any]:
    """上游 normalizePartDraft：补 device 默认值并浅拷贝嵌套结构。"""
    device = decoded.get("device") or {}
    info: Dict[str, Any] = _copy_draft(decoded)
    info["device"] = {
        **device,
        "domain": device.get("domain") or "memory",
        "chipKind": device.get("chipKind") or "unknown",
        "vendor": device.get("vendor") or UNKNOWN,
        "partNumber": device.get("partNumber") or part_number,
    }
    fields = decoded.get("fields")
    # 与上游 JSON 序列化语义一致：值为 None(=JS undefined) 的字段键不进入草稿，
    # 避免 hook/分类阶段后仍残留 None（如 K9F2G08U0C 的 bad_block/operation_temperature）。
    info["fields"] = _clean_fields(fields)
    identifiers = decoded.get("identifiers")
    if identifiers:
        info["identifiers"] = {
            "flashIds": _merge_strings([], identifiers.get("flashIds")),
            "partNumbers": _merge_strings([], identifiers.get("partNumbers")),
        }
    info["controllers"] = _merge_strings([], decoded.get("controllers"))
    components = decoded.get("components")
    info["components"] = list(components) if isinstance(components, list) else None
    meta = decoded.get("meta")
    info["meta"] = dict(meta) if isinstance(meta, dict) else None
    warnings = decoded.get("warnings")
    info["warnings"] = list(warnings) if isinstance(warnings, list) else None
    return info


def _merge_strings(current: List[str], values: Any) -> Optional[List[str]]:
    from .draft import merge_draft_string_array

    return merge_draft_string_array(current, values or [])


def enrich_nand_die_profile_fields(
    info: Dict[str, Any], profile_table: Dict[str, Any]
) -> Dict[str, Any]:
    """上游 enrichNandDieProfileFields：按 canonical key 注入 die profile 字段。"""
    die_codename = draft_field(info, "die_codename")
    if not isinstance(die_codename, str) or not die_codename.strip() or die_codename == UNKNOWN:
        return info

    profile_key = canonical_nand_die_profile_key(
        die_codename, info, has_profile_key=lambda key: key in profile_table
    )
    meta = info.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("nandDieProfileKey"), str) and meta["nandDieProfileKey"].strip():
        meta["nandDieProfileKey"] = profile_key
    if profile_key != die_codename.strip():
        set_draft_field(info, "die_codename", profile_key)

    profile = profile_table.get(profile_key)
    if not isinstance(profile, dict):
        return info

    for key, value in profile.items():
        if value is None or not is_fdnext_field_key(key):
            continue
        if key != "die_codename" and draft_field(info, key) is not None:
            continue
        set_draft_field(info, key, value)
    return info


def derive_nand_density_from_die_stack(
    info: Dict[str, Any], profile_table: Dict[str, Any]
) -> Dict[str, Any]:
    """上游 deriveNandDensityFromDieStack：raw_nand 用 die_count x die_density 推 density。"""
    current = draft_density(info)
    device = info.get("device") or {}
    if (current is not None and draft_vendor(info) != "spectek") or device.get("chipKind") != "raw_nand":
        return info
    die_count = draft_field(info, "die_count")
    if isinstance(die_count, bool) or not isinstance(die_count, (int, float)):
        return info
    if not math.isfinite(float(die_count)) or die_count <= 0:
        return info
    die_density = parse_die_density_mbit(draft_field(info, "die_density"))
    if die_density is None:
        return info
    derived = die_density * float(die_count)
    if current is None or current != derived:
        set_draft_field(info, "density", derived)
    return info


def apply_identifier_info_hooks(info: Dict[str, Any], profile_table: Dict[str, Any]) -> Dict[str, Any]:
    """上游 applyIdentifierInfoHooks：postprocessor.identifierInfo 后 derive(enrich(info))。

    官方内部把 createDefaultIdentifierPostprocessor() 作为默认 internalDecodeHooks 成员，
    identifier 解码结果必须先经其按厂商裁剪/改写，再补 die profile 字段。
    如 SK hynix byte6>=0x80 会删扩展字段并把 spp 写入 plane_count（ADDE14A2D0E0）。
    """
    from .postprocess import apply_identifier_postprocess

    info = apply_identifier_postprocess(info)
    return derive_nand_density_from_die_stack(
        enrich_nand_die_profile_fields(info, profile_table), profile_table
    )


def apply_part_info_hooks(info: Dict[str, Any], profile_table: Dict[str, Any]) -> Dict[str, Any]:
    """上游 applyPartInfoHooks：postprocessor.partInfo 后 derive(enrich(info))。"""
    from .postprocess import apply_part_postprocess

    info = apply_part_postprocess(info)
    return derive_nand_density_from_die_stack(
        enrich_nand_die_profile_fields(info, profile_table), profile_table
    )


def load_engine(
    cache_root: Optional[str] = None,
    *,
    offline: bool = False,
    refresh: bool = False,
) -> "FdnextEngine":
    """从（必要时先下载的）上游规则缓存编译并创建引擎。"""
    from .compiler import compile_decodepack
    from .resources import load_bundle

    bundle = load_bundle(cache_root, offline=offline, refresh=refresh)
    die_profile = bundle.die_profile
    profile_table = die_profile if isinstance(die_profile, dict) else {}
    packed = compile_decodepack(
        bundle.part_specs,
        bundle.identifier_specs,
        {"nand.die_profile": profile_table},
    )
    return FdnextEngine(
        packed.part_decoders,
        packed.identifier_decoders,
        profile_table=profile_table,
    )


class FdnextEngine:
    """fdnext 规则解码引擎（无 mdb/fdb 索引依赖，纯规则 + die profile 表）。

    建议进程内常驻一个实例（规则编译成本较高），decode 可反复调用。
    """

    def __init__(
        self,
        part_decoders: List[Any],
        identifier_decoders: List[Any],
        profile_table: Optional[Dict[str, Any]] = None,
    ):
        # 与上游一致：part / identifier 解码器按 priority 降序稳定排序
        self._part_decoders = sorted(part_decoders, key=lambda d: -(d.priority or 0))
        self._identifier_decoders = sorted(
            identifier_decoders, key=lambda d: -(d.priority or 0)
        )
        self._dispatch = PartDecoderDispatch(self._part_decoders)
        self._profile_table: Dict[str, Any] = dict(profile_table or {})
        self._profile_key_func = lambda key: key in self._profile_table

    # ------------------------------------------------------------------
    # 顶层料号解码
    # ------------------------------------------------------------------

    def decode_part(self, query: str) -> Dict[str, Any]:
        normalized = normalize_part_number(query)
        if not normalized:
            return self._result("part.decode", "invalid_input", query, normalized)
        info = self._detect_raw(query)
        if info is None:
            return self._result("part.decode", "not_found", query, normalized)
        return self._result("part.decode", "ok", query, normalized, draft=info)

    def _detect_raw(self, part_number: str) -> Optional[Dict[str, Any]]:
        """上游 detectRaw 的规则部分：dispatch trie 取第一个能 decode 的候选。"""
        for decoder in self._dispatch.candidates(normalize_part_number(part_number)):
            matched = decoder.match(part_number)
            if not matched:
                continue
            decoded = decoder.decode(matched)
            if decoded:
                info = normalize_part_draft(part_number, decoded)
                info = apply_part_info_hooks(info, self._profile_table)
                return self._classify_draft(info)
        return None

    # ------------------------------------------------------------------
    # Flash ID 解码
    # ------------------------------------------------------------------

    def decode_flash_id(self, query: str) -> Dict[str, Any]:
        normalized = normalize_flash_id(query)
        if not self._is_flash_id_shape(query):
            return self._result("identifier.decode", "invalid_input", query, normalized)
        return self._result("identifier.decode", "ok", query, normalized, draft=self._decode_flash_id_raw(query))

    def _is_flash_id_shape(self, query: str) -> bool:
        normalized = normalize_flash_id(query)
        compact = self._compact_identifier(query)
        return compact == normalized and 2 <= len(normalized) <= 12 and len(normalized) % 2 == 0

    @staticmethod
    def _compact_identifier(query: str) -> str:
        import re

        return re.sub(r"[\s,._:-]+", "", query.upper())

    def _decode_flash_id_raw(self, query: str) -> Dict[str, Any]:
        """上游 decodeNandFlashIdRaw（去掉 fdb 合并）：取首个命中的 identifier 解码器。"""
        normalized = normalize_flash_id(query)
        padded = pad_flash_id(normalized)
        info: Optional[Dict[str, Any]] = None
        for decoder in self._identifier_decoders:
            if decoder.idScheme != "nand.flash_id":
                continue
            decoded = decoder.decode(padded)
            if decoded:
                info = self._finalize_identifier_draft(padded, decoded)
                break
        if info is None:
            info = {
                "device": {
                    "identifier": padded,
                    "idScheme": "nand.flash_id",
                    "domain": "memory",
                    "chipKind": "raw_nand",
                    "vendor": UNKNOWN,
                },
                "fields": {},
            }
        return apply_identifier_info_hooks(info, self._profile_table)

    def _finalize_identifier_draft(self, padded: str, decoded: Dict[str, Any]) -> Dict[str, Any]:
        device = decoded.get("device") or {}
        fields = decoded.get("fields")
        info: Dict[str, Any] = _copy_draft(decoded)
        info["device"] = {
            **device,
            "domain": device.get("domain") or "memory",
            "chipKind": device.get("chipKind") or "raw_nand",
            "vendor": device.get("vendor") or UNKNOWN,
            "identifier": device.get("identifier") or padded,
            "idScheme": device.get("idScheme") or "nand.flash_id",
        }
        info["fields"] = _clean_fields(fields)
        meta = decoded.get("meta")
        info["meta"] = dict(meta) if isinstance(meta, dict) else None
        return info

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_draft(info: Dict[str, Any]) -> Dict[str, Any]:
        """上游 inspectPartForDecodeClassification 尾部三步。"""
        apply_dram_classification(info)
        apply_dram_public_type(info)
        prune_redundant_fields(info)
        return info

    def _result(
        self,
        operation: str,
        status: str,
        query: str,
        normalized: str,
        draft: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "schema": _RESULT_SCHEMA,
            "operation": operation,
            "status": status,
            "query": query,
            "normalized": normalized,
        }
        if draft is not None:
            device = draft.get("device") or {}
            result["device"] = device
            result["fields"] = draft.get("fields") or {}
            result["meta"] = draft.get("meta")
            result["identifiers"] = draft.get("identifiers")
            result["controllers"] = draft.get("controllers")
            result["warnings"] = draft.get("warnings")
            result["_draft"] = draft
        return result
