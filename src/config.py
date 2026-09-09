# -*- coding: utf-8 -*-
"""
chip_lookup.config
-------------------
运行模式与数据源配置层。

三种运行模式：
    local     本地数据模式   —— 仅读取本地数据，屏蔽上游
    upstream  上游数据源模式  —— 仅从上游索引拉取，关闭本地数据
    hybrid    混合模式        —— 本地 + 上游融合检索

配置来源与优先级（高的覆盖低的）：
    命令行参数 > 配置文件 chiplookup.json > 内置默认值

配置文件为 JSON（仅用标准库 json），默认位于程序根目录
（exe 旁边 / 源码项目根），修改后 UI 或命令行随即生效。

参数按类型分组：模式/优先级等为运行参数；上游拉取相关（离线、刷新、
缓存、超时、重试、限流）统一归类为「网络」类型参数（键名仍为 upstream_*，
兼容历史配置），清单见 NETWORK_PARAMS。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# 模式常量
# ---------------------------------------------------------------------------

MODE_LOCAL = "local"
MODE_UPSTREAM = "upstream"
MODE_HYBRID = "hybrid"
MODES = (MODE_LOCAL, MODE_UPSTREAM, MODE_HYBRID)

MODE_LABELS = {
    MODE_LOCAL: "本地数据",
    MODE_UPSTREAM: "网络",  # 上游数据源统一以「网络」命名（internal 仍为 upstream）
    MODE_HYBRID: "混合",
}

# 混合模式优先级
HYBRID_LOCAL_FIRST = "local_first"        # 本地优先，缺失从上游补全
HYBRID_UPSTREAM_FIRST = "upstream_first"  # 上游优先，异常降级本地兜底
HYBRID_PRIORITIES = (HYBRID_LOCAL_FIRST, HYBRID_UPSTREAM_FIRST)

HYBRID_PRIORITY_LABELS = {
    HYBRID_LOCAL_FIRST: "本地优先（缺失从上游补全）",
    HYBRID_UPSTREAM_FIRST: "上游优先（异常降级本地兜底）",
}

CONFIG_FILENAME = "chiplookup.json"

# ---------------------------------------------------------------------------
# 网络参数类型（原「上游」类型参数）
# ---------------------------------------------------------------------------
# 上游/索引拉取相关参数统一归类为「网络」类型：类型分组标签为「网络」，
# 写入 chiplookup.json 的键名仍沿用 upstream_* 前缀（保证历史配置兼容）。
# 类型声明集中在此，序列化（_SERIALIZED）与文档共用同一份 NETWORK_PARAMS，
# 避免三处各自维护造成不一致。

DEFAULT_UPSTREAM_TIMEOUT = 30.0
DEFAULT_UPSTREAM_RETRIES = 2
DEFAULT_UPSTREAM_RATE_LIMIT = 0.0
DEFAULT_UPSTREAM_MAX_AGE = 7 * 24 * 3600.0

# 网络类型参数的分组标签（UI/文档/配置校验统一引用）
NETWORK_PARAM_LABEL = "网络"
# 网络类型参数清单：磁盘键名沿用 upstream_*，类型分组归属「网络」
NETWORK_PARAMS = (
    "upstream_offline", "upstream_refresh", "upstream_cache_dir",
    "upstream_timeout", "upstream_max_age",
    "upstream_retries", "upstream_rate_limit",
)


# ---------------------------------------------------------------------------
# 配置文件路径
# ---------------------------------------------------------------------------


def program_root() -> str:
    """程序运行根：打包后为 exe 目录，开发模式为项目根。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_config_path(explicit: Optional[str] = None) -> str:
    """返回配置文件路径。未显式指定时用程序根目录 chiplookup.json。"""
    if explicit:
        return os.path.abspath(explicit)
    return os.path.join(program_root(), CONFIG_FILENAME)


# ---------------------------------------------------------------------------
# 配置文件读写
# ---------------------------------------------------------------------------


def load_config_file(path: Optional[str] = None) -> Dict[str, object]:
    """读取配置文件；不存在或格式非法时返回空 dict（不抛错）。"""
    cfg_path = resolve_config_path(path)
    if not os.path.exists(cfg_path):
        return {}
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config_file(config: dict, path: Optional[str] = None) -> str:
    """把配置写回 JSON 文件（UTF-8, 缩进 2），返回实际写入路径。"""
    cfg_path = resolve_config_path(path)
    os.makedirs(os.path.dirname(cfg_path) or ".", exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return cfg_path


# ---------------------------------------------------------------------------
# 配置对象
# ---------------------------------------------------------------------------


@dataclass
class Settings:
    """运行时配置。包含三种模式共用的数据源参数。"""

    mode: str = MODE_LOCAL
    # 本地 CSV 模式 / 混合模式的 CSV 路径；为空时用默认库（自动种子/定位）
    csv_path: Optional[str] = None
    # 混合模式优先级
    hybrid_priority: str = HYBRID_LOCAL_FIRST
    # UI 布局：极简模式（隐藏右栏详情区，仅显示左侧查询列表）
    minimal_mode: bool = False
    # 主窗口尺寸记忆（"宽x高"，如 "1150x780"；None=从未手动调整过，用默认尺寸）
    window_size: Optional[str] = None
    # 主窗口位置记忆（"+x+y"；恢复时按屏幕边界收敛，防止换显示器后窗口丢失）
    window_pos: Optional[str] = None
    # 界面缩放系数（乘在 DPI 因子上，0.8~1.6；改动保存后重启生效）
    ui_zoom: float = 1.0
    # 网络类型参数（原「上游」类型；键名保持 upstream_* 兼容既有配置）
    upstream_offline: bool = False
    upstream_refresh: bool = False
    upstream_cache_dir: Optional[str] = None
    upstream_timeout: float = DEFAULT_UPSTREAM_TIMEOUT
    upstream_max_age: float = DEFAULT_UPSTREAM_MAX_AGE
    upstream_retries: int = DEFAULT_UPSTREAM_RETRIES
    upstream_rate_limit: float = DEFAULT_UPSTREAM_RATE_LIMIT

    config_path: str = field(default_factory=resolve_config_path)
    # 配置解析阶段的非致命告警（便于 UI/CLI 提示）
    warnings: List[str] = field(default_factory=list)

    # ---------- 序列化 ----------

    # 通用运行参数 + 网络类型参数（键名与 NETWORK_PARAMS 一致，兼容历史配置）
    _SERIALIZED = (
        "mode", "csv_path", "hybrid_priority", "minimal_mode", "window_size",
        "window_pos", "ui_zoom",
    ) + NETWORK_PARAMS

    def to_dict(self) -> dict:
        out = {}
        for k in self._SERIALIZED:
            out[k] = getattr(self, k)
        return out

    def save(self) -> str:
        """把当前配置持久化到 self.config_path，返回路径。"""
        return save_config_file(self.to_dict(), self.config_path)

    # ---------- 从 dict 填充（带校验与告警） ----------

    def apply_dict(self, data: dict) -> None:
        for k in self._SERIALIZED:
            if k not in data:
                continue
            try:
                setattr(self, k, self._coerce(k, data[k]))
            except (TypeError, ValueError):
                self.warnings.append("配置项 %r 取值无效，已忽略" % (k,))

    def validate(self) -> None:
        """规范化非法取值：模式回退本地、优先级回退默认。"""
        if self.mode not in MODES:
            self.warnings.append(
                "模式 %r 无效，已回退为本地数据" % (self.mode,)
            )
            self.mode = MODE_LOCAL
        if self.hybrid_priority not in HYBRID_PRIORITIES:
            self.warnings.append(
                "混合优先级 %r 无效，已回退为本地优先" % (self.hybrid_priority,)
            )
            self.hybrid_priority = HYBRID_LOCAL_FIRST
        if self.csv_path:
            self.csv_path = os.path.abspath(self.csv_path)
        if self.upstream_cache_dir:
            self.upstream_cache_dir = os.path.abspath(self.upstream_cache_dir)
        if self.upstream_retries < 0:
            self.upstream_retries = DEFAULT_UPSTREAM_RETRIES
        if self.upstream_timeout <= 0:
            self.upstream_timeout = DEFAULT_UPSTREAM_TIMEOUT
        if self.upstream_rate_limit < 0:
            self.upstream_rate_limit = DEFAULT_UPSTREAM_RATE_LIMIT
        if self.window_size is not None:
            ok = False
            try:
                w, h = str(self.window_size).lower().split("x", 1)
                ok = int(w) > 0 and int(h) > 0
            except (ValueError, AttributeError):
                ok = False
            if not ok:
                self.warnings.append(
                    "窗口尺寸 %r 无效，已忽略" % (self.window_size,)
                )
                self.window_size = None
        if self.window_pos is not None:
            ok = False
            try:
                x, y = str(self.window_pos).lstrip("+").split("+", 1)
                ok = int(x) >= 0 and int(y) >= 0
            except (ValueError, AttributeError):
                ok = False
            if not ok:
                self.warnings.append(
                    "窗口位置 %r 无效，已忽略" % (self.window_pos,)
                )
                self.window_pos = None
        try:
            z = float(self.ui_zoom)
        except (TypeError, ValueError):
            z = 1.0
        if not 0.8 <= z <= 1.6:
            self.warnings.append(
                "界面缩放 %r 超出范围（0.8~1.6），已回退 1.0" % (self.ui_zoom,)
            )
            z = 1.0
        self.ui_zoom = z

    @staticmethod
    def _coerce(key: str, value) -> object:
        if key in ("upstream_offline", "upstream_refresh", "minimal_mode"):
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if key in ("upstream_timeout", "upstream_max_age", "upstream_rate_limit"):
            return float(value)
        if key == "upstream_retries":
            v = int(value)
            return v if v >= 0 else DEFAULT_UPSTREAM_RETRIES
        if key == "upstream_cache_dir":
            return str(value or "") or None
        if key == "csv_path":
            return str(value or "") or None
        if key == "window_size":
            return str(value or "") or None
        if key == "window_pos":
            return str(value or "") or None
        if key == "ui_zoom":
            return float(value)
        return str(value)


# ---------------------------------------------------------------------------
# argparse 集成
# ---------------------------------------------------------------------------


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """往命令行解析器追加模式/数据源相关参数。"""
    parser.add_argument(
        "--mode", choices=MODES, default=None,
        help="运行模式：local 本地数据 / upstream 上游 / hybrid 混合"
             "（默认读配置文件，未配置时为 local）",
    )
    parser.add_argument(
        "--config", default=None,
        help="配置文件路径（默认 %s）" % CONFIG_FILENAME,
    )
    parser.add_argument(
        "--db", default=None,
        help="数据库路径（本地/混合模式；默认自动定位）",
    )
    parser.add_argument(
        "--hybrid-priority", choices=HYBRID_PRIORITIES, default=None,
        help="混合模式优先级：local_first 本地优先 / upstream_first 上游优先",
    )
    parser.add_argument(
        "--upstream-offline", action="store_true", default=None,
        help="上游模式：只读本地缓存，不联网",
    )
    parser.add_argument(
        "--upstream-refresh", action="store_true", default=None,
        help="上游模式：忽略缓存新鲜期，强制重新下载",
    )
    parser.add_argument(
        "--upstream-cache-dir", default=None,
        help="上游模式：索引缓存目录（默认平台用户缓存）",
    )
    parser.add_argument(
        "--upstream-timeout", type=float, default=None,
        help="上游模式：HTTP 超时秒数（默认 %g）" % DEFAULT_UPSTREAM_TIMEOUT,
    )
    parser.add_argument(
        "--upstream-retries", type=int, default=None,
        help="上游模式：每个源的请求重试次数（默认 %d）" % DEFAULT_UPSTREAM_RETRIES,
    )
    parser.add_argument(
        "--upstream-rate-limit", type=float, default=None,
        help="上游模式：请求间隔秒数（限流，默认 %g）" % DEFAULT_UPSTREAM_RATE_LIMIT,
    )


def build_settings(args: argparse.Namespace) -> Settings:
    """命令行参数 + 配置文件 → Settings。命令行优先。"""
    cfg_path = resolve_config_path(getattr(args, "config", None))
    s = Settings(config_path=cfg_path)
    s.apply_dict(load_config_file(cfg_path))

    if getattr(args, "mode", None):
        s.mode = args.mode
    if getattr(args, "db", None):
        s.csv_path = args.db
    if getattr(args, "hybrid_priority", None):
        s.hybrid_priority = args.hybrid_priority
    if getattr(args, "upstream_offline", None):
        s.upstream_offline = True
    if getattr(args, "upstream_refresh", None):
        s.upstream_refresh = True
    if getattr(args, "upstream_cache_dir", None):
        s.upstream_cache_dir = args.upstream_cache_dir
    if getattr(args, "upstream_timeout", None):
        s.upstream_timeout = args.upstream_timeout
    if getattr(args, "upstream_retries", None):
        s.upstream_retries = args.upstream_retries
    if getattr(args, "upstream_rate_limit", None):
        s.upstream_rate_limit = args.upstream_rate_limit

    s.validate()
    if not s.csv_path:
        # 本地/混合模式默认库：装箱可用时自动从内嵌种子恢复
        from paths import ensure_user_database
        s.csv_path = ensure_user_database()
    return s