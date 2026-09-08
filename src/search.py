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

性能设计（针对上游/混合模式下 ~4 万条记录 × 9 个文本字段）：
- 记录集预归一化：同一份 records（每次数据源构建后固定不变）只需归一化一次，
  之后每次按键直接复用，不再重复 strip/upper；
- 打分分两级：先跑「廉价匹配」（相等/前缀/包含）；由于 前缀(≥800)/包含(≥600)
  的最低分都高于模糊最高分(500)，只要一条记录存在任何廉价命中就无需对它做模糊
  比较 —— 排名与全量模糊扫描完全一致，只是省掉注定不影响结果的昂贵比较；
- 确实需要模糊比较时，先用正确且便宜的 ratio 上界门控（纯长度上界 + 字符多重集
  上界，均 ≤ SequenceMatcher.ratio()），只有上界达标才付真正的 ratio() 成本。
  注意：上界必须用「多重集交集 Σ min(cnt_q, cnt_f)」而非「不同字符数」——
  不同字符数不是 ratio 的上界（例：q=\"AAA\" 与 f=\"AAAA\" 的 ratio≈0.86，
  但不同字符交集只有 1），会误杀带重复字符的命中。
- 模糊命中在 _scan_record 内直接保留 (字段名, 值)，无需事后回扫还原字段名。
"""

from __future__ import annotations

import threading
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Set, Tuple

# 评分权重 —— 越大说明该项越贴近真实查询意图
SCORE_EXACT = 1000.0         # 完全相等
SCORE_PREFIX = 800.0         # 查询是字段的前缀
SCORE_CONTAINS = 600.0       # 字段包含查询
SCORE_FUZZY_MAX = 500.0      # 模糊相似度最高封顶值

FUZZY_RATIO_THRESHOLD = 0.55   # 模糊结果的最低相似度
MIN_SUGGEST_RATIO = 0.45       # 无命中时“相近料号”建议的最低相似度

# 参与匹配的文本字段（料号、型号、容量、厂商等）
CANDIDATE_FIELDS = (
    "part_number", "model", "manufacturer", "capacity",
    "type", "voltage", "speed", "package", "notes",
)


def _norm(s) -> str:
    """统一大小写、去掉首尾空白，提升命中体感。"""
    if s is None:
        return ""
    return str(s).strip().upper()


def _char_counts(s: str) -> Dict[str, int]:
    """查询字符多重集（区分大小写已由 _norm 归一）。"""
    d: Dict[str, int] = {}
    for c in s:
        d[c] = d.get(c, 0) + 1
    return d


# ---------------------------------------------------------------------------
# 记录集预归一化缓存
# ---------------------------------------------------------------------------
# 一次数据源构建后 records 是只读快照（UI 整体替换、从不原地改）。
# 以 id(records) 为键缓存归一化结果；换列表自然换键。缓存强引用列表防 id 复用，
# 最多保留 3 份（进程内同时存在 local/upstream/hybrid 三份即可）。

_PREP_CACHE_MAX = 3
_PREP_CACHE: "dict[int, _Prepared]" = {}
# 后台切换线程预热新记录集时可能与主线程查询并发访问缓存，加锁防互踩。
_PREP_LOCK = threading.Lock()


class _Prepared:
    """一份记录集的归一化形态：pn 列表 + 每行非空字段 (name, norm)。"""

    __slots__ = ("records", "pn", "rows")

    def __init__(self, records: List[dict]):
        self.records = records
        pn: List[str] = []
        rows: List[List[Tuple[str, str]]] = []
        for r in records:
            pn.append(_norm(r.get("part_number", "")))
            rr: List[Tuple[str, str]] = []
            for name in CANDIDATE_FIELDS:
                raw = r.get(name)
                if raw is None:
                    continue
                f = _norm(raw)
                if f:
                    rr.append((name, f))
            rows.append(rr)
        self.pn = pn
        self.rows = rows


def _prepared(records: List[dict]) -> _Prepared:
    with _PREP_LOCK:
        prep = _PREP_CACHE.get(id(records))
        if prep is not None and prep.records is records and len(prep.pn) == len(records):
            return prep
        if len(_PREP_CACHE) >= _PREP_CACHE_MAX:
            # 弹出最旧（dict 保持插入序）
            _PREP_CACHE.pop(next(iter(_PREP_CACHE)))
        prep = _Prepared(records)
        _PREP_CACHE[id(records)] = prep
        return prep


def warm_prepared(records: List[dict]) -> None:
    """预热某记录集的预归一化缓存。

    供后台切换线程在构建完 records 后立即调用：把 ~40k×10 字段的 strip/upper
    一次性成本从「用户首次按键的主线程」挪到「切换的后台线程」。
    """
    _prepared(records)


def clear_prepared(records: List[dict]) -> None:
    """显式丢弃某记录集的预归一化缓存（一般无需调用，换列表即自动换键）。"""
    with _PREP_LOCK:
        _PREP_CACHE.pop(id(records), None)


# ---------------------------------------------------------------------------
# 打分原语
# ---------------------------------------------------------------------------


def _ratio_upper_bound_ok(q: str, qcnt: Dict[str, int], f: str,
                          threshold: float) -> bool:
    """ratio() 的可达性门控：两个都低于 threshold 的上界 ⇒ ratio 必低于 threshold。

    1) 纯长度上界（等价 SequenceMatcher.real_quick_ratio）：
       2*min(len_q, len_f) / (len_q + len_f)
    2) 字符多重集上界（等价 quick_ratio 的字符交集计数）：
       2*Σ_c min(cnt_q(c), cnt_f(c)) / (len_q + len_f)
    两者都是 SequenceMatcher.ratio() 的真上界；任一下于阈值即可安全拒绝，
    无需构建 SequenceMatcher。f.count() 走 C 层，比 Python 逐字符快。
    """
    lq = len(q)
    lf = len(f)
    total = lq + lf
    if total <= 0:
        return False
    if 2.0 * (lq if lq < lf else lf) < threshold * total:
        return False
    common = 0
    for c, nq in qcnt.items():
        nf = f.count(c)
        if nf:
            common += nq if nf > nq else nf
            if 2.0 * common >= threshold * total:
                return True
    return False


def _fuzzy_score(q: str, qcnt: Dict[str, int], f: str) -> float:
    """模糊相似度；上界门控通过才付 ratio() 成本。"""
    if len(q) < 2 or not f:
        return 0.0
    if not _ratio_upper_bound_ok(q, qcnt, f, FUZZY_RATIO_THRESHOLD):
        return 0.0
    ratio = SequenceMatcher(None, q, f).ratio()
    if ratio >= FUZZY_RATIO_THRESHOLD:
        return SCORE_FUZZY_MAX * ratio
    return 0.0


def _fuzzy_score_at(q: str, qcnt: Dict[str, int], f: str,
                    threshold: float) -> float:
    """带自定义阈值的模糊相似度（供“相近料号”建议用较低门槛）。"""
    if len(q) < 2 or not f:
        return 0.0
    if not _ratio_upper_bound_ok(q, qcnt, f, threshold):
        return 0.0
    ratio = SequenceMatcher(None, q, f).ratio()
    if ratio >= threshold:
        return SCORE_FUZZY_MAX * ratio
    return 0.0


def _scan_record(
    q: str,
    qcnt: Dict[str, int],
    rows: List[Tuple[str, str]],
    allow_fuzzy: bool,
) -> Tuple[float, str]:
    """对单条记录的归一化字段打分，返回 (最高分, 命中字段名)。

    与 score_one 语义一致（同分取第一个命中字段）；任一字段廉价命中（≥600）
    后整条记录不再做模糊比较（模糊 ≤500，不影响最终取 max 的排名）。
    模糊候选以 (字段名, 值) 保存，胜出时直接可得字段名，无需事后回扫还原。
    """
    best = 0.0
    hit = ""
    fuzzy: List[Tuple[str, str]] = []
    for name, f in rows:
        if f == q:
            return (SCORE_EXACT, name)
        if f.startswith(q):
            sc = SCORE_PREFIX + (len(q) / max(len(f), 1)) * 50
            if sc > best:
                best = sc
                hit = name
        elif q in f:
            sc = SCORE_CONTAINS + (1.0 / max(len(f), 1)) * 100
            if sc > best:
                best = sc
                hit = name
        elif best < SCORE_CONTAINS:
            fuzzy.append((name, f))

    if allow_fuzzy and best < SCORE_CONTAINS and fuzzy:
        for name, f in fuzzy:
            sc = _fuzzy_score(q, qcnt, f)
            if sc > best:
                best = sc
                hit = name
    return (best, hit)


def score_one(query: str, fields: Iterable[str]) -> Tuple[float, str]:
    """
    对单条记录与查询打分，返回 (分数, 命中字段名)。
    命中字段名用于 UI 提示用户「你在哪个字段上匹配上了」。

    兼容入口：fields 为 (字段名, 原始值) 的迭代。单条记录量级小，
    直接按逐字段比较（含模糊），不做跨记录剪枝。
    """
    q = _norm(query)
    if not q:
        return (0.0, "")
    qcnt = _char_counts(q)
    best = (0.0, "")
    fuzzy: List[Tuple[str, str]] = []
    for fname, raw in fields:
        f = _norm(raw)
        if not f:
            continue
        if f == q:
            return (SCORE_EXACT, fname)
        if f.startswith(q):
            score = SCORE_PREFIX + (len(q) / max(len(f), 1)) * 50
            if score > best[0]:
                best = (score, fname)
            continue
        if q in f:
            score = SCORE_CONTAINS + (1.0 / max(len(f), 1)) * 100
            if score > best[0]:
                best = (score, fname)
            continue
        if best[0] < SCORE_CONTAINS:
            fuzzy.append((fname, f))
    if best[0] < SCORE_CONTAINS:
        for fname, f in fuzzy:
            score = _fuzzy_score(q, qcnt, f)
            if score > best[0]:
                best = (score, fname)
    return best


# ---------------------------------------------------------------------------
# 对外查询
# ---------------------------------------------------------------------------


def search(query: str, records: List[dict], top_n: int = 20) -> List[Tuple[dict, float, str]]:
    """
    在 records 中查找与 query 最相关的条目。
    返回：[(record, score, hit_field), ...]，按 score 降序。
    """
    if not query or not records:
        return []
    q = _norm(query)
    if not q:
        return []
    qcnt = _char_counts(q)

    prep = _prepared(records)
    results: List[Tuple[dict, float, str]] = []
    append = results.append
    cheap_hits = 0
    # 一旦廉价命中已达 top_n：模糊(≤500)永远进不了前 top_n（廉价最低 ≥600），
    # 剩余记录不再付模糊成本 —— 排序结果与全量模糊扫描完全一致。
    allow_fuzzy = True
    for rec, rows in zip(prep.records, prep.rows):
        s, hit = _scan_record(q, qcnt, rows, allow_fuzzy=allow_fuzzy)
        if s > 0:
            append((rec, s, hit))
            if s >= SCORE_CONTAINS:
                cheap_hits += 1
                if cheap_hits >= top_n:
                    allow_fuzzy = False

    results.sort(key=lambda x: (-x[1], x[0].get("part_number", "")))
    return results[:top_n]


def lookup_exact(query: str, records: List[dict]) -> List[dict]:
    """严格料号精确匹配（用于在结果中识别「精确结果」标记）。"""
    if not query:
        return []
    q = _norm(query)
    if not q:
        return []
    prep = _prepared(records)
    out = []
    for rec, pn in zip(prep.records, prep.pn):
        if pn == q:
            out.append(rec)
    return out


def cheap_partials(
    query: str,
    records: List[dict],
    top_n: int = 20,
    exclude_ids: Optional[Set[int]] = None,
) -> List[Tuple[dict, float, str]]:
    """精确命中后的廉价补位。

    只跑相等/前缀/包含（绝不调用 SequenceMatcher），用于在已精确命中某料号时，
    快速把「前缀/包含匹配的近似料号」垫在精确结果下方，避免对 4 万条 × 9 字段
    做一次全量模糊扫描。返回按 score 降序的 [(record, score, hit_field), ...]。
    """
    if not query or not records:
        return []
    q = _norm(query)
    if not q:
        return []
    qcnt = _char_counts(q)

    prep = _prepared(records)
    results: List[Tuple[dict, float, str]] = []
    for rec, rows in zip(prep.records, prep.rows):
        if exclude_ids and id(rec) in exclude_ids:
            continue
        s, hit = _scan_record(q, qcnt, rows, allow_fuzzy=False)
        if s > 0:
            results.append((rec, s, hit))

    results.sort(key=lambda x: (-x[1], x[0].get("part_number", "")))
    return results[:top_n]


def suggest_terms(query: str, records: List[dict], max_n: int = 5) -> List[str]:
    """
    当查询无任何命中时，给出"用户也许想输入的"料号候选。

    用 difflib 在所有料号上找最相似的几条。先按 MIN_SUGGEST_RATIO 做上界门控，
    只有真正够像（ratio ≥ MIN_SUGGEST_RATIO）的料号才会被报告 —— 既避免把一堆
    相似度 0.1~0.3 的无关料号当「相近候选」展示，也把无命中场景的开销压到毫秒级。
    """
    if not query:
        return []
    q = _norm(query)
    if not q:
        return []
    qcnt = _char_counts(q)

    prep = _prepared(records)
    pairs: List[Tuple[float, str]] = []
    for pn in prep.pn:
        if not pn:
            continue
        sc = _fuzzy_score_at(q, qcnt, pn, MIN_SUGGEST_RATIO)
        if sc > 0:
            pairs.append((sc, pn))
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
