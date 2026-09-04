# -*- coding: utf-8 -*-
"""
src/fdnext
----------
纯 Python 实现的「料号解码」引擎 —— 移植自 iTXTech/fdnext
（https://github.com/iTXTech/fdnext，AGPL-3.0）。

设计：上游解码规则是「数据不是代码」（JSON token packs），
本包把上游的规则 JSON 与一个小型解释器一起移植到纯 Python，
不依赖 Node.js / npm / 任何第三方 PyPI 包。

本包只做「解码」本身；与 ChipLookup CSV 的对接在 tools/decode.py。
"""

from __future__ import annotations

from .engine import FdnextEngine, load_engine

__version__ = "0.1.0"

__all__ = ["FdnextEngine", "load_engine", "__version__"]
