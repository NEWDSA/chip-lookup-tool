# -*- coding: utf-8 -*-
"""
src/fdnext/constants.py
-----------------------
上游 src/constants.ts 的常量移植。
"""

# 与上游 UNKNOWN = "Unknown" 一致
UNKNOWN = "Unknown"

# 厂商别名规整表（VENDOR_PATCH）
VENDOR_PATCH: dict = {
    "sandisk": "sndk",
    "san disk": "sndk",
    "sndk": "sndk",
    "westerndigital": "sndk",
    "western digital": "sndk",
    "wd": "sndk",
    "toshiba": "kioxia",
    "toshiba-iver": "kioxia",
    "hynix": "skhynix",
    "giga device": "gigadevice",
    "gd": "gigadevice",
    "兆易创新": "gigadevice",
    "septeck": "spectek",
    "stm": "st",
}

LANGUAGES = ["chs", "eng"]
