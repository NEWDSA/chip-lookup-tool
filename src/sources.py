# -*- coding: utf-8 -*-
"""
chip_lookup.sources
-------------------
三种运行模式的数据源抽象层（RecordSource），彼此解耦：

    LocalSource      本地 CSV 模式：只读本地 CSV，完全屏蔽上游；
    UpstreamSource   上游模式：只从上游索引拉取，完全不读本地 CSV；
    HybridSource     混合模式：本地 + 上游融合检索，
                     支持 本地优先（缺失从上游补全） / 上游优先（异常降级本地兜底）。

三者都对外暴露同一套接口：list_records / count / reload / import_csv / export_csv，
UI 与 CLI 无需感知数据从哪来，只操作记录列表。
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from config import (  # noqa: E402
    HYBRID_LOCAL_FIRST,
    HYBRID_UPSTREAM_FIRST,
    MODE_HYBRID,
    MODE_LOCAL,
    MODE_UPSTREAM,
    Settings,
)
from database import DEFAULT_FIELDS, ChipDatabase, default_database_path, validate_csv  # noqa: E402
from upstream import UpstreamLoadError, UpstreamProvider  # noqa: E402


class SourceLoadError(Exception):
    """数据源加载失败（本地文件缺失/非法、上游不可用等致命问题）。"""


class SourceUnsupportedError(Exception):
    """当前模式不支持的操作（如上游模式导入本地 CSV）。"""


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------


class RecordSource:
    """数据源统一接口。子类实现各自的加载方式。"""

    MODE = MODE_LOCAL
    LABEL = "本地CSV"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.warnings: List[str] = []

    # -- 数据访问 -------------------------------------------------------

    def list_records(self) -> List[dict]:
        raise NotImplementedError

    def count(self) -> int:
        return len(self.list_records())

    def reload(self) -> None:
        raise NotImplementedError

    # -- 展示信息 -------------------------------------------------------

    def display_name(self) -> str:
        """状态栏展示用：数据源的简要定位信息。"""
        return ""

    def describe(self) -> str:
        return "%s · %s" % (self.LABEL, self.display_name() or "-")

    # -- 可选能力 -------------------------------------------------------

    def supports_import(self) -> bool:
        return False

    def supports_export(self) -> bool:
        return False

    def import_csv(self, path: str, replace: bool = False) -> int:
        raise SourceUnsupportedError("%s 模式不支持导入 CSV" % self.LABEL)

    def export_csv(self, path: str) -> int:
        raise SourceUnsupportedError("%s 模式不支持导出 CSV" % self.LABEL)


# ---------------------------------------------------------------------------
# 本地 CSV 模式
# ---------------------------------------------------------------------------


class LocalSource(RecordSource):
    """只读取本地 CSV；不触发任何上游调用。"""

    MODE = MODE_LOCAL
    LABEL = "本地CSV"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.csv_path = settings.csv_path or default_database_path()
        self.reload()

    def reload(self) -> None:
        try:
            records, issues = validate_csv(self.csv_path)
        except ValueError as exc:
            raise SourceLoadError(str(exc)) from exc
        self.warnings = list(issues)
        self._records = records

    def list_records(self) -> List[dict]:
        return [dict(r) for r in self._records]

    def count(self) -> int:
        # 直接数内部列表，避免基类 len(list_records()) 每次状态刷新整表复制
        return len(self._records)

    def display_name(self) -> str:
        return os.path.basename(self.csv_path)

    def supports_import(self) -> bool:
        return True

    def supports_export(self) -> bool:
        return True

    def import_csv(self, path: str, replace: bool = False) -> int:
        db = ChipDatabase(self.csv_path)
        n = db.import_csv(path, replace=replace)
        db.save()
        self.reload()
        return n

    def export_csv(self, path: str) -> int:
        db = ChipDatabase(self.csv_path)
        return db.export_csv(path)


# ---------------------------------------------------------------------------
# 上游数据源模式
# ---------------------------------------------------------------------------


class UpstreamSource(RecordSource):
    """只从上游索引拉取（缓存优先，可联网）；完全不读本地 CSV。"""

    MODE = MODE_UPSTREAM
    LABEL = "网络"  # 上游数据源统一以「网络」命名

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self._counts: Dict[str, int] = {}
        self._sources: Dict[str, str] = {}
        self._cache_dir: str = ""
        self.reload()

    def reload(self) -> None:
        provider = UpstreamProvider(
            cache_dir=self.settings.upstream_cache_dir,
            offline=self.settings.upstream_offline,
            refresh=self.settings.upstream_refresh,
            timeout=self.settings.upstream_timeout,
            max_age=self.settings.upstream_max_age,
            retries=self.settings.upstream_retries,
            rate_limit=self.settings.upstream_rate_limit,
        )
        try:
            result = provider.load()
        except UpstreamLoadError as exc:
            raise SourceLoadError("上游加载失败: %s" % exc) from exc
        self._records = result.records
        self._counts = result.counts
        self._sources = result.sources
        self._cache_dir = result.cache_dir
        self.warnings = [
            "网络索引来源: %s" % ", ".join(
                "%s=%s" % (k, v) for k, v in sorted(result.sources.items())
            )
        ]

    def list_records(self) -> List[dict]:
        return [dict(r) for r in self._records]

    def count(self) -> int:
        return len(self._records)

    def display_name(self) -> str:
        m = self._counts.get("marks", 0)
        p = self._counts.get("pn", 0)
        return "索引(mdb %d · pn %d)%s" % (
            m, p, " · 离线缓存" if self.settings.upstream_offline else ""
        )

    def supports_export(self) -> bool:
        return True

    def export_csv(self, path: str) -> int:
        """把上游索引（当前缓存视图）导出为 CSV，便于离线共享。"""
        data = [dict(r) for r in self._records]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=DEFAULT_FIELDS)
            writer.writeheader()
            for r in data:
                writer.writerow({k: r.get(k, "") for k in DEFAULT_FIELDS})
        return len(data)


# ---------------------------------------------------------------------------
# 混合模式
# ---------------------------------------------------------------------------


def _pn_key(rec: dict) -> str:
    return (rec.get("part_number", "") or "").strip().upper()


def merge_records(
    local_records: List[dict],
    upstream_records: List[dict],
    priority: str = HYBRID_LOCAL_FIRST,
) -> List[dict]:
    """
    双数据源融合（去重 + 字段映射/冲突处理）。

    - 按 part_number 大小写不敏感去重；
    - 主源（priority 决定）记录优先；次源只填补主源记录的空字段（只填空，不覆盖）；
    - 次源中主源没有的料号，追加到结果末尾。
    """
    local_map: Dict[str, dict] = {}
    for r in local_records:
        k = _pn_key(r)
        if k:
            local_map[k] = r
    up_map: Dict[str, dict] = {}
    for r in upstream_records:
        k = _pn_key(r)
        if k:
            up_map.setdefault(k, r)

    if priority == HYBRID_UPSTREAM_FIRST:
        primary, secondary = up_map, local_map
    else:
        primary, secondary = local_map, up_map

    merged: List[dict] = []
    for pn, rec in primary.items():
        m = dict(rec)
        filler = secondary.get(pn)
        if filler:
            for f in DEFAULT_FIELDS:
                if f == "part_number":
                    continue
                cur = (m.get(f) or "").strip()
                if not cur:
                    val = filler.get(f)
                    if val not in (None, ""):
                        m[f] = str(val).strip()  # 只填空：冲突时主源优先
        merged.append(m)

    for pn, rec in secondary.items():
        if pn not in primary:
            merged.append(dict(rec))

    return merged


class HybridSource(RecordSource):
    """本地 + 上游融合。任何一路加载失败都只降级不影响另一路。"""

    MODE = MODE_HYBRID
    LABEL = "混合"

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.csv_path = settings.csv_path or default_database_path()
        self.local_warnings: List[str] = []
        self.upstream_warnings: List[str] = []
        self._local_count = 0
        self.reload()

    def reload(self) -> None:
        # 1) 本地 CSV（容错：失败记告警，不中断）
        try:
            local_records, local_issues = validate_csv(self.csv_path)
            self.local_warnings = list(local_issues)
            self._local_count = len(local_records)
        except ValueError as exc:
            self.local_warnings = ["本地 CSV 加载失败: %s" % exc]
            local_records = []
            self._local_count = 0

        # 2) 上游索引（容错：失败记告警，不中断）
        provider = UpstreamProvider(
            cache_dir=self.settings.upstream_cache_dir,
            offline=self.settings.upstream_offline,
            refresh=self.settings.upstream_refresh,
            timeout=self.settings.upstream_timeout,
            max_age=self.settings.upstream_max_age,
            retries=self.settings.upstream_retries,
            rate_limit=self.settings.upstream_rate_limit,
        )
        try:
            up_result = provider.load()
            upstream_records = up_result.records
            self._up_counts = up_result.counts
            self.upstream_warnings = [
                "网络索引来源: %s" % ", ".join(
                    "%s=%s" % (k, v)
                    for k, v in sorted(up_result.sources.items())
                )
            ]
        except UpstreamLoadError as exc:
            self.upstream_warnings = ["网络加载失败（已降级仅用本地）: %s" % exc]
            upstream_records = []
            self._up_counts = {}

        self.warnings = self.local_warnings + self.upstream_warnings

        merged = merge_records(
            local_records, upstream_records, priority=self.settings.hybrid_priority
        )
        if not merged:
            raise SourceLoadError(
                "混合模式无可检索数据（本地 %d 条 / 网络 %d 条）; %s"
                % (len(local_records), len(upstream_records),
                   "; ".join(self.warnings) or "两路数据源均为空")
            )
        self._records = merged

    def list_records(self) -> List[dict]:
        return [dict(r) for r in self._records]

    def count(self) -> int:
        return len(self._records)

    def display_name(self) -> str:
        parts = ["本地 %d" % self._local_count]
        up = self._up_counts
        if up:
            parts.append("网络 %d" % (up.get("marks", 0) + up.get("pn", 0)))
        else:
            parts.append("网络 0(降级)")
        return " + ".join(parts)

    def supports_import(self) -> bool:
        return True

    def supports_export(self) -> bool:
        return True

    def import_csv(self, path: str, replace: bool = False) -> int:
        if self.settings.hybrid_priority == HYBRID_UPSTREAM_FIRST:
            # 上游优先：导入写入本地 CSV 供兜底，同样生效
            pass
        db = ChipDatabase(self.csv_path)
        n = db.import_csv(path, replace=replace)
        db.save()
        self.reload()
        return n

    def export_csv(self, path: str) -> int:
        """导出当前融合视图（本地 + 上游）到 CSV。"""
        data = [dict(r) for r in self._records]
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            import csv

            writer = csv.DictWriter(f, fieldnames=DEFAULT_FIELDS)
            writer.writeheader()
            for r in data:
                writer.writerow({k: r.get(k, "") for k in DEFAULT_FIELDS})
        return len(data)


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------


SOURCE_CLASSES = {
    MODE_LOCAL: LocalSource,
    MODE_UPSTREAM: UpstreamSource,
    MODE_HYBRID: HybridSource,
}


def make_source(settings: Settings) -> RecordSource:
    """按 settings.mode 构造对应的数据源实例。加载失败会抛 SourceLoadError。"""
    cls = SOURCE_CLASSES.get(settings.mode)
    if cls is None:
        raise SourceLoadError("未知运行模式: %s" % settings.mode)
    return cls(settings)