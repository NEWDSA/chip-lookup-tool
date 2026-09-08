# -*- coding: utf-8 -*-
"""
tests/test_search_parity.py
---------------------------
搜索语义回归：快速路径（预归一化 + 廉价优先 + 上界门控）必须与「逐字段朴素
全量模糊扫描」的语义完全一致（同一批记录、同分、同命中字段），并覆盖历史上
踩过的坑：

- 重复字符误杀：旧「不同字符交集」上界不是 SequenceMatcher.ratio() 的真上界，
  会把 q=\"AAAA\" vs 字段 \"AABAA\"（ratio≈0.89）这类命中误杀；
- 廉价命中 ≥ top_n 后跳过模糊必须不影响 top-N 排序结果；
- 精确命中短路 + 廉价补位不允许混入模糊结果（分数 <600）；
- “相近料号”建议只报告真正够像（ratio ≥ MIN_SUGGEST_RATIO）的料号。

运行：python tests/test_search_parity.py   （仅依赖标准库，完全离线）
"""
from __future__ import annotations

import os
import sys
import unittest
from difflib import SequenceMatcher

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
SRC = os.path.join(ROOT, "src")
for _p in (ROOT, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from search import (
    CANDIDATE_FIELDS,
    FUZZY_RATIO_THRESHOLD,
    MIN_SUGGEST_RATIO,
    SCORE_CONTAINS,
    SCORE_EXACT,
    SCORE_FUZZY_MAX,
    SCORE_PREFIX,
    _norm,
    cheap_partials,
    lookup_exact,
    search,
    suggest_terms,
)


# ---------------------------------------------------------------------------
# 朴素 oracle：与改造前逐字段全量模糊完全一致
# ---------------------------------------------------------------------------

def oracle_scan(query: str, rows):
    q = _norm(query)
    best = 0.0
    hit = ""
    fuzzy = []
    for name, f in rows:
        if f == q:
            return (SCORE_EXACT, name)
        if f.startswith(q):
            sc = SCORE_PREFIX + (len(q) / max(len(f), 1)) * 50
            if sc > best:
                best, hit = sc, name
        elif q in f:
            sc = SCORE_CONTAINS + (1.0 / max(len(f), 1)) * 100
            if sc > best:
                best, hit = sc, name
        elif best < SCORE_CONTAINS:
            fuzzy.append((name, f))
    if best < SCORE_CONTAINS:
        for name, f in fuzzy:
            if len(q) < 2:
                continue
            r = SequenceMatcher(None, q, f).ratio()
            if r >= FUZZY_RATIO_THRESHOLD:
                sc = SCORE_FUZZY_MAX * r
                if sc > best:
                    best, hit = sc, name
    return (best, hit)


def oracle_search(query, records, top_n=20):
    out = []
    for rec in records:
        rows = [
            (name, _norm(rec.get(name)))
            for name in CANDIDATE_FIELDS
            if rec.get(name) is not None and _norm(rec.get(name))
        ]
        s, hit = oracle_scan(query, rows)
        if s > 0:
            out.append((rec, s, hit))
    out.sort(key=lambda x: (-x[1], x[0].get("part_number", "")))
    return out[:top_n]


def sig(results):
    return [(r.get("part_number"), round(s, 6), h) for r, s, h in results]


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------

def make_records():
    """含重复字符/近似料号/类型容量等对抗样本的记录集。"""
    raw = [
        # part_number, model, manufacturer, capacity, type
        ("H5TQ4G63AFR-RDC", "H5TQ4G63AFR", "Hynix", "4Gb", "DDR3"),
        ("H5TQ4G63AFR-PBC", "H5TQ4G63AFR", "Hynix", "4Gb", "DDR3"),
        ("H5TQ4G63EFR-RDC", "H5TQ4G63EFR", "Hynix", "4Gb", "DDR3"),
        ("MT60B2G8RZ-56B:D", "MT60B2G8RZ", "Micron", "16Gb", "DDR5"),
        ("MT60B4G8RZ-56B:B", "MT60B4G8RZ", "Micron", "16Gb", "DDR5"),
        ("K4A8G165WC-BCWE", "K4A8G165WC", "Samsung", "8Gb", "DDR4"),
        ("K4AAG165WA-BCTD", "K4AAG165WA", "Samsung", "8Gb", "DDR4"),
        ("D9STQ", "MT41K512M8", "Micron", "4Gb", "DDR3"),
        ("EDY8016AABG-F", "EDY8016AABG", "Elpida", "8Gb", "DDR4"),
        ("AABAA-CHIP", "AABAA", "Fake", "8Gb", "SRAM"),  # 重复字符对抗
        ("AAAA-CHIP", "AAAA", "Fake", "8Gb", "SRAM"),
        ("8GB-DIMM", "M378A1K43CB2", "Samsung", "8GB", "DDR4"),
        ("16GB-DIMM", "M393A2K43CB2", "Samsung", "16GB", "DDR4"),
        ("DDR4-UDIMM", "DDR4", "Generic", "", "DDR4"),
    ]
    records = []
    for pn, model, mfr, cap, typ in raw:
        rec = {
            "part_number": pn,
            "model": model,
            "manufacturer": mfr,
            "capacity": cap,
            "type": typ,
        }
        if model == "AABAA":
            rec["notes"] = "AABAA repeat AAA test"  # 追加含重复字符的值
        records.append(rec)
    # 再补一堆 DDR4 内存条，凑足 >20 条廉价命中
    for i in range(30):
        records.append({
            "part_number": "M378A1K43DB2-%02d" % i,
            "model": "M378A1K43DB2",
            "manufacturer": "Samsung",
            "capacity": "8GB",
            "type": "DDR4",
        })
    return records


QUERIES = [
    "H5TQ4G63AFR",      # 前缀/精确混合
    "DDR4",             # 大包含命中(>20) → 模糊应被跳过且排序不变
    "AAAA",             # 重复字符：不能误杀 AABAA/AAAA 的模糊命中
    "AABAA",            # 精确命中
    "MT60B2G8RZ-56B:D", # 精确 + 冒号
    "zzzzzz-not-exist", # 全不中
    "8GB",              # 容量类
    "M378A1K43",        # 前缀大命中
    "K4A8G165WX",       # 拼错（模糊近似）
]


class SearchParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = make_records()

    def test_search_matches_naive_oracle(self):
        for q in QUERIES:
            with self.subTest(q=q):
                expected = oracle_search(q, self.records, 20)
                got = search(q, self.records, 20)
                self.assertEqual(sig(expected), sig(got), "query=%r" % q)

    def test_repeated_char_fuzzy_not_pruned(self):
        # 旧实现用「不同字符交集」当上界，q="AAAA" 与 "AABAA" 交集只有 {A}，
        # 会被错误拒绝；正确上界应放行并给出 ratio≈0.889 的模糊命中。
        got = search("AAAA", self.records, 20)
        pns = {r.get("part_number") for r, _, _ in got}
        self.assertIn("AABAA-CHIP", pns, "重复字符的模糊命中不应被门控误杀")
        rec_score = {r.get("part_number"): s for r, s, _ in got}
        expect_ratio = SequenceMatcher(None, "AAAA", "AABAA").ratio()
        self.assertAlmostEqual(
            rec_score["AABAA-CHIP"], SCORE_FUZZY_MAX * expect_ratio, places=3
        )

    def test_cheap_dominant_skips_fuzzy_with_same_ranking(self):
        # DDR4 有 >20 条廉价命中：结果应与 oracle 完全一致（模糊被跳过也不影响）
        q = "DDR4"
        self.assertGreaterEqual(
            sum(1 for _, s, _ in search(q, self.records, 20) if s >= SCORE_CONTAINS),
            20,
        )

    def test_cheap_partials_never_mix_fuzzy(self):
        exacts = lookup_exact("MT60B2G8RZ-56B:D", self.records)
        self.assertEqual(len(exacts), 1)
        seen = {id(r) for r in exacts}
        partials = cheap_partials("MT60B2G8RZ-56B:D", self.records, 20, exclude_ids=seen)
        self.assertTrue(all(s >= SCORE_CONTAINS - 1e-6 for _, s, _ in partials))

    def test_suggest_only_close_terms(self):
        # 只允许报告 ratio ≥ MIN_SUGGEST_RATIO 的料号；垃圾查询应返回空
        self.assertEqual(suggest_terms("zzzzzz-not-exist", self.records, 5), [])
        sugs = suggest_terms("K4A8G165WX", self.records, 5)
        self.assertTrue(sugs, "拼错料号应给出相近候选")
        for pn in sugs:
            r = SequenceMatcher(None, "K4A8G165WX", pn).ratio()
            self.assertGreaterEqual(r, MIN_SUGGEST_RATIO - 1e-9)

    def test_lookup_exact_uppercase_insensitive(self):
        self.assertEqual(
            [r.get("part_number") for r in lookup_exact("mt60b2g8rz-56b:d", self.records)],
            ["MT60B2G8RZ-56B:D"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
