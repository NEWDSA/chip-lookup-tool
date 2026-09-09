# -*- coding: utf-8 -*-
"""
tools/sync_upstream.py
----------------------
纯 Python 上游数据同步器 —— 不依赖 Node.js，也不依赖任何第三方 PyPI 包。

数据源：iTXTech/fdnext（https://github.com/iTXTech/fdnext，AGPL-3.0）
发布的 JSON 索引资源（GitHub raw，urllib 直接下载即可，无需 npm / Node）：

    mdb.json        Micron FBGA 顶标码(15427) + SpecTek 标记码(2153) -> 完整型号
    dram-pn.json    DRAM 料号索引(3169 条 [{vendor, pn}])，覆盖 13 家厂商

流程：抓取 -> 清洗 -> 与 ChipLookup 的 CSV 数据库做「只填空」合并 -> 写回 CSV。

设计约束：
    1) 已填写的字段永不被覆盖 —— 人工维护的数据优先级最高；
    2) 不引入规则/解码引擎，只补「索引本身能确认」的字段（model/manufacturer）；
    3) 全程仅用 Python 标准库（urllib/json/csv），不调用任何外部进程。

用法：
    python tools/sync_upstream.py                     # 在线同步到默认库
    python tools/sync_upstream.py --dry-run           # 只预览，不改盘
    python tools/sync_upstream.py --offline           # 只用本地缓存（无网/CI）
    python tools/sync_upstream.py --db <other.xlsx>    # 指定目标库
    python tools/sync_upstream.py --export-index mdb_clean.csv
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# 让 "python tools/sync_upstream.py" 也能找到 src/ 包
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.database import ChipDatabase, default_database_path  # noqa: E402

# ---------------------------------------------------------------------------
# 上游资源定义
# ---------------------------------------------------------------------------

# 多源降级：国内可达镜像在前，GitHub raw 兜底最后。
# 工厂/内网环境 raw.githubusercontent.com 常被墙或极慢，公共加速镜像可绕过；
# 顺序即优先级，逐个尝试，全部失败才报错。
UPSTREAM_BASES = (
    "https://gh.llkk.cc/https://raw.githubusercontent.com/iTXTech/fdnext/"
    "master/packages/core/resources/",
    "https://ghproxy.net/https://raw.githubusercontent.com/iTXTech/fdnext/"
    "master/packages/core/resources/",
    "https://gcore.jsdelivr.net/gh/iTXTech/fdnext@master/packages/core/resources/",
    "https://fastly.jsdelivr.net/gh/iTXTech/fdnext@master/packages/core/resources/",
    "https://cdn.jsdelivr.net/gh/iTXTech/fdnext@master/packages/core/resources/",
    "https://raw.githubusercontent.com/iTXTech/fdnext/"
    "master/packages/core/resources/",
)

RESOURCES = {
    "mdb.json": "Micron/SpecTek 标记码 -> 型号数据库",
    "dram-pn.json": "DRAM 料号索引(厂商 + 料号)",
}

DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600  # 缓存 7 天内复用
DEFAULT_TIMEOUT_SECONDS = 30
USER_AGENT = "ChipLookup-sync/1.0 (pure-python; no node)"

# 上游 vendor 小写键 -> 本地数据库习惯写法
VENDOR_DISPLAY = {
    "micron": "Micron",
    "spectek": "SpecTek",
    "samsung": "Samsung",
    "skhynix": "SK Hynix",
    "nanya": "Nanya",
    "elpida": "Elpida",
    "cxmt": "CXMT",
    "issi": "ISSI",
    "winbond": "Winbond",
    "esmt": "ESMT",
    "etron": "Etron",
    "gigadevice": "GigaDevice",
    "biwin": "Biwin",
    "longsys": "Longsys",
    "macronix": "Macronix",
}

# Spectek 顶标码可能映射到多个型号，存在歧义时不写 model（避免猜错）
_AMBIGUOUS_MODEL = object()  # type: ignore


# ---------------------------------------------------------------------------
# 缓存与抓取
# ---------------------------------------------------------------------------


def default_cache_dir() -> str:
    """按平台返回默认上游缓存目录（不依赖第三方库）。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or ROOT
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "chiplookup", "upstream")


def _cache_path(cache_dir: str, name: str) -> str:
    return os.path.join(cache_dir, name)


def _is_fresh(path: str, max_age: float) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age <= max_age


def _download(cache_dir: str, name: str, timeout: float) -> bytes:
    """按 UPSTREAM_BASES 顺序尝试下载，全部失败才抛错。返回字节内容。"""
    last_exc: Exception | None = None
    for base in UPSTREAM_BASES:
        url = base + name
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            os.makedirs(cache_dir, exist_ok=True)
            tmp = _cache_path(cache_dir, name) + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, _cache_path(cache_dir, name))
            return data
        except (urllib.error.URLError, OSError) as exc:
            last_exc = exc
    raise RuntimeError(
        "下载 %s 失败（已尝试 %d 个源）: %s" % (name, len(UPSTREAM_BASES), last_exc)
    ) from last_exc


def fetch_json(
    cache_dir: str,
    name: str,
    *,
    offline: bool,
    refresh: bool,
    timeout: float,
    max_age: float,
) -> tuple:
    """
    返回 (json 对象, 来源描述, 元信息 dict)。
    在线模式：缓存新鲜则复用；否则下载并替换缓存。
    离线模式：只读缓存，缺失则抛错。
    """
    path = _cache_path(cache_dir, name)
    info: dict = {}

    if not offline:
        cache_fresh = os.path.exists(path) and not refresh and _is_fresh(path, max_age)
        if not cache_fresh:
            try:
                data = _download(cache_dir, name, timeout)
                info["bytes"] = len(data)
                info["source"] = "network"
                return json.loads(data.decode("utf-8")), "在线下载", info
            except (urllib.error.URLError, OSError, ValueError) as exc:
                # 下载失败但本地有旧缓存 -> 降级复用，避免整次任务失败
                if os.path.exists(path):
                    info["download_error"] = str(exc)
                    info["source"] = "stale-cache"
                else:
                    raise RuntimeError(
                        "下载 %s 失败且本地无缓存: %s" % (name, exc)
                    ) from exc

    if not os.path.exists(path):
        raise FileNotFoundError(
            "离线模式缺少缓存文件: %s（先在线跑一次生成缓存，或用 --cache-dir 指定）" % path
        )
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    info["bytes"] = os.path.getsize(path)
    info.setdefault("source", "cache")
    return obj, "本地缓存", info


# ---------------------------------------------------------------------------
# 清洗
# ---------------------------------------------------------------------------


def clean_model(value) -> str:
    """清洗上游型号字符串：去空白、去 'DO NOT USE' 等丢弃标记。"""
    text = (value or "").strip()
    upper = text.upper()
    for tag in (" DO NOT USE", " DO NOT USE!"):
        if upper.endswith(tag):
            text = text[: len(text) - len(tag)].rstrip()
            break
    return text


def split_models(value):
    """
    把 mdb 条目值统一成去重后的型号候选列表。
    micron 块为字符串；spectek 块为数组；对上游历史/异构数据做防御。
    """
    seen = []
    items = value if isinstance(value, (list, tuple)) else [value]
    for it in items:
        if isinstance(it, dict):  # 极端异构：取常见键兜底
            it = it.get("pn") or it.get("partNumber") or it.get("model") or ""
        m = clean_model(it)
        if m:
            seen.append(m)
    # 去重保序
    out = []
    for m in seen:
        if m not in out:
            out.append(m)
    return out


def build_mdb_index(raw) -> dict:
    """
    归一化 mdb.json -> dict[大写码] = {"vendor": str, "models": [..]}
    另建 pn 反查表 dict[大写型号] = vendor_lower，用于按料号找厂商。
    """
    index: dict = {}
    pn_to_vendor: dict = {}
    if not isinstance(raw, dict):
        raise ValueError("mdb.json 顶层必须是 {vendor: {...}} 对象")
    for vendor, block in raw.items():
        if not isinstance(block, dict):
            continue
        vkey = vendor.strip().lower()
        for code, value in block.items():
            models = split_models(value)
            if not models:
                continue
            key = code.strip().upper()
            if key and key not in index:  # 同码多厂商时保留先出现的
                index[key] = {"vendor": vkey, "models": models}
            for m in models:
                pn_to_vendor.setdefault(m.upper(), vkey)
    return index, pn_to_vendor


def build_dram_index(raw) -> dict:
    """归一化 dram-pn.json -> dict[大写料号] = vendor_lower。"""
    pn_to_vendor: dict = {}
    if not isinstance(raw, list):
        raise ValueError("dram-pn.json 顶层必须是 [{vendor, pn}, ...] 数组")
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        pn = clean_model(entry.get("pn"))
        vendor = (entry.get("vendor") or "").strip().lower()
        if pn and vendor:
            pn_to_vendor.setdefault(pn.upper(), vendor)
    return pn_to_vendor


def vendor_display(vendor_lower: str) -> str:
    return VENDOR_DISPLAY.get(vendor_lower, vendor_lower.capitalize())


# ---------------------------------------------------------------------------
# 合并计划
# ---------------------------------------------------------------------------


def _upper(text) -> str:
    return (text or "").strip().upper()


def plan_record(rec: dict, mdb_index: dict, mdb_pn_vendor: dict, dram_pn_vendor: dict) -> dict:
    """
    计算单条记录应填写的字段（仅当该字段当前为空）。
    返回 {"fills": {字段: 值}, "note": 说明}。note 可能带 mismatch 提示。
    """
    fills: dict = {}
    note = ""
    pn = _upper(rec.get("part_number"))
    model = _upper(rec.get("model"))

    hit = mdb_index.get(pn)
    if hit:  # 顶标码命中 mdb（Micron / SpecTek）
        if hit["models"] and not (rec.get("model") or "").strip():
            if len(hit["models"]) == 1:
                fills["model"] = hit["models"][0]
            else:
                note = "命中 %s 标记码但对应多个型号，model 留空待人工确认" % pn
        if not (rec.get("manufacturer") or "").strip():
            fills["manufacturer"] = vendor_display(hit["vendor"])
        # 已填 model 与上游不一致 -> 只提示，不覆盖
        if (rec.get("model") or "").strip() and hit["models"]:
            cur = _upper(rec.get("model"))
            if cur not in [_upper(m) for m in hit["models"]]:
                note = "已有 model 与上游不符(上游: %s)" % hit["models"][0]
        return {"fills": fills, "note": note}

    # 料号本身是完整料号（dram-pn 索引 / mdb 值反查）
    vendor = dram_pn_vendor.get(pn) or mdb_pn_vendor.get(pn)
    if vendor:
        if not (rec.get("manufacturer") or "").strip():
            fills["manufacturer"] = vendor_display(vendor)
        # 与本地既有约定一致：料号即完整型号时 model 记录为同一串
        if not (rec.get("model") or "").strip() and pn:
            fills["model"] = rec.get("part_number", "").strip()
        return {"fills": fills, "note": note}

    # 现有 model 命中 dram-pn（如只填了型号、漏了厂商）
    if model and not (rec.get("manufacturer") or "").strip():
        vendor = dram_pn_vendor.get(model) or mdb_pn_vendor.get(model)
        if vendor:
            fills["manufacturer"] = vendor_display(vendor)
            note = "由型号反查厂商"
    return {"fills": fills, "note": note}


def build_plan(records: list, mdb_index: dict, mdb_pn_vendor: dict, dram_pn_vendor: dict) -> list:
    plan = []
    for rec in records:
        res = plan_record(rec, mdb_index, mdb_pn_vendor, dram_pn_vendor)
        if res["fills"] or res["note"]:
            plan.append({"part_number": rec.get("part_number", ""), **res})
    return plan


# ---------------------------------------------------------------------------
# CSV 导出（可选：把清洗后的上游索引落成 CSV）
# ---------------------------------------------------------------------------


def export_index_csv(mdb_index: dict, dst: str) -> int:
    import csv

    rows = []
    for code, info in sorted(mdb_index.items()):
        for m in info["models"]:
            rows.append((code, vendor_display(info["vendor"]), m))
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["mark_code", "manufacturer", "model"])
        writer.writerows(rows)
    return len(rows)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run(args) -> int:
    cache_dir = args.cache_dir or default_cache_dir()
    t0 = time.time()

    # 1) 抓取 / 读缓存
    print("[sync] 缓存目录: %s" % cache_dir)
    payloads = {}
    for name, desc in RESOURCES.items():
        obj, src, info = fetch_json(
            cache_dir,
            name,
            offline=args.offline,
            refresh=args.refresh,
            timeout=args.timeout,
            max_age=args.max_age,
        )
        size_kb = info.get("bytes", 0) / 1024.0
        extra = ""
        if "download_error" in info:
            extra = "（下载失败，降级使用旧缓存: %s）" % info["download_error"]
        print("[sync] 载入 %-14s %-6s %8.1f KB  %s%s"
              % (name, src, size_kb, desc, extra))
        payloads[name] = obj

    # 2) 清洗
    mdb_index, mdb_pn_vendor = build_mdb_index(payloads["mdb.json"])
    dram_pn_vendor = build_dram_index(payloads["dram-pn.json"])
    print("[sync] 清洗完成: mdb 标记码 %d 条 / mdb 料号 %d 个 / dram-pn 料号 %d 个"
          % (len(mdb_index), len(mdb_pn_vendor), len(dram_pn_vendor)))

    # 3) 可选：导出清洗后的上游索引 CSV
    if args.export_index:
        n = export_index_csv(mdb_index, args.export_index)
        print("[sync] 已导出清洗后索引 %d 行 -> %s" % (n, args.export_index))

    # 4) 合并计划
    db = ChipDatabase(args.db)
    records = db.list_records()
    plan = build_plan(records, mdb_index, mdb_pn_vendor, dram_pn_vendor)

    filled_model = sum(1 for p in plan if "model" in p["fills"])
    filled_manu = sum(1 for p in plan if "manufacturer" in p["fills"])
    print("[sync] 本地记录 %d 条；本次可补: model %d 条 / manufacturer %d 条"
          % (len(records), filled_model, filled_manu))

    changed = 0
    for p in plan:
        fills = p["fills"]
        if not fills:
            continue
        if args.verbose:
            detail = ", ".join("%s=%s" % (k, v) for k, v in fills.items())
            print("  - %-16s -> %s" % (p["part_number"], detail))
        if args.dry_run:
            continue
        rec = db.get(p["part_number"]) or {}
        for k, v in fills.items():
            if not (rec.get(k) or "").strip():
                rec[k] = v
        db.upsert(rec)
        changed += 1

    mismatches = [p for p in plan if not p["fills"] and p["note"]]
    ambiguous = [p for p in plan if p["fills"].get("model") is None and "多个型号" in p["note"]]

    if args.dry_run:
        print("[sync] dry-run：以上为预览，未写盘。")
    elif changed:
        db.save()
        print("[sync] 已合并 %d 条并写回 %s" % (changed, db.csv_path))
    else:
        print("[sync] 无可填空字段，未写盘（保持不变）。")

    if mismatches:
        print("[sync] 审计提示：%d 条已有 model 与上游标记库不一致（不覆盖，仅提醒）" % len(mismatches))
        for p in mismatches[: args.show_limit]:
            print("  ! %-16s %s" % (p["part_number"], p["note"]))
    if ambiguous:
        print("[sync] 审计提示：%d 条标记码命中多型号，model 留空待人工确认"
              % len(ambiguous))

    print("[sync] 完成，耗时 %.2fs" % (time.time() - t0))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="ChipLookup 上游数据同步器（纯 Python，无 Node.js 依赖）",
    )
    parser.add_argument("--db", default=None,
                        help="目标数据库，默认 data/chip_database.xlsx")
    parser.add_argument("--cache-dir", default=None,
                        help="上游 JSON 缓存目录（默认平台用户缓存）")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式：只读本地缓存，不联网")
    parser.add_argument("--refresh", action="store_true",
                        help="忽略缓存新鲜度，强制重新下载")
    parser.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE_SECONDS,
                        help="缓存新鲜期秒数（默认 %d，即 7 天）" % DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                        help="HTTP 超时秒数（默认 %d）" % DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--dry-run", action="store_true",
                        help="只预览将写入的字段，不修改 CSV")
    parser.add_argument("--verbose", action="store_true",
                        help="逐条打印将填写的内容")
    parser.add_argument("--export-index", default=None, metavar="CSV",
                        help="把清洗后的上游标记码索引导出为 CSV")
    parser.add_argument("--show-limit", type=int, default=20,
                        help="审计提示最多打印条数（默认 20）")
    args = parser.parse_args(argv)
    args.db = args.db or default_database_path()
    try:
        return run(args)
    except (RuntimeError, FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print("[sync] 错误: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
