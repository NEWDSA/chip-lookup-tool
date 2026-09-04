# -*- coding: utf-8 -*-
"""
src/fdnext/dispatch.py
----------------------
料号分发 Trie —— 忠实移植上游 src/engine/part-decoder-dispatch.ts。

dispatchPrefixes 为空(=[]，即无字面前缀/正则兜底)的解码器进入 fallback；
有前缀的按前缀字符走 Trie，路径上经过的每个节点都收集其 decoderIndex。
candidates() 把 fallback 与命中路径的解码器按「原数组下标升序」返回。
"""

from __future__ import annotations

from typing import Any, Dict, List


def _new_node() -> Dict[str, Any]:
    return {"children": {}, "indexes": []}


class PartDecoderDispatch:
    def __init__(self, decoders: List[Any]):
        self.decoders = decoders
        root = _new_node()
        fallback: List[int] = []
        seen: set = set()
        for decoder_index, decoder in enumerate(decoders):
            prefixes = sorted(
                {p.upper() for p in (decoder.dispatch_prefixes or []) if p}
            )
            if not prefixes:
                fallback.append(decoder_index)
                continue
            for prefix in prefixes:
                node = root
                for char in prefix:
                    node = node["children"].setdefault(char, _new_node())
                node["indexes"].append(decoder_index)
        self.root = root
        self.fallback = fallback
        # 与上游一致：最终暴露对象不可变
        self.candidates_cache: Dict[str, List[Any]] = {}

    def candidates(self, input_str: str) -> List[Any]:
        cached = self.candidates_cache.get(input_str)
        if cached is not None:
            return cached
        indexes = set(self.fallback)
        node = self.root
        for char in input_str:
            if char not in node["children"]:
                break
            node = node["children"][char]
            indexes.update(node["indexes"])
        ordered = [self.decoders[i] for i in sorted(indexes)]
        self.candidates_cache[input_str] = ordered
        return ordered
