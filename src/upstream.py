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

import json
import logging
import os
import sys
import tempfile
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

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


# ---------------------------------------------------------------------------
# 解码字段 memo（模型 → 补全后的 fields）
# ---------------------------------------------------------------------------
# 上游/混合每次切换都会把全部 ~4 万条扩展记录交给 fdnext 引擎逐条解码（实测
# 约 10s/次）。实际上很多记录共享同一个型号，且同一型号在进程内会被反复构建。
# 这里做两层缓存：
#   1) 进程内 memo（按 cache_dir 分组，跨数据源实例共享）；
#   2) 落盘 sidecar（存到索引缓存目录），让「下一次启动/切换」直接复用，
#      只有单次构建新增量足够大（说明是真实全量索引）才写盘，避免污染
#      只读 fixture / 源码目录等小缓存目录。
# 仅缓存 status=="ok" 且 fields 非空的解码结果；失败的型号每次重新尝试（代价低）。

DECODE_MEMO_SCHEMA = "fdnext-decode-fields-v1"
DECODE_MEMO_FILENAME = "fdnext-decode-fields-v1.json"
# 单次构建新增模型数达到该值才落盘（真实索引 ~4 万；测试 fixture 只有几条）
DECODE_MEMO_MIN_NEW_FOR_FLUSH = 200
_MEMO_REGISTRY: Dict[str, "DecodeMemo"] = {}
_MEMO_REGISTRY_LOCK = RLock()


def _rules_marker() -> str:
    """规则来源标识：内置快照存在时用其版本，否则标记为远程。"""
    try:
        from fdnext import resources as _res
        if _res.snapshot_dir():
            return "%s@%s" % (_res.SNAPSHOT_DIRNAME, _res.SNAPSHOT_VERSION)
    except Exception:
        pass
    return "remote"


class DecodeMemo:
    """型号 → fdnext 解码 fields 的线程安全缓存（进程内 + 可选落盘）。"""

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self._lock = RLock()
        self._fields: Dict[str, dict] = {}
        self._loaded = False   # 已从磁盘载入（磁盘文件存在且 schema 匹配）
        self._new_count = 0    # 本次进程内新增的条目数（用于决定是否落盘）

    # -- 读取 ----------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        path = os.path.join(self.cache_dir, DECODE_MEMO_FILENAME)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if (
                isinstance(data, dict)
                and data.get("schema") == DECODE_MEMO_SCHEMA
                and data.get("rules") == _rules_marker()
            ):
                fields = data.get("fields")
                if isinstance(fields, dict):
                    self._fields = {
                        k: v for k, v in fields.items() if isinstance(v, dict)
                    }
        except (OSError, ValueError):
            pass
        self._loaded = True

    def get(self, model: str) -> Optional[dict]:
        with self._lock:
            self._ensure_loaded()
            return self._fields.get(model)

    def put(self, model: str, fields: dict) -> None:
        """记录某型号的解码结果。

        允许空 dict：`ok 但无可用字段` 或 `解码未命中` 也落缓存，
        避免每个会话/每次切换都对同一批型号重复尝试解码
        （解码结果在同一规则版本下是确定的）。
        """
        if not model or fields is None:
            return
        with self._lock:
            self._ensure_loaded()
            if model not in self._fields:
                self._fields[model] = dict(fields)
                self._new_count += 1

    # -- 落盘 ----------------------------------------------------------

    def flush(self, min_new: int = DECODE_MEMO_MIN_NEW_FOR_FLUSH) -> bool:
        """新增量达到阈值才原子写盘。返回是否写盘。"""
        with self._lock:
            if self._new_count < min_new or not self._fields:
                return False
            payload = {
                "schema": DECODE_MEMO_SCHEMA,
                "rules": _rules_marker(),
                "fields": self._fields,
            }
            path = os.path.join(self.cache_dir, DECODE_MEMO_FILENAME)
            self._new_count = 0
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".fdnext-decode-", suffix=".tmp", dir=self.cache_dir
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False)
                os.replace(tmp, path)
            except Exception:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            _log.debug("解码 memo 落盘失败（忽略）: %s", exc)
            return False
        _log.info("解码 memo 已落盘：%d 个型号 -> %s", len(self._fields), path)
        return True


def _memo_for(cache_dir: str) -> DecodeMemo:
    """取（或创建）某个索引缓存目录对应的进程内 memo。"""
    with _MEMO_REGISTRY_LOCK:
        memo = _MEMO_REGISTRY.get(cache_dir)
        if memo is None:
            memo = DecodeMemo(cache_dir)
            _MEMO_REGISTRY[cache_dir] = memo
        return memo


class _LazyEngine:
    """惰性 fdnext 引擎：首次真正需要 decode_part 时才编译规则。

    memo 已覆盖全部型号时（热切换/二次启动），引擎工厂不会触发，
    省掉每次数据源重建 ~0.2s 的规则编译开销。
    """

    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._engine = None

    def decode_part(self, model: str) -> dict:
        if self._engine is None:
            self._engine = self._factory()
        return self._engine.decode_part(model)


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
    memo: Optional[DecodeMemo] = None,
) -> List[dict]:
    """把上游索引转成记录列表（与本地 CSV 字段对齐）。

    - 标记码：part_number=码，model=唯一型号（多型号歧义时留空待确认），manufacturer=厂商；
    - 完整料号：part_number=料号，model=料号（沿用本地「料号即型号」惯例），manufacturer=厂商。
    - 若提供 fdnext engine，自动解码型号补全 type/capacity 等规格字段；
      传入 memo 时同一型号只解码一次，之后从 memo 直接取 fields。
    - 若 engine 为空但 memo 有缓存，也会用 memo 补全（引擎可整体跳过）。

    按 part_number 去重（标记码优先）。
    """
    records: List[dict] = []
    seen = set()

    def _decode_and_enrich(rec: dict) -> None:
        """尝试用 fdnext 引擎解码型号并补全字段（优先命中 memo）。"""
        model = rec.get("model", "")
        if not model:
            return
        if memo is not None:
            cached = memo.get(model)
            if cached is not None:
                _enrich_from_engine(rec, {"fields": cached})
                return
        if not engine:
            return
        try:
            result = engine.decode_part(model)
            if memo is not None:
                # 空/未命中结果也缓存（同一规则版本下解码确定，无需每轮重试）
                fields = (result.get("fields") or {}) if result.get("status") == "ok" else {}
                memo.put(model, fields)
            if result.get("status") == "ok":
                _enrich_from_engine(rec, {"fields": result.get("fields") or {}})
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

        # 惰性加载 fdnext 解码引擎（补全规格字段）：memo 命中足够多时
        # （热切换/二次启动）根本不会触发引擎编译，仅首次解码才付费。
        memo = _memo_for(self.cache_dir)
        engine = None
        try:
            from fdnext.engine import load_engine

            def _factory():
                return load_engine(
                    self.cache_dir, offline=self.offline, refresh=self.refresh
                )

            engine = _LazyEngine(_factory)
        except Exception as exc:
            _log.debug("fdnext 解码引擎加载失败，跳过型号解码补全: %s", exc)

        records = build_upstream_records(idx, engine=engine, memo=memo)
        memo.flush()
        return UpstreamResult(
            records=records,
            counts=idx.counts(),
            sources=src_map,
            cache_dir=self.cache_dir,
        )