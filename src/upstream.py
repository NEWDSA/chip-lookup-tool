# -*- coding: utf-8 -*-
"""
chip_lookup.upstream
--------------------
上游数据源提供层。

职责：把上游索引（标记码 mdb.json + 料号 dram-pn.json）统一成
ChipLookup 可检索的记录列表。网络重试 / 请求限流 / 数据完整性校验
都在 src/fdnext/indexes.py 的索引缓存层实现；本层只负责：
    1) 调度上游索引加载（在线拉取 或 离线读缓存）；
    2) 把索引结构转换为与本地 CSV 一致的多字段记录；
    3) 绝不读取本地 CSV（上游独立模式保证）。

当 fdnext 解码引擎可用时，自动用型号解码补全 type/capacity 等规格字段。
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from database import DEFAULT_FIELDS  # noqa: E402
from fdnext.indexes import (  # noqa: E402
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_RATE_LIMIT,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    FdnextIndexes,
    default_cache_dir,
)

_log = logging.getLogger(__name__)


class UpstreamLoadError(Exception):
    """上游数据源加载失败（网络不可用 / 缓存缺失 / 完整性校验失败）。"""


class UpstreamResult:
    """一次上游加载的结果：记录列表 + 统计 + 来源 + 缓存目录。"""

    def __init__(
        self,
        records: List[dict],
        counts: Dict[str, int],
        sources: Dict[str, str],
        cache_dir: str,
    ):
        self.records = records
        self.counts = counts
        self.sources = sources
        self.cache_dir = cache_dir


def _blank(pn: str) -> dict:
    r = {k: "" for k in DEFAULT_FIELDS}
    r["part_number"] = pn
    return r


# fdnext 解码字段 → DEFAULT_FIELDS 映射
_DENSITY_TO_GB = {
    1: "1Gb", 2: "2Gb", 4: "4Gb", 8: "8Gb", 16: "16Gb",
    32: "32Gb", 64: "64Gb", 128: "128Gb", 256: "256Gb", 512: "512Gb",
    1024: "1Tb", 2048: "2Tb",
}


def _enrich_from_engine(rec: dict, decode_result: dict) -> dict:
    """用 fdnext 解码结果补全上游记录的空规格字段（只填空，不覆盖已有值）。"""
    fields = decode_result.get("fields", {})
    if not fields:
        return rec

    def _fill(key: str, raw_value: Any) -> None:
        if rec.get(key):
            return  # 已有值不覆盖
        if raw_value in (None, "", False, 0):
            return
        rec[key] = raw_value

    # type: dram_type → type
    _fill("type", fields.get("dram_type", ""))

    # capacity: dram_density (Mb) → Gb/Tb 字符串
    density_mb = fields.get("dram_density")
    if density_mb and isinstance(density_mb, (int, float)):
        density_gb = density_mb / 1024
        _fill("capacity", _DENSITY_TO_GB.get(int(density_gb), "%gGb" % density_gb))

    # bit_width: dram_width → "x8" / "x16" / "x32"
    width = fields.get("dram_width")
    if width and isinstance(width, (int, float)):
        _fill("bit_width", "x%d" % int(width))

    # voltage / speed / op_temp: 直接映射
    _fill("voltage", fields.get("dram_voltage", ""))
    _fill("speed", fields.get("dram_speed", ""))
    _fill("op_temp", fields.get("operation_temperature", ""))
    _fill("die_revision", fields.get("die_revision", ""))

    # die_count / cs_count
    die_count = fields.get("dram_die_count")
    if die_count and isinstance(die_count, (int, float)):
        _fill("die_count", str(int(die_count)))
    cs = fields.get("cs_count")
    if cs and isinstance(cs, (int, float)):
        _fill("cs_count", str(int(cs)))

    # package + dimensions: fdnext 返回 "VFBGA-78, 7.5x11x0.9" 格式
    pkg_raw = fields.get("package", "")
    if pkg_raw:
        parts = [p.strip() for p in str(pkg_raw).split(",", 1)]
        _fill("package", parts[0])
        if len(parts) > 1:
            _fill("dimensions", parts[1])

    return rec


def build_upstream_records(
    idx: FdnextIndexes,
    engine: Optional[Any] = None,
) -> List[dict]:
    """把上游索引转成记录列表（与本地 CSV 字段对齐）。

    - 标记码：part_number=码，model=唯一型号（多型号歧义时留空待确认），manufacturer=厂商；
    - 完整料号：part_number=料号，model=料号（沿用本地「料号即型号」惯例），manufacturer=厂商。
    - 若提供 fdnext engine，自动解码型号补全 type/capacity 等规格字段。

    按 part_number 去重（标记码优先）。
    """
    records: List[dict] = []
    seen = set()

    def _decode_and_enrich(rec: dict) -> None:
        """尝试用 fdnext 引擎解码型号并补全字段。"""
        model = rec.get("model", "")
        if not model or not engine:
            return
        try:
            result = engine.decode_part(model)
            if result.get("status") == "ok":
                _enrich_from_engine(rec, result)
        except Exception:
            pass  # 解码失败不影响记录基本数据

    for code in sorted(idx.mark_index.keys()):
        rows = idx.mark_index[code]
        models = [r["model"] for r in rows if r.get("model")]
        rec = _blank(code)
        if len(models) == 1:
            rec["model"] = models[0]
        rec["manufacturer"] = rows[0]["vendor"] if rows else ""
        _decode_and_enrich(rec)
        records.append(rec)
        seen.add(code)

    for pn in sorted(idx.pn_vendor.keys()):
        if pn in seen:
            continue
        rec = _blank(pn)
        rec["model"] = pn
        rec["manufacturer"] = idx.pn_vendor[pn]
        _decode_and_enrich(rec)
        records.append(rec)
        seen.add(pn)

    return records


class UpstreamProvider:
    """上游索引调度器（进程内按需加载，结果带缓存）。"""

    def __init__(
        self,
        *,
        cache_dir: Optional[str] = None,
        offline: bool = False,
        refresh: bool = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_age: float = DEFAULT_MAX_AGE_SECONDS,
        retries: int = DEFAULT_RETRIES,
        rate_limit: float = DEFAULT_RATE_LIMIT,
    ):
        self.cache_dir = cache_dir or default_cache_dir()
        self.offline = offline
        self.refresh = refresh
        self.timeout = timeout
        self.max_age = max_age
        self.retries = retries
        self.rate_limit = rate_limit

    def load(self) -> UpstreamResult:
        """加载上游索引并转换为记录列表。失败抛 UpstreamLoadError。

        include_catalog=False：上游独立模式不触碰本地 CSV。
        尝试加载 fdnext 解码引擎，用型号解码补全 type/capacity 等规格字段。
        """
        try:
            idx, src_map = FdnextIndexes.load(
                self.cache_dir,
                offline=self.offline,
                refresh=self.refresh,
                timeout=self.timeout,
                max_age=self.max_age,
                retries=self.retries,
                rate_limit=self.rate_limit,
                include_catalog=False,
            )
        except (OSError, ValueError, RuntimeError, FileNotFoundError) as exc:
            raise UpstreamLoadError("%s" % exc) from exc

        # 尝试加载 fdnext 解码引擎（补全规格字段）
        engine = None
        try:
            from fdnext.engine import load_engine
            engine = load_engine(self.cache_dir, offline=self.offline, refresh=self.refresh)
        except Exception as exc:
            _log.debug("fdnext 解码引擎加载失败，跳过型号解码补全: %s", exc)

        return UpstreamResult(
            records=build_upstream_records(idx, engine=engine),
            counts=idx.counts(),
            sources=src_map,
            cache_dir=self.cache_dir,
        )