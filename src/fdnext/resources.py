# -*- coding: utf-8 -*-
"""
src/fdnext/resources.py
-----------------------
纯标准库：拉取 / 缓存 / 组装 fdnext-core 的规则数据（rules as data）。

上游的"解码规则"是 JSON 而不是代码，位于
    packages/core/src/decodepack/
      rules/default-rules.ts          拼接顺序（part 规则清单）
      rules/packs/*.json              part 解码规则包（每包 = PartDecodeSpec[]）
      identifier/default-rules.ts     NAND Flash-ID 解码规则清单
      identifier/packs/*.json         identifier 规则包
      rules/tables/nand-die-profile.json  共享查表

拼接顺序来自 TS 清单里 spread 数组的顺序 —— 它决定同优先级规则的 tie-break，
因此这里**运行时解析 TS 清单本身**而非硬编码文件名列表：上游新增/改名规则包时
无需改本文件即可跟随。

缓存策略与 tools/sync_upstream.py 一致：7 天新鲜期、失败降级旧缓存、可离线。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 让 "python -m src.fdnext.resources" 直接可用
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DECODEPACK_RAW_BASE = (
    "https://raw.githubusercontent.com/iTXTech/fdnext/"
    "master/packages/core/src/decodepack/"
)

# 钉死的上游版本：@itxtech/fdnext-core@3.2.0（engine 86，与 golden 同源）。
# 仓库内置快照 src/fdnext/data/fdnext-core-3.2.0/ 由官方 npm 包导出：
#   part-specs.json        = defaultPartDecodeSpecs（203 条）
#   identifier-specs.json  = defaultIdentifierDecodeSpecs（27 条）
#   nand-die-profile.json  = nandDieProfileTable（306 键）
# 有快照时引擎默认从快照装载 —— 完全离线、确定性、与 master 无漂移。
SNAPSHOT_VERSION = "3.2.0"
SNAPSHOT_DIRNAME = "fdnext-core-" + SNAPSHOT_VERSION

PART_RULES_TS = "rules/default-rules.ts"
IDENTIFIER_RULES_TS = "identifier/default-rules.ts"
NAND_DIE_TABLE_REL = "rules/tables/nand-die-profile.json"

CACHE_SUBDIR = "fdnext-rules"
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600
DEFAULT_TIMEOUT_SECONDS = 30
USER_AGENT = "ChipLookup-fdnext/0.1 (pure-python; no node)"

_IMPORT_LINE_RE = re.compile(r'import\s+(\w+)\s+from\s+"\./packs/([\w.-]+\.json)"')
_SPREAD_RE = re.compile(r"\.\.\.(\w+)")


def manifest_name(rules_ts_rel: str) -> str:
    """TS 清单在缓存里的扁平文件名。"""
    return rules_ts_rel.replace("/", "_").replace("default-rules", "manifest")


# ---------------------------------------------------------------------------
# TS 清单解析：文件顺序 = 规则拼接顺序
# ---------------------------------------------------------------------------


def ordered_pack_files(rules_ts_text: str) -> List[str]:
    """解析 default-rules.ts，返回按 spread 顺序排列的 pack 文件名。"""
    alias_to_file = dict(_IMPORT_LINE_RE.findall(rules_ts_text))
    # 数组区域：`export const xxx = [ ... ] as ...`
    start = rules_ts_text.find("= [")
    end = rules_ts_text.find("] as", start)
    if start < 0 or end < 0:
        raise ValueError("无法从 TS 清单解析出规则数组")
    names = _SPREAD_RE.findall(rules_ts_text[start:end])
    files = [alias_to_file[n] for n in names if n in alias_to_file]
    if len(files) != len(names):
        missing = sorted(set(names) - set(alias_to_file))
        raise ValueError("TS 清单中有别名找不到文件: %s" % ", ".join(missing))
    return files


# ---------------------------------------------------------------------------
# 缓存目录 / 下载（与 tools/sync_upstream.py 同一套约定）
# ---------------------------------------------------------------------------


def default_cache_root() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "chiplookup", CACHE_SUBDIR)


def _path_for(cache_root: str, kind: str, name: str) -> str:
    if kind == "table":
        return os.path.join(cache_root, name)
    return os.path.join(cache_root, kind, name)


def _download(url: str, dst: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, dst)
    return data


def _fetch_to_cache(
    url: str,
    dst: str,
    *,
    offline: bool,
    refresh: bool,
    timeout: float,
    max_age: float,
) -> str:
    """确保 dst 有新鲜缓存；在线失败且有旧缓存则降级。返回来源描述。"""
    fresh = os.path.exists(dst) and not refresh and (time.time() - os.path.getmtime(dst)) <= max_age
    if not offline:
        if not fresh:
            try:
                _download(url, dst, timeout)
                return "network"
            except (urllib.error.URLError, OSError) as exc:
                if not os.path.exists(dst):
                    raise RuntimeError("下载 %s 失败且本地无缓存: %s" % (url, exc)) from exc
                # 有旧缓存 -> 降级复用
                return "stale-cache(%s)" % exc
        return "cache"
    if not os.path.exists(dst):
        raise FileNotFoundError("离线模式缺少缓存: %s（请先在线跑一次生成缓存）" % dst)
    return "cache"


# ---------------------------------------------------------------------------
# 规则包下载 / 装载
# ---------------------------------------------------------------------------


@dataclass
class RulesBundle:
    """一次装载得到的规则数据（保持上游 JSON 原样，供 compiler 使用）。"""

    part_specs: List[dict] = field(default_factory=list)
    identifier_specs: List[dict] = field(default_factory=list)
    die_profile: Optional[list] = None  # nand-die-profile.json 原始内容（数组表）
    cache_root: str = ""
    part_files: List[str] = field(default_factory=list)
    identifier_files: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)


def ensure_rules_cache(
    cache_root: Optional[str] = None,
    *,
    offline: bool = False,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_age: float = DEFAULT_MAX_AGE_SECONDS,
) -> str:
    """把规则清单 + 全部规则包缓存到 cache_root（默认平台缓存目录）。返回 cache_root。"""
    cache_root = cache_root or default_cache_root()
    os.makedirs(cache_root, exist_ok=True)

    def fetch_packs(rel_dir: str, manifest_rel: str, kind: str) -> None:
        # 1) 清单 TS：在线下载以它为准（解析出 pack 列表与拼接顺序）
        ts_path = os.path.join(cache_root, manifest_name(manifest_rel))
        _fetch_to_cache(
            DECODEPACK_RAW_BASE + manifest_rel, ts_path,
            offline=offline, refresh=refresh, timeout=timeout, max_age=max_age,
        )
        with open(ts_path, "r", encoding="utf-8") as f:
            files = ordered_pack_files(f.read())
        # 2) 每个规则包
        for pack in files:
            _fetch_to_cache(
                DECODEPACK_RAW_BASE + rel_dir + pack, _path_for(cache_root, kind, pack),
                offline=offline, refresh=refresh, timeout=timeout, max_age=max_age,
            )

    fetch_packs("rules/packs/", PART_RULES_TS, "part")
    fetch_packs("identifier/packs/", IDENTIFIER_RULES_TS, "identifier")
    # 3) 共享查表
    _fetch_to_cache(
        DECODEPACK_RAW_BASE + NAND_DIE_TABLE_REL,
        _path_for(cache_root, "table", NAND_DIE_TABLE_REL.split("/")[-1]),
        offline=offline, refresh=refresh, timeout=timeout, max_age=max_age,
    )
    return cache_root


def _read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def snapshot_dir() -> Optional[str]:
    """返回内置快照目录（存在时），否则 None。快照与源码同目录分发，无需网络。"""
    candidate = os.path.join(HERE, "data", SNAPSHOT_DIRNAME)
    return candidate if os.path.isdir(candidate) else None


def load_bundle(
    cache_root: Optional[str] = None,
    *,
    offline: bool = False,
    refresh: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_age: float = DEFAULT_MAX_AGE_SECONDS,
) -> RulesBundle:
    """装载解码规则。

    优先使用仓库内置官方快照（src/fdnext/data/fdnext-core-3.2.0/）——
    该快照对应 @itxtech/fdnext-core@3.2.0，可完全离线、且与 master 分支无版本漂移。
    仅在快照缺失时回退到 GitHub master 的下载+缓存路径（历史行为）。
    """
    snap = snapshot_dir()
    if snap is not None:
        bundle = RulesBundle(cache_root="<snapshot>")
        bundle.part_specs = _read_json(os.path.join(snap, "part-specs.json"))
        bundle.identifier_specs = _read_json(os.path.join(snap, "identifier-specs.json"))
        bundle.die_profile = _read_json(os.path.join(snap, "nand-die-profile.json"))
        bundle.part_files = ["part-specs.json"]
        bundle.identifier_files = ["identifier-specs.json"]
        bundle.sources = {
            "rules": "snapshot:" + SNAPSHOT_DIRNAME,
            "npm": "@itxtech/fdnext-core@" + SNAPSHOT_VERSION,
        }
        return bundle
    cache_root = ensure_rules_cache(
        cache_root, offline=offline, refresh=refresh, timeout=timeout, max_age=max_age
    )

    def load_packs(kind: str, manifest_rel: str):
        manifest = os.path.join(cache_root, manifest_name(manifest_rel))
        with open(manifest, "r", encoding="utf-8") as f:
            files = ordered_pack_files(f.read())
        specs: List[dict] = []
        for pack in files:
            data = _read_json(_path_for(cache_root, kind, pack))
            if not isinstance(data, list):
                raise ValueError("规则包 %s/%s 不是数组" % (kind, pack))
            specs.extend(data)
        return files, specs

    bundle = RulesBundle(cache_root=cache_root)
    bundle.part_files, bundle.part_specs = load_packs("part", PART_RULES_TS)
    bundle.identifier_files, bundle.identifier_specs = load_packs("identifier", IDENTIFIER_RULES_TS)
    bundle.die_profile = _read_json(_path_for(cache_root, "table", NAND_DIE_TABLE_REL.split("/")[-1]))
    return bundle


# ---------------------------------------------------------------------------
# CLI：预取规则缓存
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="预取 fdnext 解码规则到本地缓存")
    ap.add_argument("--cache-dir", default=None, help="缓存目录（默认平台缓存/chiplookup/fdnext-rules）")
    ap.add_argument("--offline", action="store_true", help="只读缓存，不联网")
    ap.add_argument("--refresh", action="store_true", help="忽略新鲜期，强制重下")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    cache_root = ensure_rules_cache(
        args.cache_dir, offline=args.offline, refresh=args.refresh
    )
    bundle = load_bundle(args.cache_dir, offline=args.offline, refresh=args.refresh)
    print("缓存目录 : %s" % cache_root)
    print("part 规则包        : %d 个文件, %d 条 spec" % (len(bundle.part_files), len(bundle.part_specs)))
    print("identifier 规则包  : %d 个文件, %d 条 spec" % (len(bundle.identifier_files), len(bundle.identifier_specs)))
    profile = bundle.die_profile
    if isinstance(profile, dict):
        profile_n = "%d 键" % len(profile)
    else:
        profile_n = "%d 条" % len(profile) if profile is not None else "无"
    print("共享表 nand-die-profile : %s" % profile_n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
