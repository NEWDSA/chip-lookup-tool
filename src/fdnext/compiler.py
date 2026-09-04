# -*- coding: utf-8 -*-
"""
src/fdnext/compiler.py
----------------------
compileDecodePack 的 Python 版：把原始规则数据编译成
{ partDecoders, identifierDecoders, profileTables }。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .identifier_compiler import compile_identifier_decode_specs
from .part_compiler import compile_part_decode_specs
from .table import normalize_decode_tables


@dataclass
class CompiledPack:
    part_decoders: List[Any] = field(default_factory=list)
    identifier_decoders: List[Any] = field(default_factory=list)
    profile_tables: Dict[str, Dict[str, Any]] = field(default_factory=dict)


def compile_decodepack(
    part_specs: List[Dict[str, Any]],
    identifier_specs: List[Dict[str, Any]],
    shared_tables: Optional[Dict[str, Any]] = None,
) -> CompiledPack:
    """把上游规则数据编译成可执行解码器（shared_tables = {'nand.die_profile': 原始表}）。"""
    return CompiledPack(
        part_decoders=compile_part_decode_specs(part_specs, shared_tables),
        identifier_decoders=compile_identifier_decode_specs(identifier_specs),
        profile_tables=normalize_decode_tables(shared_tables),
    )
