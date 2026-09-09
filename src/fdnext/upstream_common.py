# -*- coding: utf-8 -*-
"""
src/fdnext/upstream_common.py
-----------------------------
fdnext 索引层与 tools/sync_upstream.py 共用的常量与工具函数。

为什么单列出来：
- fdnext/indexes.py 在运行时（打包后）需要 VENDOR_DISPLAY / default_cache_dir，
  但 tools/sync_upstream.py 不在打包产物里，不能跨目录 from sync_upstream import ...
- 直接放进 fdnext 包内，PyInstaller 静态分析能扫到，避免运行时 No module named
- tools/ 里几个脚本仍然依赖 sync_upstream 的本体实现（合并语义、下载等）
  不动它们；本模块仅承载「真正被 fdnext 复用的纯常量/纯函数」。

契约：
- VENDOR_DISPLAY：与 tools/sync_upstream.py 内的同名 dict 保持一致。
  增删厂商需同时改两处；忘改会让两套表漂移，
  这正是 fdnext/indexes.py 注释里警告过的「避免两套约定漂移」那句。
- default_cache_dir：与 tools/sync_upstream.py 内的同名函数行为一致。
  默认平台目录 + 'chiplookup/upstream' 子路径。
"""

from __future__ import annotations

import os
import sys

# 上游 vendor 小写键 -> 本地数据库习惯写法
# 与 tools/sync_upstream.py VENDOR_DISPLAY 保持同步。
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


def default_cache_dir() -> str:
    """按平台返回默认上游缓存目录（不依赖第三方库）。

    与 tools/sync_upstream.py 内同名函数语义一致：
    - Windows：%LOCALAPPDATA%\\chiplookup\\upstream
    - macOS：~/Library/Caches/chiplookup/upstream
    - 其他：$XDG_CACHE_HOME/chiplookup/upstream 或 ~/.cache/chiplookup/upstream
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "chiplookup", "upstream")
