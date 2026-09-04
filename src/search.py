# -*- coding: utf-8 -*-
"""
chip_lookup.search
-------------------
搜索匹配逻辑：
- 精确匹配（不区分大小写）
- 前缀匹配
- 子串匹配
- 模糊匹配（difflib.SequenceMatcher + 加权评分）

返回按相关度从高到低排序的候选列表。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Iterable, List, Tuple


# 评分权重 —— 越大说明该项越贴近真实查询意图
SCORE_EXACT = 1000.0         # 完全相等
SCORE_PREFIX = 800.0         # 查询是字段的前缀
SCORE_CONTAINS = 600.0       # 字段包含查询
SCORE_FUZZY_MAX = 500.0      # 模糊相似度最高封顶值


def _norm(s: str) -> str:
    """统一大小写、去掉首尾空白、可能的话去空格，提升命中体感。"""
    if s is None:
        return ""
    return str(s).strip().upper()


def score_one(query: str, fields: Iterable[str]) -> Tuple[float, str]:
    """
    对单条记录与查询打分，返回 (分数, 命中字段名)。
    命中字段名用于 UI 提示用户「你在哪个字段上匹配上了」。
    """
    q = _norm(query)
    best = (0.0, "")

    for fname, raw in fields:
        f = _norm(raw)
        if not f:
            continue

        # 1) 完全相等（最高）
        if f == q:
            return (SCORE_EXACT, fname)

        # 2) 前缀匹配
        if f.startswith(q):
            score = SCORE_PREFIX + (len(q) / max(len(f), 1)) * 50
            if score > best[0]:
                best = (score, fname)
            continue

        # 3) 子串包含
        if q in f:
            # 越短的字段匹配上查询，分数越高（用户找的可能更"专")
            score = SCORE_CONTAINS + (1.0 / max(len(f), 1)) * 100
            if score > best[0]:
                best = (score, fname)
            continue

        # 4) 模糊相似度（按字符级）
        ratio = SequenceMatcher(None, q, f).ratio()
        if ratio >= 0.55:  # 阈值：避免返回完全不沾边的候选
            score = SCORE_FUZZY_MAX * ratio
            if score > best[0]:
                best = (score, fname)

    return best


def search(query: str, records: List[dict], top_n: int = 20) -> List[Tuple[dict, float, str]]:
    """
    在 records 中查找与 query 最相关的条目。
    返回：[(record, score, hit_field), ...]，按 score 降序。
    """
    if not query or not records:
        return []

    q = query.strip()
    if not q:
        return []

    # 所有可能参与匹配的文本字段（料号、型号、容量、厂商等）
    candidate_fields = (
        "part_number", "model", "manufacturer", "capacity",
        "type", "voltage", "speed", "package", "notes",
    )

    results: List[Tuple[dict, float, str]] = []
    for r in records:
        fields = ((name, r.get(name, "")) for name in candidate_fields)
        s, hit = score_one(q, fields)
        if s > 0:
            results.append((r, s, hit))

    results.sort(key=lambda x: (-x[1], x[0].get("part_number", "")))
    return results[:top_n]


def lookup_exact(query: str, records: List[dict]) -> List[dict]:
    """严格料号精确匹配（用于在结果中识别「精确结果」标记）。"""
    if not query:
        return []
    q = _norm(query)
    out = []
    for r in records:
        if _norm(r.get("part_number", "")) == q:
            out.append(r)
    return out


def suggest_terms(query: str, records: List[dict], max_n: int = 5) -> List[str]:
    """
    当查询无任何命中时，给出"用户也许想输入的"料号候选。
    用 difflib 在所有料号上找最相似的几条。
    """
    if not query:
        return []
    q = _norm(query)
    pairs = []
    for r in records:
        pn = r.get("part_number", "")
        if not pn:
            continue
        ratio = SequenceMatcher(None, q, _norm(pn)).ratio()
        pairs.append((ratio, pn))
    pairs.sort(key=lambda x: (-x[0], x[1]))
    seen = set()
    out = []
    for _, pn in pairs:
        if pn in seen:
            continue
        seen.add(pn)
        out.append(pn)
        if len(out) >= max_n:
            break
    return out
