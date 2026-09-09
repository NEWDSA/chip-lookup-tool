# -*- coding: utf-8 -*-
"""
chip_lookup.paths
-----------------
定位「数据文件」和「默认数据库」位置。
根据运行环境自动判断：
    1) 用户级（exe 旁边的 chip_database.xlsx）     -- 持久、可读写
    2) exe 内嵌（PyInstaller _MEIPASS/data/...）  -- 只读，作为初始种子
    3) 开发模式（源码 src/ 的 ../data/...）

保证：upsert/delete/import 永远写入"持久层"；首次启动时自动把种子拷贝过去。
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Tuple


def program_root() -> str:
    """返回程序运行时所在根目录（exe 所在目录 或 脚本目录）。"""
    if getattr(sys, "frozen", False):
        # 由 PyInstaller 打包后
        return os.path.dirname(sys.executable)
    # 开发模式：src/database.py → 项目根
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_root() -> str:
    """PyInstaller 临时解压根 _MEIPASS；非打包模式返回项目根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return program_root()


def resolve_database_path(filename: str = "chip_database.xlsx") -> Tuple[str, bool]:
    """
    返回 (数据库绝对路径, 是否用户级可写)。
    优先级：
        - 用户级：<program_root>/data/<filename>   持久
        - 内嵌  ：<_MEIPASS>/data/<filename>      只读
        - 内嵌  ：<program_root>/data/<filename>  项目自带
    不会修改文件。
    """
    user_path = os.path.join(program_root(), "data", filename)
    if os.path.exists(user_path):
        return user_path, True

    bundle_path = os.path.join(bundle_root(), "data", filename)
    if os.path.exists(bundle_path):
        return bundle_path, False

    return user_path, True  # 还没有，让调用方去 seed


def ensure_user_database(filename: str = "chip_database.xlsx") -> str:
    """
    保证用户级数据库存在。如不存在，把 bundle 内的种子拷一份过去；
    都不存在则创建一个只含表头的空 xlsx。
    返回最终的可写数据库绝对路径。
    """
    user_path = os.path.join(program_root(), "data", filename)
    bundle_path = os.path.join(bundle_root(), "data", filename)

    if os.path.exists(user_path):
        return user_path

    os.makedirs(os.path.dirname(user_path), exist_ok=True)
    if os.path.exists(bundle_path):
        shutil.copyfile(bundle_path, user_path)
    else:
        from .database import DEFAULT_FIELDS, _write_xlsx  # 延迟 import，避免循环
        _write_xlsx(user_path, [])
    return user_path
