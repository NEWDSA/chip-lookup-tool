# -*- coding: utf-8 -*-
"""
src/fdnext/identifier_compiler.py
---------------------------------
NAND Flash ID 解码器 —— 忠实移植上游 src/decodepack/identifier-compiler.ts。

规则(IdentifierDecodeSpec)：{ id, idScheme:"nand.flash_id", priority?, match, vendor,
    definition: { "<字节偏移>": { "<字段名>": BitRuleSet | {from: 复用字段} } } }

BitRule: { dq: [bit...], def: {"<bit组合值>": value}, when?, whenFields?, whenDieDensityMbitGte? }

输出形态（与上游一致）：
    {
      device: { identifier, idScheme, vendor, domain:"memory", chipKind:"raw_nand" },
      fields: {...},
      meta: { ruleId, fieldProfile: "nand.flash_id" }
    }
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def byte_at(identifier: str, offset: int) -> int:
    """取第 offset 字节（1-based）的十进制值。调用方需保证该字节为合法 hex。"""
    index = (offset - 1) * 2
    return int(identifier[index:index + 2], 16)


def _is_valid_hex(identifier: str, offset: int) -> bool:
    index = (offset - 1) * 2
    text = identifier[index:index + 2]
    if len(text) != 2:
        return False
    try:
        int(text, 16)
        return True
    except ValueError:
        return False


def byte_hex_at(identifier: str, offset: int) -> str:
    if not _is_valid_hex(identifier, offset):
        return ""
    return "%02X" % byte_at(identifier, offset)


def _when_byte_matches(actual: str, expected: str) -> bool:
    pattern = expected.upper().zfill(2)
    if "*" not in pattern and "?" not in pattern:
        return actual == pattern
    if len(pattern) != len(actual):
        return False
    return all(c == "*" or c == "?" or c == actual[i] for i, c in enumerate(pattern))


def _rule_matches_when(identifier: str, when: Optional[Dict[str, Any]]) -> bool:
    if not when:
        return True
    for offset_key, expected in when.items():
        actual = byte_hex_at(identifier, int(offset_key))
        values = expected if isinstance(expected, list) else [expected]
        if not any(_when_byte_matches(actual, value) for value in values):
            return False
    return True


def _scalar_equals(a: Any, b: Any) -> bool:
    return a == b or str(a) == str(b)


def _field_number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _field_condition_matches(value: Any, condition: Any) -> bool:
    if isinstance(condition, list):
        return any(_scalar_equals(value, item) for item in condition)
    if condition is None or not isinstance(condition, dict):
        return _scalar_equals(value, condition)

    comparison = condition
    if "eq" in comparison:
        expected = comparison["eq"] if isinstance(comparison["eq"], list) else [comparison["eq"]]
        if not any(_scalar_equals(value, item) for item in expected):
            return False
    numeric = _field_number(value)
    if comparison.get("gte") is not None and (numeric is None or numeric < comparison["gte"]):
        return False
    if comparison.get("gt") is not None and (numeric is None or numeric <= comparison["gt"]):
        return False
    if comparison.get("lte") is not None and (numeric is None or numeric > comparison["lte"]):
        return False
    if comparison.get("lt") is not None and (numeric is None or numeric >= comparison["lt"]):
        return False
    return True


def _rule_matches_fields(fields: Dict[str, Any], when_fields: Optional[Dict[str, Any]]) -> bool:
    if not when_fields:
        return True
    for field, condition in when_fields.items():
        if not _field_condition_matches(fields.get(field), condition):
            return False
    return True


def _rule_matches_die_density(fields: Dict[str, Any], min_die_density: Any) -> bool:
    if min_die_density is None:
        return True
    density = _field_number(fields.get("density"))
    die_count = _field_number(fields.get("die_count"))
    return density is not None and die_count is not None and die_count > 0 and density / die_count >= min_die_density


def _rule_matches(identifier: str, rule: Dict[str, Any], fields: Dict[str, Any]) -> bool:
    return (
        _rule_matches_when(identifier, rule.get("when"))
        and _rule_matches_fields(fields, rule.get("whenFields"))
        and _rule_matches_die_density(fields, rule.get("whenDieDensityMbitGte"))
    )


def _canonical_field(name: str) -> Dict[str, Any]:
    """字段名 -> {target: fields|meta, key, outputKey, scale?}（page/block size x1024）。"""
    raw = name[6:] if name.startswith("field:") else name
    if raw in ("meta.nandDieProfileKey", "meta.nandDieProfileKeys"):
        return {"target": "meta", "key": raw[len("meta."):], "outputKey": raw}
    scale = 1024 if raw in ("page_size", "block_size") else None
    return {"target": "fields", "key": raw, "outputKey": raw, "scale": scale}


def _read_output(out: Dict[str, Any], name: str) -> Any:
    field = _canonical_field(name)
    source = out.get("meta") if field["target"] == "meta" else out.get("fields")
    return (source or {}).get(field["key"])


def _write_output(out: Dict[str, Any], name: str, value: Any) -> None:
    field = _canonical_field(name)
    out[field["target"]][field["key"]] = value


def decode_identifier_by_definition(
    identifier: str,
    rule: Dict[str, Any],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "device": {
            "identifier": identifier,
            "idScheme": rule.get("idScheme"),
            "vendor": rule.get("vendor"),
            "domain": "memory",
            "chipKind": "raw_nand",
        },
        "fields": {},
        "meta": {"ruleId": rule.get("id"), "fieldProfile": "nand.flash_id"},
    }
    fields = out["fields"]

    for offset_key, rules in (rule.get("definition") or {}).items():
        offset = int(offset_key)
        if not _is_valid_hex(identifier, offset):
            continue
        byte = byte_at(identifier, offset)
        for field_name, rule_set in rules.items():
            # 字段复用 {from: ...}
            if isinstance(rule_set, dict) and "from" in rule_set and isinstance(rule_set["from"], str):
                value = _read_output(out, rule_set["from"])
                if value is not None:
                    _write_output(out, field_name, value)
                continue
            entries = rule_set if isinstance(rule_set, list) else [rule_set]
            for bit_rule in entries:
                if not _rule_matches(identifier, bit_rule, fields):
                    continue
                data = 0
                for bit in bit_rule.get("dq", []):
                    data = (data << 1) + ((byte >> bit) & 1)
                resolved = bit_rule.get("def", {}).get(str(data))
                if resolved is None:
                    continue
                field = _canonical_field(field_name)
                value = resolved * field["scale"] if isinstance(resolved, (int, float)) and not isinstance(resolved, bool) and field.get("scale") else resolved
                _write_output(out, field_name, value)
                break

    return out


class IdentifierDecoder:
    """单个已编译 Flash ID 解码器。"""

    def __init__(self, rule: Dict[str, Any]):
        self.id: str = rule.get("id", "")
        self.idScheme: str = rule.get("idScheme", "nand.flash_id")
        self.priority: Optional[int] = rule.get("priority")
        self.rule = rule
        from .part_compiler import _compile_js_regex
        self._match_prefix = rule["match"]["value"] if rule.get("match", {}).get("kind") == "prefix" else None
        self._match_pattern = (
            _compile_js_regex(rule["match"]["value"], rule["match"].get("flags"))
            if self._match_prefix is None
            else None
        )

    def matches_normalized(self, normalized: str) -> bool:
        if self._match_prefix is not None:
            return normalized.startswith(self._match_prefix)
        return bool(self._match_pattern.search(normalized))

    def check(self, identifier: str) -> bool:
        return self.matches_normalized(identifier.upper())

    def decode(self, identifier: str) -> Optional[Dict[str, Any]]:
        normalized = identifier.upper()
        if not self.matches_normalized(normalized):
            return None
        return decode_identifier_by_definition(normalized, self.rule)


def compile_identifier_decode_specs(rules: List[Dict[str, Any]]) -> List[IdentifierDecoder]:
    return [IdentifierDecoder(rule) for rule in rules]
