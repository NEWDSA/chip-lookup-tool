# -*- coding: utf-8 -*-
"""
src/fdnext/indexes.py
---------------------
本地索引层：把上游「索引类」数据（非规则）与本地 CSV 目录整合成统一检索能力。

背景（与 M1 边界的关系）：规则解码引擎刻意不内置索引以保证与 golden 对拍干净；
本模块作为引擎之上的**独立层**，把 fm.itxtech.org 依赖的索引能力补回来：

    mdb.json             Micron FBGA 顶标码 / SpecTek 标记码 -> 型号（mark 反查）
    dram-pn.json         DRAM 料号索引（pn -> 厂商）
    managed-nand-pn.json 受控 NAND（eMMC/UFS/eMCP）料号索引
    fdb.json             Flash ID -> 已知料号名 / 控制器（含 iddb 汇总块）
    data/chip_database.csv  本地目录（catalog 模糊搜索）

上游实际结构（2026-09 实测确认）：
    mdb.json             {vendor: {标记码: 型号 或 [型号...]}}
    dram-pn.json         [{vendor, pn}, ...]
    managed-nand-pn.json [{vendor, pn}, ...]
    fdb.json             {schemaVersion, info, iddb, <vendor 块>...}
                         其中 iddb 块以 Flash ID 为键：
                             {ID: {"t": [控制器], "n": ["<厂商> <料号>"...]}}
                         厂商块以已知料号为键：
                             {PN: {"id": [Flash ID...], "t": [控制器],
                                   "l": die_codename, "c": 层级, "pc": 级别,
                                   "pkg": 封装, "sg": 速度等级, "vol": 电压}}
                         （Flash ID 均为 12 位大写十六进制 = 6 字节）

本模块所有索引数据与 tools/sync_upstream.py 共用同一套下载缓存语义
（7 天新鲜期 / 下载失败降级旧缓存），纯 Python 标准库，无 Node / 无第三方依赖。
mdb.json / dram-pn.json 为必需索引；managed-nand-pn.json / fdb.json 为可选
（离线缺文件时自动跳过，不阻塞标记码/料号反查）。

典型调用：
    idx = FdnextIndexes.load(cache_dir=..., offline=True)  # 只读缓存
    idx.lookup_marking("D8DKS")                       # -> [{vendor, model}]
    idx.lookup_pn("K4S511632D-UC75")                  # -> 厂商显示名
    idx.lookup_flash("2C84044BA900")                  # -> fdb 记录(控制器/料号名)
    idx.fuzzy_catalog("MT40", top_n=5)                # 本地目录模糊搜索
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.normpath(os.path.join(HERE, ".."))
ROOT = os.path.normpath(os.path.join(SRC, ".."))
for _p in (ROOT, SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.database import ChipDatabase, default_database_path  # noqa: E402

# 与 tools/sync_upstream.py 共用缓存与厂商显示名，避免两套约定漂移
if os.path.join(ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "tools"))
from sync_upstream import VENDOR_DISPLAY  # noqa: E402
from sync_upstream import default_cache_dir  # noqa: E402

# GitHub raw 为主源；jsDelivr CDN 仅作降级镜像（国内网络更稳）。
# 顺序即优先级：先 GitHub raw，失败再镜像。
UPSTREAM_BASES = (
    "https://raw.githubusercontent.com/iTXTech/fdnext/"
    "master/packages/core/resources/",
    "https://cdn.jsdelivr.net/gh/iTXTech/fdnext@master/packages/core/resources/",
)

# (文件名, 说明, 是否必需)
# 必需：缺失会抛错（mdb/dram-pn 是标记码+料号反查的主干）。
# 可选：离线缺失时跳过（fdb/managed-nand 只影响 Flash 库 / 受控 NAND 补全）。
INDEX_FILES = {
    "mdb.json": ("Micron/SpecTek 标记码 -> 型号", True),
    "dram-pn.json": ("DRAM 料号索引 -> 厂商", True),
    "managed-nand-pn.json": ("受控 NAND(eMMC/UFS/eMCP) 料号索引", False),
    "fdb.json": ("Flash ID -> 料号名/控制器", False),
}

DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RETRIES = 2            # 每个镜像源的请求重试次数
DEFAULT_RATE_LIMIT = 0.0       # 请求间最小间隔（秒），用于限流
USER_AGENT = "ChipLookup-indexes/1.0 (pure-python; no node)"


# ---------------------------------------------------------------------------
# 上游文件完整性校验（下载后 / 离线读取时共用）
# ---------------------------------------------------------------------------


def validate_index_content(name: str, content: bytes) -> object:
    """校验上游 JSON 内容：可解析且顶层结构符合该文件的预期形态。返回解析对象。"""
    import json

    try:
        obj = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(
            "完整性校验失败 %s: 不是有效的 UTF-8/JSON（%s）" % (name, exc)
        ) from exc
    if name == "mdb.json":
        if not isinstance(obj, dict):
            raise ValueError("完整性校验失败 %s: 顶层应为 {厂商: {码: 型号}}" % name)
        for _vendor, block in obj.items():
            if not isinstance(block, dict):
                raise ValueError("完整性校验失败 %s: 厂商块应为对象" % name)
            break
    elif name in ("dram-pn.json", "managed-nand-pn.json"):
        if not isinstance(obj, list):
            raise ValueError("完整性校验失败 %s: 顶层应为数组" % name)
    elif name == "fdb.json":
        if not isinstance(obj, dict):
            raise ValueError("完整性校验失败 %s: 顶层应为对象" % name)
    return obj


def validate_index_file(name: str, path: str) -> object:
    """读取缓存文件并做完整性校验。非法时抛 ValueError。"""
    with open(path, "rb") as f:
        return validate_index_content(name, f.read())



# ---------------------------------------------------------------------------
# 索引缓存（与 sync_upstream 同款语义：7 天新鲜 / 失败降级 / 可离线）
# ---------------------------------------------------------------------------


def _download(name: str, dst: str, timeout: float, retries: int = DEFAULT_RETRIES,
              rate_limit: float = DEFAULT_RATE_LIMIT) -> bytes:
    """按 UPSTREAM_BASES 顺序尝试下载，全部失败才抛错。

    带网络异常重试（每个源 retries 次，指数退避）与请求限流
    （相邻请求间隔 rate_limit 秒）；下载内容须通过完整性校验才会写盘。
    """
    import time
    import urllib.error
    import urllib.request

    last_exc: Optional[Exception] = None
    for base in UPSTREAM_BASES:
        for attempt in range(retries + 1):
            req = urllib.request.Request(
                base + name, headers={"User-Agent": USER_AGENT}
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read()
                # 数据完整性校验：JSON 结构不合法视为下载失败
                validate_index_content(name, data)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                tmp = dst + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, dst)
                return data
            except (urllib.error.URLError, OSError, ValueError) as exc:
                last_exc = exc
                if rate_limit > 0:
                    time.sleep(rate_limit)  # 请求限流
                if attempt < retries:
                    time.sleep(0.2 * (attempt + 1))  # 指数退避
    raise RuntimeError(
        "下载 %s 失败（已尝试 %d 个源 × %d 次）: %s"
        % (name, len(UPSTREAM_BASES), retries + 1, last_exc)
    ) from last_exc


def ensure_index_cache(
    cache_dir: Optional[str] = None,
    *,
    offline: bool = False,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_age: float = DEFAULT_MAX_AGE_SECONDS,
    retries: int = DEFAULT_RETRIES,
    rate_limit: float = DEFAULT_RATE_LIMIT,
) -> Tuple[str, Dict[str, str]]:
    """
    确保索引文件就位。返回 (cache_dir, {name: 来源描述})。
    在线模式下载缺失/过期文件（带重试 + 限流 + 完整性校验），失败且有旧文件时降级复用。
    「可选」索引离线缺失时不在 src_map 中出现（调用方按缺省处理）。
    """
    import time
    import urllib.error

    cache_dir = cache_dir or default_cache_dir()
    src_map: Dict[str, str] = {}

    for name, (desc, required) in INDEX_FILES.items():
        path = os.path.join(cache_dir, name)
        fresh = (
            os.path.exists(path)
            and not refresh
            and (time.time() - os.path.getmtime(path)) <= max_age
        )
        outcome: Optional[str] = None
        if not offline and not fresh:
            try:
                _download(name, path, timeout, retries=retries, rate_limit=rate_limit)
                outcome = "network"
            except (urllib.error.URLError, RuntimeError, OSError, ValueError) as exc:
                if not os.path.exists(path):
                    if required:
                        raise RuntimeError(
                            "下载 %s 失败且本地无缓存: %s" % (name, exc)
                        ) from exc
                    outcome = "absent"  # 可选文件在线也失败 -> 跳过
                else:
                    outcome = "stale-cache(%s)" % exc
        elif not offline:
            outcome = "cache"

        if outcome is None:
            if not os.path.exists(path):
                if required:
                    raise FileNotFoundError(
                        "离线模式缺少索引缓存: %s（请先在线执行一次生成缓存，"
                        "或用 --index-dir 指向已有缓存）" % path
                    )
                outcome = "absent"
            else:
                outcome = "cache"
        if outcome != "absent":
            # 离线/缓存命中时也做完整性校验，损坏缓存给出明确提示
            try:
                validate_index_file(name, path)
            except ValueError as exc:
                if required:
                    raise RuntimeError(
                        "索引缓存损坏 %s: %s（请删掉该文件后重新在线同步）"
                        % (path, exc)
                    ) from exc
                outcome = "absent"
            src_map[name] = outcome
    return cache_dir, src_map


# ---------------------------------------------------------------------------
# 数据解析
# ---------------------------------------------------------------------------


def _display_vendor(vendor: str) -> str:
    v = (vendor or "").strip()
    if not v:
        return ""
    return VENDOR_DISPLAY.get(v.lower(), v)


def _upper(text: Any) -> str:
    return (str(text or "")).strip().upper()


def _clean_model(value: Any) -> str:
    text = str(value or "").strip()
    upper = text.upper()
    for tag in (" DO NOT USE", " DO NOT USE!"):
        if upper.endswith(tag):
            text = text[: len(text) - len(tag)].rstrip()
            break
    return text


def _as_str_list(value: Any) -> List[str]:
    """把字符串/标量/列表统一成字符串列表（避免对单个 str 做字符拆分）。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out = []
        for x in value:
            if x is None:
                continue
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, (int, float)):
                out.append(str(x))
        return out
    return [str(value)]


def build_mark_index(raw: Any) -> Dict[str, List[Dict[str, str]]]:
    """
    mdb.json -> {大写标记码: [{"vendor": 显示名, "model": 型号}, ...]}
    同码多型号（SpecTek）全部保留，由调用方决定是否歧义。
    """
    out: Dict[str, List[Dict[str, str]]] = {}
    if not isinstance(raw, dict):
        return out
    for vendor, block in raw.items():
        if not isinstance(block, dict):
            continue
        for code, value in block.items():
            items = value if isinstance(value, list) else [value]
            models: List[str] = []
            for it in items:
                if isinstance(it, dict):
                    it = it.get("pn") or it.get("partNumber") or it.get("model") or ""
                m = _clean_model(it)
                if m and m not in models:
                    models.append(m)
            if not models:
                continue
            key = _upper(code)
            out.setdefault(key, [])
            for m in models:
                rec = {"vendor": _display_vendor(vendor), "model": m}
                if rec not in out[key]:
                    out[key].append(rec)
    return out


def _pn_vendor_from_list(raw: Any, pn_key: str, vendor_key: str) -> Dict[str, str]:
    """从 [{pn:..., vendor:...}, ...] 建 pn -> 厂商显示名。"""
    out: Dict[str, str] = {}
    if not isinstance(raw, list):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pn = _upper(entry.get(pn_key))
        vendor = _display_vendor(entry.get(vendor_key))
        if pn and vendor and pn not in out:
            out[pn] = vendor
    return out


def build_pn_vendor(dram_raw: Any, managed_raw: Any = None) -> Dict[str, str]:
    """聚合 dram-pn / managed-nand-pn 的 pn -> 厂商映射（首见优先）。"""
    merged: Dict[str, str] = {}
    for raw, pk, vk in (
        (dram_raw, "pn", "vendor"),
        (managed_raw, "pn", "vendor"),
        (managed_raw, "partNumber", "manufacturer"),
        (managed_raw, "model", "manufacturer"),
    ):
        if not raw:
            continue
        for pn, vendor in _pn_vendor_from_list(raw, pk, vk).items():
            merged.setdefault(pn, vendor)
    return merged


_IMPORT_RE = __import__("re")
_HEX_ID_RE = _IMPORT_RE.compile(r"^[0-9A-F]{4,24}$")

# fdb 厂商块里值得透出的元数据：内部键 -> 展示键
_FDB_META_KEYS = (
    ("l", "die_codename"),
    ("c", "cell_level"),
    ("pc", "part_class"),
    ("pkg", "package"),
    ("sg", "speed_grade"),
    ("vol", "voltage"),
)


def _fdb_meta(value: Dict[str, Any]) -> Dict[str, str]:
    meta: Dict[str, str] = {}
    for inner, out_key in _FDB_META_KEYS:
        v = value.get(inner)
        if isinstance(v, str) and v.strip():
            meta[out_key] = v.strip()
    return meta


def build_fdb(raw: Any) -> Dict[str, List[Dict[str, Any]]]:
    """
    fdb.json -> {大写 Flash ID: [记录, ...]}

    记录统一形态（含 iddb 汇总块与厂商块的差异）：
        iddb 块（Flash ID 为键）：{vendor: "", controllers: [...], names: [...]}
        厂商块（料号为键）      ：{vendor: 显示名, pn: 料号,
                                   controllers: [...], meta: {die_codename: ...}}
    """
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not isinstance(raw, dict):
        return out
    for vendor, block in raw.items():
        if not isinstance(block, dict) or vendor in ("info", "schemaVersion"):
            continue

        if vendor == "iddb":
            # 块键本身就是 Flash ID，值含 t(控制器) / n(厂商+料号名)
            for key, value in block.items():
                if not isinstance(value, dict):
                    continue
                if not _HEX_ID_RE.match(_upper(key)):
                    continue
                rec = {
                    "vendor": "",
                    "controllers": _as_str_list(value.get("t")),
                    "names": _as_str_list(value.get("n")),
                }
                bucket = out.setdefault(_upper(key), [])
                if rec not in bucket:
                    bucket.append(rec)
            continue

        # 厂商块：键为已知料号，值含 id 列表 + t(控制器) + 元数据
        vdisplay = _display_vendor(vendor)
        for key, value in block.items():
            if not isinstance(value, dict):
                continue
            ids = _as_str_list(value.get("id"))
            if not ids:
                continue
            meta = _fdb_meta(value)
            rec: Dict[str, Any] = {
                "vendor": vdisplay,
                "pn": str(key).strip(),
                "controllers": _as_str_list(value.get("t")),
            }
            if meta:
                rec["meta"] = meta
            for fid in ids:
                fid = _upper(fid)
                if not _HEX_ID_RE.match(fid):
                    continue
                bucket = out.setdefault(fid, [])
                if rec not in bucket:
                    bucket.append(rec)
    return out


def _dedupe(rows: List[Dict[str, Any]], keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for r in rows:
        k = tuple(r.get(key) for key in keys)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# 统一检索对象
# ---------------------------------------------------------------------------


class FdnextIndexes:
    """整合标记码/料号/fdb/本地目录的一站式检索对象（进程内常驻）。"""

    def __init__(
        self,
        mark_index: Optional[Dict[str, List[Dict[str, str]]]] = None,
        pn_vendor: Optional[Dict[str, str]] = None,
        fdb: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        catalog: Optional[List[Dict[str, Any]]] = None,
    ):
        self.mark_index: Dict[str, List[Dict[str, str]]] = mark_index or {}
        self.pn_vendor: Dict[str, str] = pn_vendor or {}
        self.fdb: Dict[str, List[Dict[str, Any]]] = fdb or {}
        self.catalog: List[Dict[str, Any]] = catalog or []

    # -- 查询 -----------------------------------------------------------

    def lookup_marking(self, code: str) -> List[Dict[str, str]]:
        """FBGA/标记码精确反查 -> [{vendor, model}]（含同码多型号）。"""
        return list(self.mark_index.get(_upper(code), []))

    def lookup_pn(self, pn: str) -> Optional[str]:
        """完整料号 -> 厂商显示名（找不到返回 None）。"""
        return self.pn_vendor.get(_upper(pn))

    def lookup_flash(self, flash_id: str) -> List[Dict[str, Any]]:
        """Flash ID -> fdb 记录列表（控制器/料号名/厂商元数据）。"""
        return list(self.fdb.get(_upper(flash_id), []))

    def fuzzy_catalog(self, query: str, top_n: int = 5) -> List[Dict[str, Any]]:
        """本地 CSV 目录模糊搜索，返回前 top_n 条候选。"""
        if not self.catalog or not query.strip():
            return []
        from src.search import search

        scored = search(query, self.catalog, top_n=top_n)
        rows = []
        for rec, score, matched in scored:
            rows.append({
                "part_number": rec.get("part_number", ""),
                "model": rec.get("model", ""),
                "manufacturer": rec.get("manufacturer", ""),
                "type": rec.get("type", ""),
                "capacity": rec.get("capacity", ""),
                "score": round(float(score), 3),
                "matched_field": matched,
            })
        return _dedupe(rows, ("part_number",))

    # -- 计数 -----------------------------------------------------------

    def counts(self) -> Dict[str, int]:
        return {
            "marks": len(self.mark_index),
            "pn": len(self.pn_vendor),
            "fdb_ids": len(self.fdb),
            "catalog": len(self.catalog),
        }

    # -- 工厂 -----------------------------------------------------------

    @classmethod
    def from_raws(
        cls,
        mdb_raw: Optional[Any] = None,
        dram_raw: Optional[Any] = None,
        managed_raw: Optional[Any] = None,
        fdb_raw: Optional[Any] = None,
        catalog_path: Optional[str] = None,
    ) -> "FdnextIndexes":
        mark_index = build_mark_index(mdb_raw) if mdb_raw is not None else None
        pn_vendor = build_pn_vendor(dram_raw, managed_raw) if dram_raw is not None else None
        if mark_index is not None and pn_vendor is not None:
            # mdb 值里的完整型号（MT60B2G8RZ-56B:D 等）也可反查厂商
            for rows in mark_index.values():
                for r in rows:
                    if r["model"] and _upper(r["model"]) not in pn_vendor:
                        pn_vendor[_upper(r["model"])] = r["vendor"]
        fdb = build_fdb(fdb_raw) if fdb_raw is not None else None
        catalog: Optional[List[Dict[str, Any]]] = None
        if catalog_path:
            catalog = ChipDatabase(catalog_path).list_records()
        return cls(
            mark_index=mark_index or {},
            pn_vendor=pn_vendor or {},
            fdb=fdb or {},
            catalog=catalog or [],
        )

    @classmethod
    def load(
        cls,
        cache_dir: Optional[str] = None,
        *,
        offline: bool = False,
        refresh: bool = False,
        catalog_path: Optional[str] = None,
        include_catalog: bool = True,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_age: float = DEFAULT_MAX_AGE_SECONDS,
        retries: int = DEFAULT_RETRIES,
        rate_limit: float = DEFAULT_RATE_LIMIT,
    ) -> Tuple["FdnextIndexes", Dict[str, str]]:
        """从缓存装载索引（离线）或按需下载（在线）。返回 (索引, 来源描述)。

        include_catalog=False 时完全不触碰本地 CSV（上游独立模式）；
        只读当下实际存在的文件：可选索引缺失时自动跳过（离线不阻塞主干功能）。
        """
        import json

        cache_dir, src_map = ensure_index_cache(
            cache_dir,
            offline=offline, refresh=refresh,
            timeout=timeout, max_age=max_age,
            retries=retries, rate_limit=rate_limit,
        )

        def read(name: str):
            with open(os.path.join(cache_dir, name), "r", encoding="utf-8") as f:
                return json.load(f)

        has = lambda name: os.path.exists(os.path.join(cache_dir, name))  # noqa: E731
        if not include_catalog:
            catalog_path = None
        elif not catalog_path:
            catalog_path = default_database_path()
        idx = cls.from_raws(
            mdb_raw=read("mdb.json") if has("mdb.json") else None,
            dram_raw=read("dram-pn.json") if has("dram-pn.json") else None,
            managed_raw=read("managed-nand-pn.json") if has("managed-nand-pn.json") else None,
            fdb_raw=read("fdb.json") if has("fdb.json") else None,
            catalog_path=catalog_path,
        )
        return idx, src_map


def resolve_engine() -> Any:
    """快捷创建规则解码引擎（快照规则，全离线）。"""
    from src.fdnext import load_engine

    return load_engine()
