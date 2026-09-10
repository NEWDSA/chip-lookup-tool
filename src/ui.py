# -*- coding: utf-8 -*-
"""
chip_lookup.ui
---------------
Tkinter UI。结构：
    +-------------------------------------------------------+
    |  ChipLookup  •  数据源: <path>  •  共 N 条              |   <- 状态栏
    +--------------------+----------------------------------+
    |  [搜索输入框]       |  详细卡片区（按字段分组）          |
    |  [解析料号][搜索]    |                                  |
    |  ------------------ |  [厂商 / 型号 / 类型]             |
    |  候选列表(可键盘    |  [容量 / 位宽 / 速度 / 电压]     |
    |   上下键选择)       |  [封装 / 尺寸 / Die / CS]        |
    |                    |  [温度 / Rev / 备注]              |
    +--------------------+----------------------------------+
    |  [复制全部][复制料号][清空][导入CSV][导出CSV][...]      |  <- 操作栏
    +-------------------------------------------------------+
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import io
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional, Tuple

from config import MODE_LABELS, Settings
from database import default_database_path
from math_canvas import MathCanvasWithRecognizer
from paths import bundle_root
from search import cheap_partials, lookup_exact, search, suggest_terms, warm_prepared
from sources import RecordSource, make_source


# ---------------- 配色（深色卡片风，参考 itxtech.fm）----------------
COLOR_BG          = "#0e1620"   # 整体背景（深蓝）
COLOR_PANEL       = "#16202c"   # 面板背景
COLOR_CARD        = "#1c2735"   # 卡片背景
COLOR_CARD_HOVER  = "#243443"   # 卡片悬浮
COLOR_BORDER      = "#2a3a4f"   # 边框
COLOR_TEXT        = "#e6edf3"   # 主文字
COLOR_TEXT_DIM    = "#9bb0c4"   # 次文字（提亮至 WCAG AA 达标，对比度 5.5:1+）
COLOR_ACCENT      = "#16c47e"   # 强调绿（解析料号按钮）
COLOR_ACCENT2     = "#4a90e0"   # 强调蓝（搜索按钮，提亮至 5.2:1 对比度）
COLOR_WARN        = "#f59e0b"   # 警告黄
COLOR_DANGER      = "#ef4444"   # 危险红
COLOR_FOCUS       = "#38bdf8"   # 选中态（偏蓝，与 Accent Green 区分）

# 应用版本（标题栏与「关于」对话框展示；对外可见的改版递增）
APP_VERSION       = "1.1.0"


# ---------------- Design Tokens ----------------
# 字号层级
FONT_SIZE_XS   = 9
FONT_SIZE_SM   = 10
FONT_SIZE_MD   = 11
FONT_SIZE_LG   = 12
FONT_SIZE_XL   = 13
FONT_SIZE_XXL  = 16

# 字体族
FONT_FAMILY_UI    = "Microsoft YaHei UI"
FONT_FAMILY_MONO  = "Consolas"

# 间距系统
SPACE_XXS  = 2
SPACE_XS   = 4
SPACE_SM   = 6
SPACE_MD   = 8
SPACE_LG   = 10
SPACE_XL   = 12
SPACE_XXL  = 14
SPACE_XXXL = 16

# 按钮内边距（3 种规格）
BTN_PADDING_SM = (10, 5)     # 紧凑按钮（ModeSel/ModeRadio）
BTN_PADDING_MD = (12, 8)     # 次按钮（Ghost）
BTN_PADDING_LG = (14, 8)     # 主按钮（Accent/Blue）

# 卡片内边距
CARD_PADDING = (20, 16)


# ---------------- 工具函数 ----------------

def make_text_for_copy(record: dict) -> str:
    """生成「一键复制」用的纯文本格式。"""
    lines = []
    labels = {
        "part_number":  "料号",
        "model":        "型号",
        "manufacturer": "厂商",
        "type":         "类型",
        "capacity":     "容量",
        "bit_width":    "位宽",
        "voltage":      "电压",
        "speed":        "速度",
        "package":      "封装",
        "dimensions":   "尺寸",
        "die_count":    "Die 数",
        "cs_count":     "CS 数",
        "die_revision": "Die 版本",
        "op_temp":      "工作温度",
        "notes":        "备注",
    }
    for k, label in labels.items():
        v = (record.get(k, "") or "").strip()
        if v:
            lines.append(f"{label}: {v}")
    # 添加 GB 值
    cap_gb = _capacity_gb(record.get("capacity", ""))
    if cap_gb:
        lines.append(f"GB值: {cap_gb}")
    return "\n".join(lines)


import re as _re

_CAPACITY_RE = _re.compile(r"(\d+(?:\.\d+)?)\s*[GTgT]", _re.IGNORECASE)
_BITWIDTH_RE = _re.compile(r"[xX]?(\d+)")


def _capacity_display(capacity: str, bit_width: str) -> str:
    """把原始容量换算为 Config 格式显示文本。

    公式：Config 深度 = Capacity / BitWidth
    例：16Gb + x4 → 4G x4 (16Gb)；8Gb + x8 → 1G x8 (8Gb)

    说明：DRAM 标称容量已包含总存储位数，bit_width 表示数据总线宽度。
    """
    if not capacity:
        return capacity
    cm = _CAPACITY_RE.search(capacity)
    if not cm:
        return capacity
    try:
        density_g = float(cm.group(1))
    except (ValueError, IndexError):
        return capacity
    # 提取 bit_width 数值
    bw = 0
    if bit_width:
        bw_m = _BITWIDTH_RE.search(bit_width)
        if bw_m:
            try:
                bw = int(bw_m.group(1))
            except (ValueError, IndexError):
                pass
    # 计算 Config 深度
    if bw > 0:
        config_depth = density_g / bw
        config_str = ("%g" % config_depth) if config_depth != int(config_depth) else str(int(config_depth))
        return "%sG %s (%s)" % (config_str, bit_width, capacity)
    return capacity


def _capacity_gb(capacity: str) -> str:
    """把原始容量换算为 GB 值显示文本。

    公式：GB = 标称容量(Gb) ÷ 8
    例：16Gb → 2GB；24Gb → 3GB；4Gb → 0.5GB
    """
    if not capacity:
        return ""
    cm = _CAPACITY_RE.search(capacity)
    if not cm:
        return ""
    try:
        density_g = float(cm.group(1))
    except (ValueError, IndexError):
        return ""
    gb = density_g / 8
    gb_str = ("%g" % gb) if gb != int(gb) else str(int(gb))
    return "%sGB" % gb_str


# ---------------- 细条滚动条（详情区） ----------------

def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


_BG_RGB = _hex_to_rgb(COLOR_BG)


def _win_dpi_scale() -> float:
    """Windows 真实系统 DPI / 96；非 Windows 或探测失败返回 0（调用方自行回退）。

    DPI 感知进程拿到真实 DPI（125% → 1.25）；未感知进程被系统虚拟化返回 96
    → 恒 1.0。不要用 winfo_fpixels('1i') 推导——它由字体度量计算（实测
    96.09/96），会引入 ~1% 漂移。
    """
    if sys.platform != "win32":
        return 0.0
    try:
        import ctypes
        dpi = 0
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
        except Exception:
            pass
        if dpi <= 0:
            hdc = ctypes.windll.user32.GetDC(0)
            try:
                dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
            finally:
                ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi / 96.0
    except Exception:
        pass
    return 0.0


class SlimVScrollbar(tk.Canvas):
    """右侧详情区的自绘细条竖向滚动条（替代 clam 主题默认 ttk.Scrollbar）。

    设计要点（对应视觉优化需求）：
    1. 纤细但易点：命中区 16px（#6 扩大点击目标，原 11px 太窄难点中），
       视觉条宽 6px 居中绘制，去掉了原生粗大的上下箭头按钮；
    2. 长度准确：滑块高度只由 canvas 回报的 (first,last) 比例换算，有多少
       可滚动范围就显示多长；内容不溢出时被 _sync_detail_scrollbar 整体
       卸载，从根上杜绝“整条轨道占满的无效滑块”；
    3. 圆角 + 透明感：轨道/滑块是圆角胶囊形，hover / 按下时透明度逐帧过渡；
    4. 交互可用：滑块最短 24px 保证易抓取，按住拖动、点击轨道翻页。

    说明：Tk 的 PhotoImage 不支持逐像素 alpha，这里把透明度按已知底色
    COLOR_BG 预先混合成不透明像素（伪透明），因此本控件必须贴在纯
    COLOR_BG 底色的区域上使用。
    """

    WIDTH = 16            # 命中区宽度（整条都响应悬停/点击；#6 扩大点击目标）
    BAR_W = 6.0           # 视觉条宽（纤细，命中区内居中绘制）
    INSET_Y = 3           # 视觉条相对控件顶/底的留白
    MIN_THUMB = 24        # 滑块最短像素（保证可抓取）
    RADIUS = 3.0          # 圆角半径（≈ 半条宽 → 胶囊形）
    # 不同档位下的“透明度”（颜色混合用 alpha，白色画笔叠在深色底上）
    A_THUMB = {"idle": 0.30, "over": 0.44, "thumb": 0.56, "drag": 0.72}
    A_TRACK = {"idle": 0.00, "over": 0.10, "thumb": 0.14, "drag": 0.18}

    def __init__(self, master: tk.Widget, command: Callable):
        super().__init__(
            master, width=self.WIDTH, bg=COLOR_BG,
            highlightthickness=0, bd=0, relief="flat", cursor="arrow",
        )
        # DPI 缩放：像素常量按显示器 DPI 放大（类常量保留 96 DPI 逻辑值）。
        # winfo_fpixels 需控件已创建，故先按类值构造、再改写并 configure 宽度。
        sc = _win_dpi_scale()
        if sc <= 0:
            try:
                sc = round(float(self.winfo_fpixels("1i")) / 96.0 * 40) / 40.0
            except Exception:
                sc = 1.0
        if abs(sc - 1.0) > 0.01:
            self.WIDTH = max(7, int(round(self.WIDTH * sc)))
            self.BAR_W = max(4.0, self.BAR_W * sc)
            self.RADIUS = self.BAR_W / 2.0
            self.INSET_Y = max(2, int(round(self.INSET_Y * sc)))
            self.MIN_THUMB = max(16, int(round(self.MIN_THUMB * sc)))
            self.configure(width=self.WIDTH)
        self._cmd = command
        self._first = 0.0
        self._last = 1.0
        self._state = "idle"            # idle / over / thumb / drag
        self._cur_thumb_a = self.A_THUMB["idle"]
        self._cur_track_a = self.A_TRACK["idle"]
        self._target: Optional[Tuple[float, float]] = None
        self._anim_after: Optional[str] = None
        self._drag_y0: Optional[float] = None    # 拖动起点 y_root
        self._drag_first0 = 0.0                  # 拖动起点 first
        self._track_photo: Optional[tk.PhotoImage] = None
        self._thumb_photo: Optional[tk.PhotoImage] = None
        self._track_key: Optional[Tuple[int, int]] = None
        self._thumb_key: Optional[Tuple[int, int]] = None
        self._cursor_now = "arrow"

        # master 必须显式指定：PhotoImage 不带 master 会挂到“默认根”（首个 Tk），
        # 同进程创建第二个 App 时图像属于旧解释器，触发 "pyimageN doesn't exist"
        self._blank_photo = tk.PhotoImage(master=self, width=1, height=1)
        # 两层自绘图层：轨道在下、滑块在上
        self._track_item = self.create_image(0, 0, anchor="nw", image=self._blank_photo)
        self._thumb_item = self.create_image(0, 0, anchor="nw", image=self._blank_photo)

        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Motion>", self._on_motion)
        self.bind("<ButtonPress-1>", self._on_press)

    # ---------- 对外接口：挂到 canvas 的 yscrollcommand ----------
    def set(self, first: float, last: float):
        """canvas 回报视图区间（0~1），据此换算滑块长度与位置。"""
        try:
            first = max(0.0, min(1.0, float(first)))
            last = max(first + 1e-9, min(1.0, float(last)))
        except (TypeError, ValueError):
            return
        if abs(first - self._first) < 0.0005 and abs(last - self._last) < 0.0005:
            return
        self._first, self._last = first, last
        self._paint()

    # ---------- 几何换算 ----------
    def _thumb_geometry(self) -> Tuple[int, int, int]:
        """返回 (滑块活动区高, 滑块高, 滑块顶端 y)，全部按当前控件高换算。"""
        h = max(2 * self.INSET_Y + 1, self.winfo_height())
        motion = max(self.MIN_THUMB, h - 2 * self.INSET_Y)
        frac = max(0.0, min(1.0, self._last - self._first))
        thumb_h = max(self.MIN_THUMB, int(round(motion * frac)))
        thumb_h = min(thumb_h, motion)
        top = self.INSET_Y + int(round((motion - thumb_h) * self._first))
        top = max(self.INSET_Y, min(top, self.INSET_Y + motion - thumb_h))
        return motion, thumb_h, top

    # ---------- 绘制 ----------
    def _render_band(self, height: int, alpha: float) -> tk.PhotoImage:
        """把视觉条带渲染为 WIDTH×height 的 PPM 位图（圆角胶囊 + 抗锯齿）。

        全像素不透明，透明感 = 白色画笔按 alpha 与底色 COLOR_BG 混合。
        """
        bg_r, bg_g, bg_b = _BG_RGB
        x0 = (self.WIDTH - self.BAR_W) / 2.0
        x1 = x0 + self.BAR_W
        radius = min(self.RADIUS, height / 2.0, self.BAR_W / 2.0)
        cx = (x0 + x1) / 2.0
        cy = height / 2.0
        hw = (x1 - x0) / 2.0 - radius
        hh = max(0.0, height / 2.0 - radius)
        w = self.WIDTH
        raw = bytearray()
        for yy in range(height):
            py = yy + 0.5
            for xx in range(w):
                px = xx + 0.5
                qx = abs(px - cx) - hw
                qy = abs(py - cy) - hh
                sd = min(max(qx, qy), 0.0) \
                    + math.hypot(max(qx, 0.0), max(qy, 0.0)) - radius
                cov = max(0.0, min(1.0, 0.5 - sd))   # ~1px 抗锯齿过渡带
                if cov <= 0.0:
                    raw.append(bg_r)
                    raw.append(bg_g)
                    raw.append(bg_b)
                else:
                    a = alpha * cov
                    raw.append(bg_r + int((255 - bg_r) * a))
                    raw.append(bg_g + int((255 - bg_g) * a))
                    raw.append(bg_b + int((255 - bg_b) * a))
        header = ("P6\n%d %d\n255\n" % (w, height)).encode("ascii")
        return tk.PhotoImage(master=self, data=bytes(header) + bytes(raw), format="ppm")

    def _paint(self):
        """按当前几何与透明度档位重建图层（带尺寸/透明度缓存，滚动时仅移动滑块）。"""
        try:
            if not self.winfo_exists():
                return
            h = self.winfo_height()
            if h <= 0:
                return
        except tk.TclError:
            return

        # 轨道层（只在 hover/拖动时浮现；idle 直接回落为底色）
        key_t = (h, int(self._cur_track_a * 200))
        if self._track_key != key_t:
            self._track_key = key_t
            self._track_photo = (
                self._render_band(h, self._cur_track_a)
                if self._cur_track_a > 0.001 else self._blank_photo
            )
            self.itemconfigure(self._track_item, image=self._track_photo)

        # 滑块层（仅当长度/透明度变化时重绘，拖动时只移动坐标）
        _, thumb_h, top = self._thumb_geometry()
        key_s = (thumb_h, int(self._cur_thumb_a * 200))
        if self._thumb_key != key_s:
            self._thumb_key = key_s
            self._thumb_photo = self._render_band(thumb_h, self._cur_thumb_a)
            self.itemconfigure(self._thumb_item, image=self._thumb_photo)
        self.coords(self._thumb_item, 0, top)

    # ---------- 状态机（idle / over / thumb / drag） ----------
    def _over_thumb_at(self, y: int) -> bool:
        _, thumb_h, top = self._thumb_geometry()
        return top <= y <= top + thumb_h

    def _go(self, state: str):
        if state == self._state and self._anim_after is None:
            return
        self._state = state
        cursor = "sb_v_double_arrow" if state in ("thumb", "drag") else "arrow"
        if cursor != self._cursor_now:
            self._cursor_now = cursor
            try:
                self.configure(cursor=cursor)
            except tk.TclError:
                pass
        self._animate(self.A_THUMB[state], self.A_TRACK[state])

    def _on_enter(self, _event):
        self._go("thumb" if self._over_thumb_at(self.winfo_pointery() - self.winfo_rooty()) else "over")

    def _on_leave(self, _event):
        if self._drag_y0 is None:
            self._go("idle")

    def _on_motion(self, event):
        if self._drag_y0 is not None:
            return
        self._go("thumb" if self._over_thumb_at(event.y) else "over")

    def _animate(self, thumb_a: float, track_a: float):
        self._target = (thumb_a, track_a)
        if self._anim_after is None:
            self._anim_step()

    def _anim_step(self):
        self._anim_after = None
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        thumb_t, track_t = self._target
        step = 0.08
        thumb_a = self._cur_thumb_a + max(-step, min(step, thumb_t - self._cur_thumb_a))
        track_a = self._cur_track_a + max(-step, min(step, track_t - self._cur_track_a))
        self._cur_thumb_a, self._cur_track_a = thumb_a, track_a
        self._paint()
        if abs(thumb_a - thumb_t) > 0.005 or abs(track_a - track_t) > 0.005:
            self._anim_after = self.after(12, self._anim_step)

    # ---------- 交互（拖滑块 / 点轨道翻页） ----------
    def _on_press(self, event):
        _, thumb_h, top = self._thumb_geometry()
        if top <= event.y <= top + thumb_h:
            # 按住滑块 → 抓取拖动
            self._drag_y0 = float(event.y_root)
            self._drag_first0 = self._first
            self._go("drag")
            self.bind_all("<B1-Motion>", self._on_drag_move)
            self.bind_all("<ButtonRelease-1>", self._on_drag_end)
            return "break"
        # 点击轨道空白处 → 上/下翻一页
        self._cmd("scroll", -1 if event.y < top + thumb_h / 2.0 else 1, "pages")
        return "break"

    def _on_drag_move(self, event):
        if self._drag_y0 is None:
            return
        motion, thumb_h, _ = self._thumb_geometry()
        span = max(1, motion - thumb_h)
        frac = self._drag_first0 + (event.y_root - self._drag_y0) / span
        hi = max(0.0, 1.0 - (self._last - self._first))
        self._cmd("moveto", max(0.0, min(hi, frac)))

    def _on_drag_end(self, _event):
        self._drag_y0 = None
        try:
            self.unbind_all("<B1-Motion>")
            self.unbind_all("<ButtonRelease-1>")
        except tk.TclError:
            pass
        self._on_enter(None)


# ---------------- 轻量悬停提示 ----------------

def _create_tip_window(master: tk.Widget, text: str) -> tk.Toplevel:
    """构造无边框深色提示气泡（ToolTip 与候选行级提示共用样式）。"""
    tip = tk.Toplevel(master)
    tip.wm_overrideredirect(True)   # 无边框、不进任务栏
    try:
        tip.attributes("-topmost", True)
    except tk.TclError:
        pass
    tk.Label(
        tip, text=text, justify=tk.LEFT, anchor="w",
        bg=COLOR_CARD, fg=COLOR_TEXT,
        highlightthickness=1, highlightbackground=COLOR_BORDER,
        padx=8, pady=5,
        font=(FONT_FAMILY_UI, 9),
    ).pack(fill="both", expand=True)
    return tip


def _place_tip_window(tip: Optional[tk.Toplevel],
                      widget: Optional[tk.Widget] = None):
    """气泡定位：优先在控件正上方；右/上贴边时往屏内收（#7 屏幕边缘收敛）。"""
    if tip is None:
        return
    try:
        tip.update_idletasks()
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        if widget is not None:
            # 相对控件定位：水平居中，垂直在控件上方
            wx = widget.winfo_rootx()
            wy = widget.winfo_rooty()
            ww = widget.winfo_width()
            px = wx + ww // 2 - w // 2
            py = wy - h - 2
        else:
            # 无控件时 fallback 到指针位置
            px = tip.winfo_pointerx() + 14
            py = tip.winfo_pointery() + 20
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        x = max(0, min(px, sw - w - 8))
        y = max(0, min(py, sh - h - 8))
        tip.wm_geometry("+%d+%d" % (x, y))
    except tk.TclError:
        pass


class ToolTip:
    """轻量悬停提示：深色小气泡，600ms 防抖弹出。

    - 停留 600ms 才出现（快速划过不弹），移开/按下立即消失；
    - 显示位置在指针右下，贴屏幕边缘自动收回（见 _place_tip_window）；
    - `update_text` 支持动态改文案（如状态栏的当前数据源描述）。

    实例无需外部保活：tkinter 的 bind 会持有回调对象，控件存续期间
    ToolTip 一直可达；控件销毁时 <Destroy> 回调清场。
    """

    DELAY_MS = 600   # 防抖：停留超过此时长才弹出

    def __init__(self, widget: tk.Widget, text: str = ""):
        self._widget = widget
        self._text = text
        self._after_id: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        self._tip_label: Optional[tk.Label] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def update_text(self, text: str):
        """动态更新文案；气泡正在显示时立即生效。"""
        self._text = text
        if self._tip is not None:
            try:
                self._tip_label.configure(text=text)
                _place_tip_window(self._tip, self._widget)
            except tk.TclError:
                pass

    # ---------- 内部 ----------
    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self._widget.after(self.DELAY_MS, self._show)

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
            self._tip_label = None

    def _cancel(self):
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _on_destroy(self, _event=None):
        # 控件销毁（含 App 关闭）时清掉挂起的 after 与气泡，防止关闭期报错
        self._hide()

    def _show(self):
        self._after_id = None
        try:
            if not self._text or not self._widget.winfo_exists():
                return
        except tk.TclError:
            return
        if self._tip is not None:
            return
        self._tip = _create_tip_window(self._widget, self._text)
        self._tip_label = self._tip.winfo_children()[0]
        _place_tip_window(self._tip, self._widget)


# 数据源模式悬停说明（#7 tooltip 体系）：radio 上只放得下短标签，差异在这里说清
_SOURCE_MODE_HINTS = {
    "local": "读取本地数据文件，离线可用",
    "upstream": "从上游网络接口拉取数据（需联网，首次构建较慢）",
    "hybrid": "本地 + 网络合并查询（首次构建较慢）",
}


# ---------------- 主窗口 ----------------

class App(tk.Tk):
    def __init__(self, settings: Settings, source: RecordSource,
                 on_change: Optional[Callable[[], None]] = None):
        super().__init__()
        # settings 提前赋值：缩放因子需要读 ui_zoom 偏好（#16）
        self.settings = settings
        # DPI 缩放因子（必须先于任何像素尺寸计算）：winfo_fpixels('1i') 返回
        # 当前显示器真实 DPI（进程开启 DPI 感知后，125% 缩放 → 120），除以 96
        # 得缩放系数，再乘用户界面缩放偏好（⋯菜单可选 0.8~1.6，#16）。
        # 像素类常量（窗口尺寸/行高/列宽等）经 _s() 缩放，保证高分
        # 屏下布局比例与 96 DPI 一致（字体是 pt 单位，Tk 按 scaling 自动缩放）。
        self._ui_scale = self._compute_ui_scale() * self._zoom_factor()
        self.title(f"ChipLookup v{APP_VERSION} · 芯片料号查询器")
        self._set_window_icon()
        # #5：minsize 1000x680 → 720x560 —— 1080p@125% 半屏（逻辑 768 宽）可贴靠，
        # 「左资料右查询」的分屏工作流可用；窄窗可用性由状态栏三态折叠（#5）+
        # 底栏低频项收纳（#9）保证
        self.minsize(self._s(720), self._s(560))
        self.configure(bg=COLOR_BG)

        # 窗口尺寸记忆状态（见 _on_window_configure / _save_window_size）
        self._win_size_ready: bool = False         # 启动就绪前忽略尺寸事件（程序性变化）
        self._win_size_save_after: Optional[str] = None  # 尺寸防抖保存句柄
        self._last_saved_win_size: Optional[Tuple[int, int]] = None  # 上次已保存尺寸（去重）
        self._last_saved_win_pos: Optional[str] = None  # 上次已保存位置（去重，#20）
        # 左栏右缘 grid padx（逻辑 7px）：_apply_pane_width 计算列 minsize 时需一并计入，
        # 实例属性按 DPI 缩放（类常量保留逻辑值便于追溯）
        self._LEFT_PADX = self._s(self._LEFT_PADX)
        # 状态栏 toast / 三态文案状态（见 _toast / _refresh_status / _status_mode）
        self._toast_after: Optional[str] = None   # toast 到期恢复回调句柄（连续 toast 防抖）
        self._status_mode_state: str = "full"     # 状态栏当前文案态 full/compact/hidden（#5/#8）

        # 初始尺寸/位置：优先恢复用户上次手动调整并保存的大小与位置（#20），
        # 位置按屏幕边界收敛防止丢失；从未保存过则用默认尺寸
        w, h = self._initial_window_size()
        self.geometry("%dx%d%s" % (w, h, self._saved_window_pos_suffix()))
        self.source = source
        self.on_change = on_change
        self._records_cache = self.source.list_records()
        self._candidates: List[Tuple[dict, float, str]] = []
        self._selected_index: int = -1  # 候选列表中选中项（全局索引，与 tree iid 一致）

        # ---------- 数据源切换（后台线程 + 队列轮询，latest-wins） ----------
        # 数据源重建（尤其上游/混合：~4 万条 × fdnext 解码）耗时秒级，
        # 不能在 Tk 主线程里同步执行，否则窗口冻结。方案：
        #   1. 工作线程里 make_source + list_records，结果放 queue；
        #   2. 主线程用 after() 轮询队列取回结果并一次性落地（token 防串台）；
        #   3. 忙碌期间允许继续点 radio：只记「最新想要的目标模式」，
        #      当前切换收尾后再启动下一个（latest-wins，不做中间切换）。
        #   4. 已构建好的数据源按模式缓存：来回切换/再次点击不再重建。
        self._source_cache: Dict[str, RecordSource] = {
            getattr(self.source, "MODE", settings.mode): self.source
        }
        self._switch_busy = False          # 是否正在后台构建
        self._switch_token = 0             # 递增令牌，用于忽略过期结果
        self._sync_token = 0               # 同步任务独立 token（与切换任务隔离）
        self._desired_mode: Optional[str] = None   # 用户最新想切到的模式
        self._pending_force = False        # 下一个切换是否为强制重建（F5 刷新）
        self._switch_queue: "queue.Queue[tuple]" = queue.Queue()
        self._polling = False              # 队列轮询是否已挂起
        self._source_radios: List[ttk.Radiobutton] = []
        self._switch_started: Optional[float] = None  # 当前切换开始时刻（耗时 toast 用，#12）
        # 输入防抖：连续敲键只触发最后一次查询（上游 4 万条时省去中间全量扫描）
        self._query_after_id: Optional[str] = None

        # ---------- 候选区分页参数 ----------
        self.PAGE_SIZE = 8                # 每页条数，与 tree 可见行数一致
        # #10：固定「加载更多」模式（策略选择行已移除）；"paginate" 渲染路径保留
        self.page_mode = tk.StringVar(value="load_more")  # "paginate" | "load_more"
        self._current_page: int = 0        # 分页模式：当前页（0-indexed）
        self._visible_count: int = self.PAGE_SIZE  # 加载更多模式：已加载条数
        self._tree_col_cache: Optional[Tuple[int, ...]] = None  # 树列宽缓存（防抖动）
        self._tree_col_custom: bool = False        # 用户是否拖拽过列宽（此后按自定义比例缩放适配）
        self._tree_cursor: str = ""                # 表头当前光标态（防抖）
        self._detail_value_labels: List = []  # 详情卡字段值（用于随窗口自适应换行）
        self._detail_scroll_visible: bool = True  # 详情滚动条当前是否已挂载（_build_layout 先 pack，随后按内容自动显隐）
        self._left_pane_w: Optional[int] = None    # 左栏宽度（像素，None=尚未初始化）
        self._pane_custom: bool = False            # 用户是否拖过左右分隔条（拖过则不再随窗口比例变化）
        self._pane_drag: Optional[Tuple[int, int]] = None  # (拖动起始 x_root, 起始左栏宽)
        self._pane_repin_after: Optional[str] = None  # 左栏宽度 after_idle 复钉句柄（防抖）
        self._minimal_mode: bool = False           # 极简模式：右栏详情区整体隐藏
        self._minimal_anim_after: Optional[str] = None  # 极简布局动画 after 句柄（防重入）
        self._minimal_saved_w: Optional[int] = None  # 进极简前的左栏宽（出极简时恢复）
        self._col_drag: Optional[Tuple[int, int]] = None   # (被拖分隔线左侧列下标, 拖动起始 x_root)
        self._col_drag_widths: Optional[Tuple[int, ...]] = None  # 拖动起点各列宽（拖动基准）
        # 候选行级悬停提示状态（见 _on_tree_tip_motion）
        self._tree_tip: Optional[tk.Toplevel] = None      # 行信息气泡（完整料号/厂商/容量）
        self._tree_tip_after: Optional[str] = None        # 行级提示 600ms 防抖句柄
        self._tree_tip_row: Optional[str] = None          # 气泡当前对应的行 iid（换行即重置）
        self._tree_hover_row: Optional[str] = None        # hover 高亮当前所在行（#13）

        self._configure_style()
        self._build_layout()
        self._refresh_status()
        self._bind_global_keys()
        self._refresh_mode_buttons()  # 初始化分页分段按钮高亮
        self._refresh_source_buttons()  # 初始化数据源 radio 选中态（回填已保存模式）
        # 窗口首帧布局稳定后，按内容高度决定详情滚动条是否出现
        self._schedule_scroll_sync()
        # 恢复上次的极简模式偏好（延后一帧等首帧布局稳定；启动不做动画）
        if self.settings.minimal_mode:
            self.after(80, lambda: self._set_minimal_mode(True, animate=False))
        # 窗口尺寸记忆：布局稳定后才跟踪（启动期间的程序性尺寸变化不保存）
        self.bind("<Configure>", self._on_window_configure)
        self.after(600, self._enable_win_size_tracking)

    # ---------------- 样式 ----------------

    def _compute_ui_scale(self) -> float:
        """DPI 缩放因子 = 真实系统 DPI / 96（探测见 _win_dpi_scale）。

        Windows 优先走系统 API；非 Windows 或 API 不可用时回退 fpixels，
        并按 2.5% 步长吸附消除字体度量带来的 ~1% 漂移。
        """
        f = _win_dpi_scale()
        if f > 0:
            return f
        try:
            f = float(self.winfo_fpixels("1i")) / 96.0
            return round(f * 40) / 40.0
        except Exception:
            return 1.0

    def _set_window_icon(self):
        """设置窗口图标（标题栏 + 任务栏）。打包后从 _MEIPASS 读取，开发模式从项目根读取。"""
        from paths import bundle_root
        icon_path = os.path.join(bundle_root(), "installer", "chip_lookup.ico")
        if os.path.isfile(icon_path):
            try:
                self.iconbitmap(icon_path)
            except tk.TclError:
                pass

    def _s(self, px) -> int:
        """逻辑像素 → 物理像素（按显示器 DPI 缩放，见 _compute_ui_scale）。"""
        return int(round(px * self._ui_scale))

    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")  # clam 在 Win 上比较稳
        except tk.TclError:
            pass

        # 全局调整
        style.configure(".", background=COLOR_BG, foreground=COLOR_TEXT, fieldbackground=COLOR_CARD)
        style.configure("TFrame", background=COLOR_BG)
        style.configure("Panel.TFrame", background=COLOR_PANEL)
        style.configure("Card.TFrame", background=COLOR_CARD)

        # Label
        style.configure("TLabel", background=COLOR_BG, foreground=COLOR_TEXT)
        style.configure("Panel.TLabel", background=COLOR_PANEL, foreground=COLOR_TEXT)
        style.configure("Card.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT)
        style.configure("Dim.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT_DIM)
        style.configure("Hint.TLabel", background=COLOR_PANEL, foreground=COLOR_TEXT_DIM)
        style.configure("Status.TLabel", background=COLOR_PANEL, foreground=COLOR_TEXT_DIM)
        style.configure("Title.TLabel", background=COLOR_PANEL, foreground=COLOR_TEXT, font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Big.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT, font=("Consolas", 16, "bold"))

        # 输入框
        style.configure(
            "Search.TEntry",
            fieldbackground=COLOR_PANEL,
            foreground=COLOR_TEXT,
            insertcolor=COLOR_TEXT,
            borderwidth=1,
            relief="flat",
            padding=(10, 8),
            # #19：Consolas 只对纯 ASCII 有意义，输入框常混中文/全角 → 用 UI 字体；
            # 料号展示区（详情 Big label）保持 Consolas 等宽对齐
            font=("Microsoft YaHei UI", 12),
        )
        # 输入框聚焦态（#4 焦点可见）：边框/内亮线变亮蓝，与按钮焦点环同色系
        style.map(
            "Search.TEntry",
            bordercolor=[("focus", COLOR_FOCUS)],
            lightcolor=[("focus", COLOR_FOCUS)],
        )

        # 按钮
        # 键盘焦点可见（#4）：focusthickness 1 + 亮蓝 focuscolor，Tab 遍历时能
        # 看清焦点在哪个按钮上（原 focusthickness=0 连键盘用户都看不到焦点）
        style.configure(
            "Accent.TButton",
            background=COLOR_ACCENT,
            foreground="#0e1620",
            borderwidth=0,
            focusthickness=1,
            focuscolor=COLOR_FOCUS,
            padding=BTN_PADDING_LG,
            font=(FONT_FAMILY_UI, FONT_SIZE_SM, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#0fa970"), ("disabled", "#3a4a48")], foreground=[("disabled", "#b0c4d4")])

        style.configure(
            "Blue.TButton",
            background=COLOR_ACCENT2,
            # 深字 #0e1620 on #4a90e0 = 5.6:1（WCAG AA 达标；原白字仅 3.3:1），
            # 与 Accent 绿钮的「亮底 + 深字」设计语言保持一致
            foreground="#0e1620",
            borderwidth=0,
            focusthickness=1,
            focuscolor=COLOR_FOCUS,
            padding=BTN_PADDING_LG,
            font=(FONT_FAMILY_UI, FONT_SIZE_SM, "bold"),
        )
        # 对比度逐态核对：active 深字 on #6aa9ec = 7.5:1 ✓；disabled #b0c4d4
        # on #3b4862 = 5.1:1 ✓（disabled 底色偏深，必须单映射浅字，深字仅 2:1）
        style.map(
            "Blue.TButton",
            background=[("active", "#6aa9ec"), ("disabled", "#3b4862")],
            foreground=[("disabled", "#b0c4d4")],
        )

        style.configure(
            "Ghost.TButton",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            borderwidth=0,
            focusthickness=1,
            focuscolor=COLOR_FOCUS,
            padding=BTN_PADDING_MD,
            font=(FONT_FAMILY_UI, FONT_SIZE_SM),
        )
        style.map("Ghost.TButton", background=[("active", COLOR_BORDER)])

        # 分页模式分段按钮（紧凑）
        style.configure(
            "ModeSel.TButton",
            background=COLOR_BORDER,
            foreground=COLOR_TEXT,
            borderwidth=0,
            focusthickness=1,
            focuscolor=COLOR_FOCUS,
            padding=BTN_PADDING_SM,
            font=(FONT_FAMILY_UI, FONT_SIZE_XS),
        )
        style.map("ModeSel.TButton", background=[("active", "#3a4d63")])
        style.configure(
            "ModeSelActive.TButton",
            background=COLOR_ACCENT,
            foreground="#0e1620",
            borderwidth=0,
            focusthickness=1,
            focuscolor=COLOR_FOCUS,
            padding=BTN_PADDING_SM,
            font=(FONT_FAMILY_UI, FONT_SIZE_XS, "bold"),
        )
        style.map("ModeSelActive.TButton", background=[("active", "#0fa970")])

        # 数据源模式 radio 单选组（indicatoron=False 成紧凑分段样式；互斥由共享变量保证）
        style.configure(
            "ModeRadio.TRadiobutton",
            background=COLOR_BORDER,
            foreground=COLOR_TEXT,
            borderwidth=0,
            focusthickness=0,
            padding=BTN_PADDING_SM,
            font=(FONT_FAMILY_UI, FONT_SIZE_XS),
            indicatoron=False,
            focuscolor="",          # 去掉 clam 主题自带的焦点虚框
        )
        style.map(
            "ModeRadio.TRadiobutton",
            background=[("selected", COLOR_ACCENT), ("active", "#3a4d63")],
            foreground=[
                ("selected", "#0e1620"),
                ("active", COLOR_TEXT),
                ("disabled", "#66788c"),   # 切换忙碌期间禁用态置灰（#12）
            ],
        )
        # 彻底移除 Radiobutton 的 focus 内边距/边框元素，消除选中时的虚线外框
        style.layout("ModeRadio.TRadiobutton", [
            ("Radiobutton.button", {"sticky": "nswe", "children": [
                ("Radiobutton.padding", {"sticky": "nswe", "children": [
                    ("Radiobutton.label", {"sticky": "nswe"}),
                ]}),
            ]}),
        ])

        # 列表
        style.configure(
            "Candidate.Treeview",
            background=COLOR_PANEL,
            fieldbackground=COLOR_PANEL,
            foreground=COLOR_TEXT,
            borderwidth=0,
            rowheight=self._s(32),  # 逻辑 32px：随 DPI 缩放，保证行高与字体的比例恒定
            font=("Microsoft YaHei UI", 10),
        )
        # 原生的 ttk.Treeview.Heading 在不同主题下对垂直 sticky 支持不一致，
        # 导致表头文字无法可靠地在单元格内垂直居中。这里把 heading 行压缩到
        # 接近 0 高度并留空，实际表头由上方自定义的 tk.Frame + Label 实现，
        # 从而完全控制表头高度、padding 和对齐。
        style.configure(
            "Candidate.Treeview.Heading",
            background=COLOR_PANEL,
            foreground=COLOR_PANEL,
            borderwidth=0,
            anchor="center",
            padding=(0, 0),
            font=("Microsoft YaHei UI", 1),
            relief="flat",
        )
        style.layout(
            "Candidate.Treeview.Heading",
            [
                ("Treeheading.cell", {"sticky": "nswe"}),
                ("Treeheading.padding", {
                    "sticky": "nswe",
                    "children": [
                        ("Treeheading.text", {"sticky": "nsew"}),
                    ],
                }),
            ]
        )
        style.map(
            "Candidate.Treeview",
            background=[("selected", COLOR_FOCUS)],
            foreground=[("selected", "#0e1620")],
        )

        # 卡片名（左侧字段名）
        style.configure(
            "Field.TLabel",
            background=COLOR_CARD,
            foreground=COLOR_TEXT_DIM,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Value.TLabel",
            background=COLOR_CARD,
            foreground=COLOR_TEXT,
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        # 空值占位符样式（网络模式下上游不提供规格字段时显示「—」）
        style.configure(
            "DimValue.TLabel",
            background=COLOR_CARD,
            foreground=COLOR_TEXT_DIM,
            font=("Microsoft YaHei UI", 11),
        )

    # ---------------- 布局 ----------------

    def _build_layout(self):
        # 顶部状态栏
        top = ttk.Frame(self, style="Panel.TFrame", padding=(self._s(14), self._s(8)))
        top.pack(fill=tk.X, side=tk.TOP)
        ttk.Label(top, text="ChipLookup", style="Title.TLabel").pack(side=tk.LEFT)
        # 同步按钮：头部左侧主操作，紧跟标题（与右侧的极简模式按钮形成对称）。
        # 用 ModeSel.TButton 紧凑暗灰风格，跟头部其它按钮风格一致；同步
        # 期间置 disabled 并改文案，主线程不会因 I/O 卡住（脚本走后台线程）。
        self.btn_sync = ttk.Button(
            top, text="同步", style="ModeSel.TButton",
            command=self._on_sync_clicked,
        )
        self.btn_sync.pack(side=tk.LEFT, padx=(12, 0))
        ToolTip(self.btn_sync,
                "从上游索引（fdnext）拉取最新数据并合并到本地库，仅填空不覆盖已有字段")
        self.status_label = ttk.Label(top, text="", style="Status.TLabel")
        self.status_label.pack(side=tk.LEFT, padx=(24, 0))
        # 常驻文案只放「模式 · 条数」（#8 去重）；数据源完整描述（LABEL · 文件名）
        # 悬停查看，文案由 _refresh_status 动态更新
        self._status_tooltip = ToolTip(self.status_label)

        # 数据源模式选择（radio 单选组：本地CSV / 网络 / 混合）
        # 互斥由共享变量 var_source_mode 自动保证，仅能选中一项；切换后写回配置文件
        mode_box = ttk.Frame(top, style="Panel.TFrame")
        mode_box.pack(side=tk.RIGHT)
        ttk.Label(mode_box, text="数据源模式：", style="Status.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self.var_source_mode = tk.StringVar(value=self.settings.mode)
        for key in ("local", "upstream", "hybrid"):
            rb = ttk.Radiobutton(
                mode_box,
                text=MODE_LABELS[key],
                value=key,
                variable=self.var_source_mode,
                style="ModeRadio.TRadiobutton",
                command=self._on_source_radio,
            )
            rb.pack(side=tk.LEFT, padx=(0, 2))
            self._source_radios.append(rb)
            # 悬停说明各模式差异（#7）：radio 上只放得下短标签
            ToolTip(rb, _SOURCE_MODE_HINTS.get(key, ""))

        # 极简模式切换：隐藏右栏详情区，把空间让给左侧查询列表（偏好写入配置文件）
        self.btn_minimal = ttk.Button(
            top, text="极简模式", style="ModeSel.TButton",
            command=self._toggle_minimal_mode,
        )
        self.btn_minimal.pack(side=tk.RIGHT, padx=(0, 10))
        ToolTip(self.btn_minimal, "隐藏右侧详情区，把空间让给查询列表（Esc 退出）")

        # 主体（左右两栏 + 可拖分隔条）
        body = ttk.Frame(self, style="TFrame", padding=(self._s(14), 0))
        body.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.body = body

        # 分栏：0=左栏(像素宽，可拖分隔条改变)、1=分隔条、2=右栏(吃掉剩余宽度 → 详情动态适配)
        body.columnconfigure(0, weight=0)
        body.columnconfigure(1, weight=0)
        body.columnconfigure(2, weight=1)
        body.rowconfigure(0, weight=1)
        # 左栏像素宽（首帧布局后由 _on_body_configure 换算为窗口宽度的 30%）
        body.bind("<Configure>", self._on_body_configure)

        # 左栏
        left = ttk.Frame(body, style="Panel.TFrame", padding=(self._s(12), self._s(12)))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, self._LEFT_PADX))
        left.configure(width=self._s(300))  # 先占位，首帧由 body Configure 校正
        body.columnconfigure(0, minsize=self._s(300))
        self.left = left

        ttk.Label(left, text="查询输入", style="Panel.TLabel",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 8))

        # 输入框 + 按钮行
        input_row = ttk.Frame(left, style="Panel.TFrame")
        input_row.pack(fill=tk.X, pady=(0, 8))
        self.var_query = tk.StringVar()
        self.var_query.trace_add("write", lambda *a: self._on_query_change())
        entry = ttk.Entry(input_row, textvariable=self.var_query, style="Search.TEntry")
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry = entry
        entry.bind("<Return>", lambda e: self._on_enter())
        entry.bind("<Escape>", self._on_escape)
        ToolTip(entry, "输入料号关键词实时搜索（↑↓ 选择，Enter 查看详情）")

        # 按钮行
        btn_row = ttk.Frame(left, style="Panel.TFrame")
        btn_row.pack(fill=tk.X, pady=(0, 12))
        btn_parse = ttk.Button(btn_row, text="解析料号", style="Accent.TButton",
                               command=self._on_enter)
        btn_parse.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ToolTip(btn_parse, "按编码规则解析料号字符串，显示各字段含义")
        self.btn_fuzzy = ttk.Button(btn_row, text="搜索", style="Blue.TButton",
                                    command=self._on_fuzzy)
        self.btn_fuzzy.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        ToolTip(self.btn_fuzzy, "在当前数据源中模糊搜索（回车同效）")

        # 手写数学公式区域
        math_frame = ttk.Frame(left, style="Panel.TFrame")
        math_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(math_frame, text="手写数学公式", style="Panel.TLabel",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        # 手写板和识别器
        self.math_canvas = MathCanvasWithRecognizer(
            math_frame,
            width=380,
            height=150,
            on_result=self._on_math_result,
            on_error=self._on_math_error,
        )
        self.math_canvas.pack(fill=tk.X)

        # 手写板按钮行
        math_btn_row = ttk.Frame(math_frame, style="Panel.TFrame")
        math_btn_row.pack(fill=tk.X, pady=(6, 0))

        btn_recognize = ttk.Button(
            math_btn_row, text="识别公式", style="Accent.TButton",
            command=self._start_recognize,
        )
        btn_recognize.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ToolTip(btn_recognize, "识别手写板中的数学公式并计算结果")
        self.btn_recognize = btn_recognize

        btn_clear_canvas = ttk.Button(
            math_btn_row, text="清空画布", style="Ghost.TButton",
            command=self.math_canvas.clear,
        )
        btn_clear_canvas.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ToolTip(btn_clear_canvas, "清空手写板")

        ttk.Label(left, text="候选（↑↓ 选择，Enter 查看详情）",
                  style="Hint.TLabel").pack(anchor="w", pady=(0, 6))

        # #10：分页策略选择行已移除——暴露「分页/加载更多」是实现概念不是用户
        # 目标，且占一行纵向空间。固定用「加载更多」（悬停/键盘到末行自动追加，
        # pager 行内按钮兜底）；分页渲染代码路径保留（将来可配置化再启用）。

        # 候选 Treeview 与分页控件的容器（让 tree 区域可扩展、pager 始终贴底）
        tree_box = ttk.Frame(left, style="Panel.TFrame")
        # 关键：关闭 pack 几何传播。ttk.Treeview 列默认 stretch，
        # 会使 tree 的 requested 宽恒等于当前可视宽，进而把 left 的 requested 撑死，
        # grid 因此无法让左栏收缩。关闭传播后左栏宽度完全由 grid 的 minsize 决定。
        tree_box.pack_propagate(False)
        tree_box.pack(fill=tk.BOTH, expand=True, pady=(0, 0))
        self.tree_box = tree_box

        # 分页/加载更多 控件容器（动态填充，候选数 ≤ PAGE_SIZE 时为空）
        # 必须 side=BOTTOM 固定在 tree_box 底部，不被 tree 扩展挤掉
        # 重要：先 pack pager（BOTTOM），再 pack tree（TOP + expand=True），
        # 否则 tree 的 expand 会先吃掉整列空间，导致 pager 被挤到 0 高度。
        self.pager_frame = ttk.Frame(tree_box, style="Panel.TFrame")
        self.pager_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

        # 自定义表头：ttk.Treeview 的 heading 行垂直对齐不可靠，用 tk.Label
        # 自己实现，确保文字在固定高度内完美垂直居中；列宽拖动/左栏缩放时
        # 通过 _sync_header_labels 与 tree 列宽保持像素级同步。
        self._HEADER_HEIGHT = self._s(32)
        # Treeview 单元格文字的固定水平内边距 + 树内容区左边框：
        # 表头文字必须补偿这两项才能与下方单元格文字左缘对齐（实测校准值：
        # pad=6 时左锚定列整体偏右 2px，故取 4）
        self._CELL_TEXT_PADX = 4
        self.header_frame = tk.Frame(
            tree_box, bg=COLOR_PANEL, height=self._HEADER_HEIGHT,
            highlightthickness=0, bd=0,
        )
        self.header_frame.pack(side=tk.TOP, fill=tk.X)
        self.header_frame.pack_propagate(False)
        self._header_labels: Dict[str, tk.Label] = {}
        self._header_anchor: Dict[str, str] = {}
        header_font = ("Microsoft YaHei UI", 10, "bold")
        for c, w, anchor in [
            ("part_number", 110, "w"),
            ("model", 200, "w"),
            ("manufacturer", 110, "w"),
            ("capacity", 130, "center"),
            ("capacity_gb", 50, "center"),
            ("type", 80, "center"),
        ]:
            lbl = tk.Label(
                self.header_frame,
                text={
                    "part_number": "料号", "model": "型号", "manufacturer": "厂商",
                    "capacity": "容量", "capacity_gb": "GB值", "type": "类型",
                }[c],
                bg=COLOR_PANEL,
                fg=COLOR_TEXT_DIM,
                font=header_font,
                anchor=anchor,
                bd=0,
                padx=0,  # tk.Label 默认 padx=1，会让文字整体右偏 1px
                highlightthickness=0,
            )
            lbl.bind("<Motion>", self._on_tree_motion)
            lbl.bind("<ButtonPress-1>", self._on_tree_press)
            self._header_labels[c] = lbl
            self._header_anchor[c] = anchor
        # header_frame 本身也捕获事件（鼠标在 label 间隙时）
        self.header_frame.bind("<Motion>", self._on_tree_motion)
        self.header_frame.bind("<ButtonPress-1>", self._on_tree_press)
        self.header_frame.bind("<Leave>", self._on_tree_leave)

        # 候选列表（高度 = PAGE_SIZE 行，限制候选区过高）
        cols = self._TREE_COLS
        self.tree = ttk.Treeview(
            tree_box, columns=cols, show="", height=self.PAGE_SIZE,
            style="Candidate.Treeview", selectmode="browse",
        )
        # 隐藏原生 #0 树列，避免左侧出现空白列
        self.tree.column("#0", width=0, minwidth=0, stretch=False)
        for c, w, anchor in [
            ("part_number", 110, "w"),
            ("model", 200, "w"),
            ("manufacturer", 110, "w"),
            ("capacity", 130, "center"),
            ("capacity_gb", 50, "center"),
            ("type", 80, "center"),
        ]:
            self.tree.column(c, width=w, anchor=anchor)
        # #13 行 hover 高亮：hover tag 底色（选中行样式优先级更高，不受影响）
        self.tree.tag_configure("hover", background=COLOR_CARD_HOVER)
        # tree 占大头（fill both + expand），但放进 tree_box 后由 box 控制边界
        # side=TOP + expand=True：吃占 pager 留下的剩余空间
        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        # 初始同步一次表头位置/宽度
        self._sync_header_labels()
        self.tree.bind("<<TreeviewSelect>>", self._on_candidate_select)
        self.tree.bind("<Double-1>", self._on_candidate_activate)
        # 宽度自适应：六列始终铺满当前左栏可视宽度（不裁剪也不留白）
        self.tree.bind("<Configure>", lambda e: self._fit_tree_columns(e.width))
        # 鼠标拖拽改列宽：悬停表头分隔线显双箭头光标，按下拖动联动相邻列
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<ButtonPress-1>", self._on_tree_press)
        self.tree.bind("<Leave>", self._on_tree_leave)
        # 行级悬停提示（#7）：数据行停留 600ms 显示完整料号/厂商/容量（add 叠加
        # 绑定，不干扰上面的光标切换逻辑）；滚轮换行后提示失准 → 直接隐藏
        self.tree.bind("<Motion>", self._on_tree_tip_motion, add="+")
        self.tree.bind("<Leave>", self._on_tree_tip_leave, add="+")
        self.tree.bind("<MouseWheel>", lambda e: self._tree_tip_hide(), add="+")
        # 行 hover 高亮（#13）：与光标切换/行提示并行不悖（add 叠加绑定）
        self.tree.bind("<Motion>", self._on_tree_hover_motion, add="+")
        self.tree.bind("<Leave>", self._on_tree_hover_leave, add="+")

        # 左右分栏分隔条：按住拖动调整左栏宽度，右栏随之动态适配。
        # #6 扩大点击目标：画布命中区 12px（原 6px 视觉条即命中区，太窄难点中），
        # 中间 6px 视觉条居中绘制——hover/拖动高亮只重画视觉条（itemconfigure），
        # 命中区宽度恒定
        sash = tk.Canvas(body, width=self._s(12), height=1, bg=COLOR_BG,
                         highlightthickness=0, bd=0, cursor="sb_h_double_arrow")
        sash.grid(row=0, column=1, sticky="ns")
        sash.bind("<Enter>", self._on_sash_enter)
        sash.bind("<Leave>", self._on_sash_leave)
        sash.bind("<ButtonPress-1>", self._on_sash_press)
        sash.bind("<Configure>", lambda e: self._render_sash_band())
        self.sash = sash
        self._sash_color = COLOR_BORDER
        self._sash_band = sash.create_rectangle(0, 0, 1, 1, fill=COLOR_BORDER, width=0)

        # 右栏：详情卡片（吃掉左栏/分隔条之外的剩余宽度）
        right = ttk.Frame(body, style="TFrame")
        right.grid(row=0, column=2, sticky="nsew", padx=(3, 0))
        self.right = right

        # 滚动容器（细条自绘滚动条：6px 圆角滑块 + hover 透明度，见 SlimVScrollbar）
        self.detail_canvas = tk.Canvas(right, bg=COLOR_BG, highlightthickness=0, bd=0)
        self.detail_scroll = SlimVScrollbar(right, command=self.detail_canvas.yview)
        self.detail_canvas.configure(yscrollcommand=self.detail_scroll.set)
        self.detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.detail_inner = ttk.Frame(self.detail_canvas, style="TFrame")
        self.detail_window = self.detail_canvas.create_window((0, 0), window=self.detail_inner, anchor="nw")
        # 内容尺寸变化（渲染/换行/增删字段）→ 刷新滚动区域并自动同步滚动条显隐
        self.detail_inner.bind("<Configure>", self._on_detail_inner_configure)
        # 画布尺寸变化（窗口缩放/滚动条显隐挤占）→ 同步内容宽度 + 滚动条显隐
        self.detail_canvas.bind("<Configure>", self._on_detail_canvas_configure)
        # 滚动
        self.detail_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # 默认空状态
        self._render_detail_empty()

        # 底部操作栏（#9 重组）：视觉权重与使用频率成正比——高频的复制/清空
        # 保留实体按钮，低频的导入/导出/刷新与设置项收进「更多 ⋯」菜单，
        # 退出独占最右（与功能按钮拉开，降低误触）
        bottom = ttk.Frame(self, style="Panel.TFrame", padding=(14, 8))
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        b = ttk.Button(bottom, text="复制全部", style="Accent.TButton",
                       command=lambda: self._copy_current(make_text_for_copy, "已复制全部字段到剪贴板"))
        b.pack(side=tk.LEFT, padx=(0, 6))
        ToolTip(b, "复制当前记录的全部字段到剪贴板")
        b = ttk.Button(bottom, text="复制料号", style="Blue.TButton",
                       command=self._copy_part_no)
        b.pack(side=tk.LEFT, padx=(0, 6))
        ToolTip(b, "复制当前记录的料号 (Ctrl+C)")
        b = ttk.Button(bottom, text="清空", style="Ghost.TButton",
                       command=self._clear_query)
        b.pack(side=tk.LEFT, padx=(0, 6))
        ToolTip(b, "清空查询输入 (Esc)")

        more_btn = ttk.Menubutton(bottom, text="更多 ⋯", style="Ghost.TButton")
        more_btn.pack(side=tk.LEFT, padx=(0, 6))
        ToolTip(more_btn, "导入/导出 xlsx、刷新数据、界面缩放、关于")
        # 深色菜单（与主题一致；tearoff=0 去掉 Win 的可撕离虚线）
        more_menu = tk.Menu(
            more_btn, tearoff=0, bg=COLOR_CARD, fg=COLOR_TEXT,
            activebackground=COLOR_CARD_HOVER, activeforeground=COLOR_TEXT, bd=1,
        )
        more_menu.add_command(label="导入 xlsx…", command=self._import_csv)
        more_menu.add_command(label="导出 xlsx…", command=self._export_csv)
        more_menu.add_separator()
        more_menu.add_command(label="刷新数据", accelerator="F5", command=self._reload_db)
        more_menu.add_separator()
        # 界面缩放（#16）：radio 组写入配置，布局常量启动时已实例化 → 重启生效
        zoom_menu = tk.Menu(
            more_menu, tearoff=0, bg=COLOR_CARD, fg=COLOR_TEXT,
            activebackground=COLOR_CARD_HOVER, activeforeground=COLOR_TEXT,
        )
        self._zoom_var = tk.IntVar(value=int(round(self.settings.ui_zoom * 100)))
        for pct in (85, 100, 115, 130):
            zoom_menu.add_radiobutton(
                label=f"{pct}%", variable=self._zoom_var, value=pct,
                command=lambda p=pct: self._set_ui_zoom(p / 100.0),
            )
        more_menu.add_cascade(label="界面缩放（重启生效）", menu=zoom_menu)
        more_menu.add_separator()
        more_menu.add_command(label="关于 ChipLookup", command=self._show_about)
        more_btn.configure(menu=more_menu)

        b = ttk.Button(bottom, text="退出", style="Ghost.TButton",
                       command=self.destroy)
        b.pack(side=tk.RIGHT)
        ToolTip(b, "退出程序")

    # ---------------- 交互 ----------------

    def _on_mousewheel(self, event):
        """右侧详情滚轮：仅在“指针位于详情区”且“内容真实溢出”时滚动。

        滚轮经 bind_all 全局接入，任意位置滚动都会进到这里。若不分流：
        1) 指针在左栏/顶部/底栏时，右侧详情也会跟着滚动，造成误滚；
        2) 内容未溢出时，Tk 理论上滚不动，但画布 scrollregion 与内层实际高度
           在布局切换瞬间可能残留偏差，滚动会把内容“顶出”视口。
        因此在滚动前做双重兜底：来源必须属于详情区；若内容不足以滚动，
        则忽略本次滚动并顺手把任何残留视口偏移钉回顶部。
        """
        if not self._is_detail_scroll_source(getattr(event, "widget", None)):
            return
        try:
            bbox = self.detail_canvas.bbox("all")
            content_h = bbox[3] if bbox else 0
            view_h = self.detail_canvas.winfo_height()
        except tk.TclError:
            return
        # 内容未超出可视区（含 2px 容差，与滚动条显隐阈值一致）→ 不允许滚动
        if content_h <= view_h + 2:
            try:
                if self.detail_canvas.yview()[0] > 0.0:
                    self.detail_canvas.yview_moveto(0.0)
            except tk.TclError:
                pass
            return
        # Windows / macOS 兼容
        if event.delta:
            self.detail_canvas.yview_scroll(int(-event.delta / 120), "units")
        elif event.num == 4:
            self.detail_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self.detail_canvas.yview_scroll(1, "units")

    def _is_detail_scroll_source(self, widget) -> bool:
        """滚轮事件来源控件是否位于右侧详情滚动区（detail_canvas 或其子控件）。

        bind_all 会把整窗滚轮都送到 _on_mousewheel，靠事件 widget 的 master 链
        上溯判断：能回溯到 detail_canvas / detail_scroll 才算详情区，其余（左栏
        候选列表、顶部输入框、底部操作栏等）一律不响应，杜绝“人在左边滚、
        右边的详情跟着动”的误滚。
        """
        try:
            w = widget
            while w is not None:
                if w is self.detail_canvas or w is self.detail_scroll:
                    return True
                nxt = getattr(w, "master", None)
                if nxt is w:
                    return False
                w = nxt
        except Exception:
            return False
        return False

    def _on_detail_resize(self, width: int):
        """右栏画布宽度变化：同步内容宽度，并让字段值随宽度自适应换行。"""
        try:
            self.detail_canvas.itemconfigure(self.detail_window, width=width)
            self._refresh_value_wrap()
        except tk.TclError:
            pass

    def _on_detail_inner_configure(self, event=None):
        """详情内容尺寸变化：刷新滚动范围，并顺带同步滚动条显隐。"""
        try:
            self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox("all"))
        except tk.TclError:
            pass
        self._schedule_scroll_sync()

    def _on_detail_canvas_configure(self, event):
        """详情画布尺寸变化：同步内容宽度与字段换行，再同步滚动条显隐。"""
        self._on_detail_resize(event.width)
        self._schedule_scroll_sync()

    def _schedule_scroll_sync(self):
        """把滚动条显隐刷新推迟到本轮几何计算完成后再执行，避免读到中间态尺寸。"""
        try:
            self.after_idle(self._sync_detail_scrollbar)
        except tk.TclError:
            pass

    def _sync_detail_scrollbar(self):
        """
        滚动条长度与可视区域匹配的关键：只在内容真正溢出时才挂载滚动条。

        根因：Tk 的滚动条是“视图窗口指示器”，当内容高度(scrollregion) ≤ 可视区高度时，
        yscrollcommand 恒返回 (0.0, 1.0)，Tk 会把滑块拉伸铺满整个轨道，
        表现为一根占满右侧的“超长无效滚动条”。因此内容不溢出时直接卸载滚动条，
        滑块自然不再出现；一旦内容超出可视区再重新挂载，滑块即按比例恢复常规长度。
        """
        try:
            if not self.winfo_exists():
                return
            bbox = self.detail_canvas.bbox("all")
            content_h = bbox[3] if bbox else 0
            view_h = self.detail_canvas.winfo_height()
            if view_h <= 0 or content_h <= 0:
                return
            show = content_h > view_h + 2  # 容差 2px，避免在边界反复抖动
            if show == self._detail_scroll_visible:
                return
            self._detail_scroll_visible = show
            if show:
                # 滚动条须先于画布 pack（否则画布 expand 会占满空间把滚动条挤没）
                self.detail_scroll.pack_forget()
                self.detail_canvas.pack_forget()
                self.detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
                self.detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            else:
                self.detail_canvas.yview_moveto(0.0)  # 归位，避免卸载后留下不可达的滚动偏移
                self.detail_scroll.pack_forget()
        except Exception:
            pass

    def _bind_global_keys(self):
        self.bind("<Up>", self._on_arrow_up)
        self.bind("<Down>", self._on_arrow_down)
        self.bind("<Control-c>", self._on_ctrl_c)
        self.bind("<Control-C>", self._on_ctrl_c)
        self.bind("<Escape>", self._on_escape)
        self.bind("<F5>", lambda e: self._reload_db())

    def _on_ctrl_c(self, event):
        """Ctrl+C处理：如果焦点在Text控件上，允许原生复制；否则复制料号。"""
        widget = event.widget
        # 检查是否是Text控件（disabled状态也允许选择和复制）
        if isinstance(widget, tk.Text):
            return  # 允许原生复制行为
        # 其他情况复制料号
        self._copy_part_no()

    def _refresh_status(self):
        """状态栏主文案三态（#5 响应式 + #8 去重）：

        full（≥ 逻辑 1150px）「模式 · 共 N 条」
        compact（< 1150）    「共 N 条」（模式看顶栏 radio 选中态）
        hidden（< 900）      清空文字（极窄窗给 radio 组让位；悬停 tooltip 仍在）

        去重：describe()（LABEL · 文件名）只进悬停 tooltip，不常驻。
        """
        mode_label = MODE_LABELS.get(self.settings.mode, self.settings.mode)
        count = self.source.count()
        self._status_mode_state = self._status_mode()
        if self._status_mode_state == "hidden":
            self.status_label.configure(text="")
        elif self._status_mode_state == "compact":
            self.status_label.configure(text=f"共 {count} 条")
        else:
            self.status_label.configure(text=f"{mode_label} · 共 {count} 条")
        tip = self._status_tooltip
        if tip is not None:
            tip.update_text(self.source.describe())

    def _status_mode(self) -> str:
        """按窗口物理宽判定状态栏文案态（阈值逻辑 px，经 _s 缩放比较）。

        full ≥ 1150 > compact ≥ 900 > hidden。窗口尚未量出尺寸
        （winfo_width ≤ 1，__init__ 首调时还没布局）按 full 处理，
        避免启动首帧误判；布局就绪后 _enable_win_size_tracking 会重刷。
        """
        try:
            w = self.winfo_width()
        except Exception:
            return "full"
        if w <= 1:
            return "full"
        if w < self._s(900):
            return "hidden"
        if w < self._s(1150):
            return "compact"
        return "full"

    def _refresh_source_buttons(self):
        """把 radio 组件选中项同步到当前模式（互斥由共享变量自动保证）。"""
        if self.var_source_mode.get() != self.settings.mode:
            self.var_source_mode.set(self.settings.mode)

    def _set_radios_enabled(self, enabled: bool):
        """数据源 radio 批量启停：后台切换期间禁用（#12 忙碌指示）。"""
        state = ("!disabled",) if enabled else ("disabled",)
        for rb in self._source_radios:
            try:
                rb.state(state)
            except tk.TclError:
                pass

    def _switch_elapsed(self) -> float:
        """当前/刚完成的一次数据源切换耗时（秒），用于完成 toast（#12）。"""
        started = self._switch_started
        if started is None:
            return 0.0
        return max(0.0, time.monotonic() - started)

    def _on_source_radio(self):
        """radio 单选回调：用户选中哪个单选项就切换哪个数据源。"""
        self._set_source_mode(self.var_source_mode.get())

    def _set_source_mode(self, mode: str):
        """radio 回调入口：请求切到某数据源模式（后台重建，不阻塞 UI）。

        忙碌期间可以继续点别的模式——只记最新目标（latest-wins），
        当前切换收尾后再启动下一个，避免中间态重建浪费。
        """
        if mode == self.settings.mode and not self._switch_busy:
            return  # 已在该模式且无在途切换
        self._request_source_mode(mode)

    def _request_source_mode(self, mode: str, force: bool = False):
        """登记一次切换/刷新请求。force=True 表示无视缓存强制重建（F5）。"""
        self._desired_mode = mode
        self._pending_force = force
        self._pump_switch()

    def _pump_switch(self):
        """空闲时启动一次在途切换；忙碌时由收尾逻辑（latest-wins）负责接力。"""
        if self._switch_busy:
            return
        mode = self._desired_mode
        if mode is None:
            return
        if not self._pending_force and mode == self.settings.mode:
            # 已在该模式、又无强制刷新 → 无事可做
            self._desired_mode = None
            self._pending_force = False
            self._refresh_source_buttons()
            return

        self._switch_busy = True
        force = self._pending_force
        self._pending_force = False
        self._desired_mode = None
        self._switch_token += 1
        token = self._switch_token
        label = MODE_LABELS.get(mode, mode)
        self.status_label.configure(
            text=f"正在{'刷新' if force else '切换'}数据源至「{label}」…"
        )
        self._switch_started = time.monotonic()
        # 忙碌期间禁用 radio（#12）：后台构建是秒级重活，连点只会往
        # latest-wins 队列后排；禁用态比「点了没反应」更明确。收尾自动恢复。
        self._set_radios_enabled(False)
        self._start_switch_worker(token, mode, force)
        self._ensure_polling()

    def _start_switch_worker(self, token: int, mode: str, force: bool):
        """后台线程重建数据源（make_source + 全量 list_records），结果入队。"""
        snapshot = copy.copy(self.settings)
        snapshot.mode = mode

        def _run():
            try:
                src = self._build_source_snapshot(snapshot, mode, force)
                records = src.list_records()
                # 后台线程顺手把 ~40k×10 字段的预归一化做好，切完首次按键不再卡
                try:
                    warm_prepared(records)
                except Exception:
                    pass  # 预热失败无碍：首次查询惰性重建即可
                self._switch_queue.put((token, "ok", mode, src, records))
            except Exception as exc:  # SourceLoadError / OSError 等一律兜底
                self._switch_queue.put((token, "err", mode, exc))

        threading.Thread(
            target=_run, daemon=True, name="chiplookup-source-switch"
        ).start()

    def _build_source_snapshot(
        self, snapshot: Settings, mode: str, force: bool
    ) -> RecordSource:
        """按模式取缓存或重建数据源。仅在后台线程调用。"""
        if not force:
            cached = self._source_cache.get(mode)
            if cached is not None:
                return cached
        return make_source(snapshot)

    def _ensure_polling(self):
        """确保主线程有一个 after() 轮询在跑（仅在途切换期间）。"""
        if self._polling:
            return
        self._polling = True
        self.after(40, self._poll_switch_queue)

    def _poll_switch_queue(self):
        """主线程轮询后台任务结果并落地。

        队列上同时跑两类任务：
            "ok" / "err"            数据源切换（用 _switch_token）
            "sync_ok" / "sync_err"  上游同步结果（用 _sync_token，独立计数）

        两类任务用不同 token 体系，避免相互过期：同步结果可能在切换
        数据源（递增 _switch_token）后才到达，但同步的 _sync_token 不
        受影响。分发按 kind 字段路由到不同处理函数。
        """
        try:
            while True:
                item = self._switch_queue.get_nowait()
                token = item[0]
                kind = item[1]
                if kind in ("ok", "err"):
                    if token != self._switch_token:
                        continue  # 过期结果
                elif kind in ("sync_ok", "sync_err"):
                    if token != self._sync_token:
                        continue
                if kind == "ok":
                    _, _, mode, src, records = item
                    self._apply_switch_result(token, mode, src, records)
                elif kind == "err":
                    _, _, mode, exc = item
                    self._handle_switch_error(token, mode, exc)
                elif kind == "sync_ok":
                    _, _, csv_path, rc, log = item
                    self._handle_sync_result(token, csv_path, (rc, log), log)
                elif kind == "sync_err":
                    _, _, csv_path, exc, _ = item
                    self._handle_sync_error(token, csv_path, exc)
        except queue.Empty:
            pass
        # 仍在途 → 继续轮询；否则停表
        if self._switch_busy:
            self.after(40, self._poll_switch_queue)
        else:
            self._polling = False

    def _apply_switch_result(
        self, token: int, mode: str, src: RecordSource, records: List[dict]
    ):
        """后台构建成功：一次性把新数据源落到主线程状态（不阻塞）。"""
        self.source = src
        self._source_cache[mode] = src
        self._records_cache = records
        previous = self.settings.mode
        changed = previous != mode
        self.settings.mode = mode
        if changed:
            try:
                self.settings.save()  # 持久化模式选择（配置文件下次启动生效）
            except OSError as exc:
                self._toast(f"模式已切换，但配置保存失败：{exc}")
        self._refresh_status()
        # 若无更新的切换目标，把 radio 与查询结果对齐；否则让位给最新请求
        if self._desired_mode is None:
            self._refresh_source_buttons()
            elapsed = self._switch_elapsed()
            if changed:
                label = MODE_LABELS.get(mode, mode)
                # #8 去重：条数/描述状态栏刚刷新过、文件名悬停可见 →
                # toast 只说结果与耗时（#12）
                self._toast(f"已切换至「{label}」 · {elapsed:.1f}s")
            else:
                self._toast(f"已刷新 · {elapsed:.1f}s")
            if self.var_query.get().strip():
                self._run_query()
            else:
                self._clear_detail()
                self._render_detail_empty()
            if not changed and self.on_change:
                self.on_change()
        self._switch_busy = False
        # 同步按钮复位：数据源切换收尾时一并把同步按钮从禁用态恢复——
        # 这条路径在「同步成功 → 自动重建数据源」联动场景下是必须的
        self._set_busy_ui("同步", busy=False)
        # 切换收尾：radio 恢复可用（#12）；若 latest-wins 队列还有新目标，
        # 下一行 _pump_switch 会立刻再次禁用并接力
        self._set_radios_enabled(True)
        self._pump_switch()

    def _handle_switch_error(self, token: int, mode: str, exc: Exception):
        """后台构建失败：回滚 radio/状态；若用户又点了新模式则直接接力。"""
        self._switch_busy = False
        # 失败收尾：radio 恢复可用（#12），让用户重试或改选其他模式
        self._set_busy_ui("同步", busy=False)
        if self._desired_mode is None:
            # 没有更新的目标 → 回滚到当前已加载模式
            self._refresh_source_buttons()
            self._refresh_status()
            # #14：切换失败改非阻断 toast（原模态框打断浏览流；错误红字停留
            # 3.8s，radio 已回滚到可用模式，用户可直接重试或改选）
            self._toast(
                f"切换「{MODE_LABELS.get(mode, mode)}」失败：{exc}", error=True)
        # 已有更新的目标（用户忙中又点了别的模式）→ 不打断，直接跑下一个
        self._pump_switch()

    def _cancel_pending_query(self):
        """取消尚未触发的防抖查询（回车/搜索按钮等显式动作前调用）。"""
        if self._query_after_id is not None:
            try:
                self.after_cancel(self._query_after_id)
            except Exception:
                pass
            self._query_after_id = None

    def _on_query_change(self):
        """输入防抖：连续敲键只调度一次查询，避免上游 4 万条全量扫描。"""
        self._cancel_pending_query()
        self._query_after_id = self.after(150, self._flush_query)

    def _flush_query(self):
        self._query_after_id = None
        self._run_query()

    def _on_escape(self, event=None):
        """Esc：切换极简/完整模式。

        输入框与顶层都绑定本方法并返回 "break"，避免一次按键触发两处。
        """
        self._set_minimal_mode(not self._minimal_mode)
        return "break"

    def _on_enter(self):
        """回车：如果候选唯一或已选定某项 → 显示详情；否则聚焦候选列表第一项。"""
        self._cancel_pending_query()
        if not self._candidates:
            self._run_query()
            return
        if self._selected_index >= 0 and self._selected_index < len(self._candidates):
            # 极简模式下不退回完整模式：详情在后台静默更新，
            # 搜索/回车绝不打断极简状态，更不改写持久化偏好
            rec = self._candidates[self._selected_index][0]
            self._render_detail(rec)
            self.entry.focus_set()
        elif len(self._candidates) == 1:
            rec = self._candidates[0][0]
            self._render_detail(rec)
            self.entry.focus_set()
        else:
            # 多个候选 → 把焦点切到候选列表，让用户可用上下键
            self.tree.selection_set(self.tree.get_children()[0])
            self.tree.focus_set()
            self.tree.focus(self.tree.get_children()[0])

    def _on_fuzzy(self):
        """显式「搜索」按钮：强制模糊模式（即使恰好精确匹配也展示全部候选）。"""
        self._cancel_pending_query()
        self._run_query()

    def _start_recognize(self) -> None:
        """「识别公式」入口：先给按钮加识别中状态，再交给识别器。

        pix2tex 首次调用要加载 ~100MB 权重（实测约 70 秒），旧实现直接把
        recognize_async 挂到按钮上，用户点完到出结果之间界面毫无反应，
        极易被当成卡死而反复点击。这里用按钮文案 + 禁用态给出明确反馈，
        由 _on_math_result / _on_math_error 统一复位。
        """
        if getattr(self.math_canvas, "_is_recognizing", False):
            return  # 已在识别中，避免按钮被永久置灰
        self.btn_recognize.configure(text="识别中…", state="disabled")
        self._toast("正在识别手写公式…")
        self.update_idletasks()
        self.math_canvas.recognize_async()

    def _reset_recognize_button(self) -> None:
        try:
            self.btn_recognize.configure(text="识别公式", state="normal")
        except tk.TclError:
            pass

    def _on_math_result(self, latex_str: str, result: dict) -> None:
        """手写公式识别成功回调"""
        self._reset_recognize_button()
        self._clear_detail()

        card = ttk.Frame(self.detail_inner, style="Card.TFrame", padding=CARD_PADDING)
        card.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(card, text="识别结果", style="Card.TLabel",
                  font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 10))

        # LaTeX 表达式
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="LaTeX:", style="Field.TLabel", width=10).pack(side=tk.LEFT)
        ttk.Label(row, text=latex_str, style="Value.TLabel").pack(side=tk.LEFT)

        # 计算结果
        if "error" not in result:
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text="结果:", style="Field.TLabel", width=10).pack(side=tk.LEFT)
            ttk.Label(row, text=result["result"], style="Value.TLabel",
                      foreground=COLOR_ACCENT).pack(side=tk.LEFT)

            # 表达式
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text="表达式:", style="Field.TLabel", width=10).pack(side=tk.LEFT)
            ttk.Label(row, text=result["expr"], style="Value.TLabel").pack(side=tk.LEFT)

            # 复制按钮
            btn_copy = ttk.Button(
                card, text="复制 LaTeX", style="ModeSel.TButton",
                command=lambda: self._copy_to_clipboard(latex_str),
            )
            btn_copy.pack(anchor="w", pady=(8, 0))
        else:
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text="错误:", style="Field.TLabel", width=10).pack(side=tk.LEFT)
            ttk.Label(row, text=result["error"], style="Value.TLabel",
                      foreground=COLOR_DANGER).pack(side=tk.LEFT)

    def _on_math_error(self, error_msg: str) -> None:
        """手写公式识别失败回调"""
        self._reset_recognize_button()
        self._toast("识别失败：%s" % error_msg, error=True)

    def _on_arrow_up(self, event=None):
        # 如果焦点在输入框，把事件转移给 tree
        if self.focus_get() is self.entry:
            return None
        self._move_selection(-1)
        return "break"

    def _on_arrow_down(self, event=None):
        if self.focus_get() is self.entry:
            return None
        self._move_selection(1)
        return "break"

    def _move_selection(self, delta: int):
        children = self.tree.get_children()
        if not children:
            return
        cur = self.tree.focus()
        if not cur:
            idx = 0
        else:
            try:
                idx = children.index(cur)
            except ValueError:
                idx = 0
        idx = max(0, min(len(children) - 1, idx + delta))
        self.tree.selection_set(children[idx])
        self.tree.focus(children[idx])
        self.tree.see(children[idx])
        # #10：键盘浏览到已加载末行 → 自动追加更多（加载更多模式，焦点不跳）
        self._maybe_autoload_more(children[idx])

    def _on_candidate_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            # iid 是 candidates 的全局索引字符串（见 _insert_rows）
            idx = int(sel[0])
        except (ValueError, IndexError):
            return
        if idx < 0 or idx >= len(self._candidates):
            return
        self._selected_index = idx
        rec = self._candidates[idx][0]
        self._render_detail(rec)
        if self._minimal_mode:
            # 极简模式详情区隐藏（#11）：选中即后台静默更新，用户感知不到
            # → 状态栏 toast 点明；连续选择经 _toast 防抖不闪烁
            self._toast("详情已在后台更新 · Esc 返回完整模式查看")

    def _on_candidate_activate(self, event=None):
        # 双击同样不打断极简模式（详情后台更新）；退出极简只走按钮或 Esc
        self._on_candidate_select(event)

    # ---------------- 候选区分页 / 加载更多 ----------------

    def _render_candidates(self):
        """根据当前分页模式 + 候选数渲染候选表格 + 分页控件。

        - 候选数 ≤ PAGE_SIZE：直接全显示，不渲染控件
        - 分页模式：「« 第 N / M 页 共 X 条 »」
        - 加载更多模式：[加载更多 (还剩 Y 条)] 已显示 X / N
        """
        # 清空 tree 与 pager
        for r in self.tree.get_children():
            self.tree.delete(r)
        for w in self.pager_frame.winfo_children():
            w.destroy()
        self._refresh_mode_buttons()

        total = len(self._candidates)
        page_size = self.PAGE_SIZE

        if total == 0:
            return  # 无候选：不显示任何控件

        if total <= page_size:
            # 全部显示，无需分页控件
            self._insert_rows(0, total)
            self._select_first()
            return

        if self.page_mode.get() == "paginate":
            total_pages = (total + page_size - 1) // page_size
            page = max(0, min(self._current_page, total_pages - 1))
            self._current_page = page
            start = page * page_size
            end = min(start + page_size, total)
            self._insert_rows(start, end)
            # 分页控件：«  第 N/M 页  ·  共 X 条  »（#7：«» 缩写悬停说明）
            btn_prev = ttk.Button(self.pager_frame, text="«", style="ModeSel.TButton", width=2,
                                  command=self._prev_page)
            btn_prev.pack(side=tk.LEFT, padx=(0, 4))
            ToolTip(btn_prev, "上一页")
            ttk.Label(self.pager_frame, text=f"第 {page + 1} / {total_pages} 页",
                      style="Hint.TLabel").pack(side=tk.LEFT)
            btn_next = ttk.Button(self.pager_frame, text="»", style="ModeSel.TButton", width=2,
                                  command=self._next_page)
            btn_next.pack(side=tk.LEFT, padx=(4, 0))
            ToolTip(btn_next, "下一页")
            ttk.Label(self.pager_frame, text=f"·  共 {total} 条",
                      style="Hint.TLabel").pack(side=tk.LEFT, padx=(8, 0))
        else:  # load_more
            self._insert_rows(0, self._visible_count)
            self._refresh_pager()

        self._select_first()

    def _insert_rows(self, start: int, end: int):
        """在 tree 中插入 candidates[start:end] 的行，iid 用全局索引。"""
        for i in range(start, end):
            rec, _, hit = self._candidates[i]
            mark = "★ " if hit == "part_number 精确匹配" else ""
            self.tree.insert(
                "", "end",
                iid=str(i),
                values=(
                    mark + rec.get("part_number", ""),
                    rec.get("model", ""),
                    rec.get("manufacturer", ""),
                    _capacity_display(rec.get("capacity", ""), rec.get("bit_width", "")),
                    _capacity_gb(rec.get("capacity", "")),
                    rec.get("type", ""),
                ),
            )

    # ---------------- 布局自适应 ----------------

    # 候选表六列（顺序/语义固定）
    _TREE_COLS = ("part_number", "model", "manufacturer", "capacity", "capacity_gb", "type")
    # 左栏右缘与分隔条之间的留白（grid padx 逻辑值；__init__ 里按 DPI 缩放为
    # 实例属性，_apply_pane_width 需一并计入列宽）
    _LEFT_PADX = 7
    # 候选表六列宽度策略（逻辑像素，使用时经 _col_metrics 按 DPI 缩放）。
    # 三档语义，解决「窄左栏下料号列被挤到只剩 2 字符」的核心问题：
    #   PREFERRED 常规最小宽：接近完整内容的最小宽度，空间富余时从此起步；
    #   FLOOR     硬底线：极限可辨宽（低于此内容完全不可读），缺口分配的起点；
    #   DEFICIT_W 缺口分配权重：可用宽介于 sum(FLOOR) 与 sum(PREFERRED) 之间时，
    #             富余量按此分配——料号是查询工具的核心数据，占比最高；
    #             型号与料号高度重复（本数据集两者字符串基本一致），最先被压缩。
    _TREE_COL_PREFERRED = {
        "part_number": 128, "model": 96, "manufacturer": 62,
        "capacity": 102, "capacity_gb": 50, "type": 40,
    }
    _TREE_COL_FLOOR = {
        "part_number": 88, "model": 32, "manufacturer": 46,
        "capacity": 72, "capacity_gb": 40, "type": 30,
    }
    _TREE_COL_DEFICIT_W = {
        "part_number": 0.40, "capacity": 0.18, "manufacturer": 0.14,
        "model": 0.10, "capacity_gb": 0.10, "type": 0.08,
    }
    # 常规分支的加宽权重（超出 PREFERRED 的富余按此分配）
    _TREE_COL_WEIGHT = {
        "part_number": 0.30, "model": 0.25, "manufacturer": 0.18,
        "capacity": 0.12, "capacity_gb": 0.08, "type": 0.07,
    }

    def _col_metrics(self):
        """列宽策略常量的 DPI 缩放副本（缓存，避免拖栏高频调用重复换算）。

        注意：仅两个「像素」字典（PREFERRED/FLOOR）参与缩放；两个「比例」
        字典（DEFICIT_W/WEIGHT）必须原样返回——若走 int(round(0.46)) 会把
        权重全部归零/钳一，分配退化为等权（实测踩坑）。
        """
        cached = getattr(self, "_col_metrics_cache", None)
        if cached is None:
            s = self._ui_scale
            cached = (
                {c: max(1, int(round(v * s))) for c, v in self._TREE_COL_PREFERRED.items()},
                {c: max(1, int(round(v * s))) for c, v in self._TREE_COL_FLOOR.items()},
                dict(self._TREE_COL_DEFICIT_W),
                dict(self._TREE_COL_WEIGHT),
            )
            self._col_metrics_cache = cached
        return cached

    def _fit_tree_columns(self, width: Optional[int] = None):
        """把候选表 5 列的总宽铺满当前左栏可视宽度，杜绝横向裁切/留白。

        三段式分配（宽度常量见 _TREE_COL_PREFERRED/_FLOOR/_DEFICIT_W）：
        - 常规（avail ≥ ΣPREFERRED）：PREFERRED 起步，富余按 WEIGHT 加宽；
        - 缺口（ΣFLOOR ≤ avail < ΣPREFERRED）：从 FLOOR 起步，富余按 DEFICIT_W
          分配（料号优先、单列不超过 PREFERRED，超出的份额回流给其余列）——
          旧版在此场景把缺口全部记在料号头上（容量列保住 130px、料号仅剩
          24px 只显示 2 字符），是核心数据不可读的根因；
        - 极窄（avail < ΣFLOOR）：按 FLOOR 等比压缩，料号保持最大占比。
        - 用户拖过列宽后：保留用户设定的各列比例等比缩放，但以 FLOOR 托底。
        """
        tree = self.tree
        try:
            avail = (width if width else tree.winfo_width()) - self._s(4)
        except Exception:
            return
        if avail < self._s(180):
            return
        cols = self._TREE_COLS
        pref, floor, defw, weight = self._col_metrics()
        if self._tree_col_custom and self._tree_col_cache:
            # 自定义模式：把当前各列宽等比缩放到新可视宽度（保留用户手感），
            # 以 FLOOR 托底（旧版用 PREFERRED 托底，窄栏下会撑爆总宽造成尾部裁切）
            old_total = sum(self._tree_col_cache)
            if old_total > 0:
                plan = [
                    max(floor[c], int(w * avail / old_total + 0.5))
                    for w, c in zip(self._tree_col_cache, cols)
                ]
                # 四舍五入误差并入首列，保证总宽严格等于可视宽度
                plan[0] += avail - sum(plan)
                plan[0] = max(floor[cols[0]], plan[0])
                if sum(plan) == avail:
                    widths = tuple(plan)
                    if widths == self._tree_col_cache:
                        return
                    self._tree_col_cache = widths
                    for c, w in zip(cols, widths):
                        tree.column(c, width=w)
                    self._sync_header_labels()
                    return
                # 托底后总宽放不下（用户比例与硬底线冲突，左栏过窄）：
                # 本次放弃保手感，落到下方标准三段分配（_tree_col_custom 保持
                # 不变，窗口重新变宽后仍优先按用户比例缩放）
        base = sum(floor.values())
        pref_total = sum(pref.values())
        if avail <= base:
            # 极窄：按 FLOOR 等比压缩（比例缩放保料号最大占比）
            raw = {c: floor[c] * avail / base for c in cols}
        elif avail < pref_total:
            # 缺口：FLOOR 起步 + DEFICIT_W 分配富余，单列封顶 PREFERRED，
            # 触顶列退出分配、份额回流其余列（水量填充，最多数轮收敛）
            raw = {c: float(floor[c]) for c in cols}
            pool = float(avail - base)
            active = list(cols)
            for _ in range(4):
                if pool <= 1.0 or not active:
                    break
                wsum = sum(defw[c] for c in active)
                progressed = False
                for c in list(active):
                    give = pool * defw[c] / wsum
                    room = pref[c] - raw[c]
                    take = min(give, room)
                    if take > 0:
                        raw[c] += take
                        pool -= take
                        progressed = True
                    if pref[c] - raw[c] < 0.5:
                        active.remove(c)
                if not progressed:
                    break
        else:
            # 常规：PREFERRED 起步，富余按 WEIGHT 加宽
            extra = avail - pref_total
            raw = {c: pref[c] + extra * weight[c] for c in cols}
        plan = {c: max(self._s(20), int(round(raw[c]))) for c in cols}
        # 舍入尾差并入「料号」列，保证总和与可视宽度严格一致
        plan["part_number"] = max(self._s(20),
                                  plan["part_number"] + avail - sum(plan.values()))
        widths = tuple(plan[c] for c in cols)
        if widths == self._tree_col_cache:
            return
        self._tree_col_cache = widths
        for k in cols:
            tree.column(k, width=plan[k])
        self._sync_header_labels()

    def _sync_header_labels(self):
        """让自定义表头 label 的宽度/位置与 tree 列宽严格同步。

        列宽由 _fit_tree_columns / _on_col_drag 维护；每次列宽变化后调用本方法，
        header_frame 内的 tk.Label 会按 tree.column(c, "width") 被 place 到对应
        x 坐标，确保拖动左栏或列分隔线时标题与下方单元格始终对齐。

        水平补偿：
        - Treeview 内容区相对树 widget 有 ~2px 左边框（取首行 bbox 校准）；
        - 单元格文字还有 ~_CELL_TEXT_PADX 的固定水平内边距。
        因此左锚定列的表头文字要再右移同样距离，才能和单元格文字左缘对齐；
        居中列只需补边框偏移即可让文字中心与单元格中心重合。
        """
        if not getattr(self, "_header_labels", None):
            return
        try:
            # 树内容区相对树 widget 的左边框是固定 2px（DPI 缩放下偶发 bbox 抖动
            # 返回 3，故不用实测值，直接用常量，保证多次同步结果稳定）
            off = 2
            pad = self._CELL_TEXT_PADX
            x = 0
            for c in self._TREE_COLS:
                w = self.tree.column(c, "width")
                lbl = self._header_labels[c]
                if self._header_anchor.get(c) == "w":
                    lbl.place(x=x + off + pad, y=0,
                              width=max(20, w - off - pad),
                              height=self._HEADER_HEIGHT)
                else:
                    lbl.place(x=x + off, y=0, width=w, height=self._HEADER_HEIGHT)
                x += w
        except tk.TclError:
            pass

    # ---------------- 左栏列宽鼠标拖拽 ----------------

    def _tree_col_widths(self) -> Tuple[int, ...]:
        try:
            return tuple(self.tree.column(c, "width") for c in self._TREE_COLS)
        except tk.TclError:
            fallback = self._tree_col_cache
            if not fallback:
                floor = self._col_metrics()[1]
                fallback = tuple(floor[c] for c in self._TREE_COLS)
            return fallback

    def _divider_index_at(self, x: int, tol: int = 6) -> Optional[int]:
        """返回 x 落在哪条表头分隔线上（第 i 列与第 i+1 列之间）；不在则 None。"""
        widths = self._tree_col_widths()
        acc = 0
        for i, w in enumerate(widths[:-1]):
            acc += w
            if abs(x - acc) <= tol:
                return i
        return None

    def _tree_event_x(self, event) -> int:
        """把任意 widget 上的事件 x 换算为树内容区的相对坐标。

        自绘表头的 label/header_frame 与树是不同 widget，event.x 是各自
        widget 相对坐标；分隔线判定必须统一到树坐标系（逻辑 px）。
        """
        if event.widget is self.tree:
            return event.x
        try:
            return event.x + event.widget.winfo_rootx() - self.tree.winfo_rootx()
        except tk.TclError:
            return event.x

    def _set_drag_cursor(self, cursor: str):
        """统一设置/恢复表头与树上的拖拽光标（cursor 是 per-widget 的）。"""
        widgets = [self.header_frame, self.tree] + list(self._header_labels.values())
        for w in widgets:
            try:
                w.configure(cursor=cursor)
            except tk.TclError:
                pass

    def _on_tree_motion(self, event):
        """悬停表头分隔线时把光标切换成左右拖拽箭头，提供可拖拽的视觉反馈。

        仅自绘表头（header_frame / 标题 label）上生效；树区域已是数据行，
        不再提供列宽拖拽热区。
        """
        if getattr(self, "_col_drag", None):
            return
        want = ""
        if event.widget is not self.tree and event.y <= self._HEADER_HEIGHT:
            if self._divider_index_at(self._tree_event_x(event)) is not None:
                want = "sb_h_double_arrow"
        if want != self._tree_cursor:
            self._tree_cursor = want
            self._set_drag_cursor(want)

    def _on_tree_leave(self, event=None):
        if not getattr(self, "_col_drag", None):
            self._tree_cursor = ""
            self._set_drag_cursor("")

    # ---------------- 行级悬停提示（候选列表数据行，#7） ----------------

    def _tree_row_tooltip_text(self, iid: str) -> str:
        """数据行提示文案：完整料号 / 厂商 · 容量（列窄被裁的内容在这里看全）。"""
        try:
            rec = self._candidates[int(iid)][0]
        except (ValueError, IndexError, TypeError):
            return ""
        pn = (rec.get("part_number", "") or "").strip()
        mfr = (rec.get("manufacturer", "") or "").strip()
        cap = _capacity_display(rec.get("capacity", ""), rec.get("bit_width", ""))
        second = " · ".join(x for x in (mfr, cap) if x)
        return "\n".join(x for x in (pn, second) if x)

    def _on_tree_tip_motion(self, event):
        """数据行 Motion：换行即隐藏并重新防抖；停留 600ms 弹出行信息。

        拖列宽（_col_drag）期间抑制——此时指针贴着表头分隔线移动，
        弹气泡纯属干扰。
        """
        if self._col_drag is not None:
            self._tree_tip_hide()
            return
        try:
            row = self.tree.identify_row(event.y)
        except tk.TclError:
            return
        if row:
            # #10：悬停到已加载末行且还有剩余 → 自动追加（加载更多模式）
            self._maybe_autoload_more(row)
        if row != self._tree_tip_row:
            self._tree_tip_hide()
            self._tree_tip_row = row
            if row:
                self._tree_tip_after = self.after(
                    600, lambda: self._tree_tip_show(row))

    def _on_tree_tip_leave(self, _event=None):
        """指针离开候选列表：取消防抖并收起气泡。"""
        self._tree_tip_hide()
        self._tree_tip_row = None

    def _tree_tip_hide(self):
        """取消挂起的防抖并销毁当前气泡（换行/移出/拖列/滚轮时调用）。"""
        if self._tree_tip_after is not None:
            try:
                self.after_cancel(self._tree_tip_after)
            except Exception:
                pass
            self._tree_tip_after = None
        if self._tree_tip is not None:
            try:
                self._tree_tip.destroy()
            except tk.TclError:
                pass
            self._tree_tip = None

    def _tree_tip_show(self, row: str):
        """防抖到期：若指针仍在触发行上且行数据可读，弹出信息气泡。"""
        self._tree_tip_after = None
        if self._col_drag is not None or row != self._tree_tip_row:
            return
        try:
            if not self.tree.exists(row):
                return
        except tk.TclError:
            return
        text = self._tree_row_tooltip_text(row)
        if not text:
            return
        self._tree_tip = _create_tip_window(self, text)
        _place_tip_window(self._tree_tip)

    # ---------------- 行 hover 高亮（#13） ----------------

    def _on_tree_hover_motion(self, event):
        """指针所在行加 hover 底色（tag 移动方案，零自绘）。

        选中行的 selected 样式优先级高于 tag 背景，选中高亮不受影响；
        重渲染后行iid不变但 tags 被清空，指针再动一格即恢复。
        """
        try:
            row = self.tree.identify_row(event.y)
        except tk.TclError:
            return
        if row == self._tree_hover_row:
            return
        old = self._tree_hover_row
        self._tree_hover_row = row
        if old is not None:
            try:
                if self.tree.exists(old):
                    self.tree.item(old, tags=())
            except tk.TclError:
                pass
        if row:
            try:
                self.tree.item(row, tags=("hover",))
            except tk.TclError:
                pass

    def _on_tree_hover_leave(self, _event=None):
        """指针离开候选列表：撤掉 hover 高亮。"""
        old = self._tree_hover_row
        self._tree_hover_row = None
        if old is not None:
            try:
                if self.tree.exists(old):
                    self.tree.item(old, tags=())
            except tk.TclError:
                pass

    def _on_tree_press(self, event):
        """左键落在自绘表头分隔线附近时接管拖动：本列变宽、右侧各列让位（总宽恒定）。

        树区域（数据行）上的点击直接放行，交给 Treeview 原生的行选择逻辑。
        返回 "break" 以阻止事件继续传播。
        """
        if event.widget is self.tree:
            return None
        if event.y > self._HEADER_HEIGHT:
            return None
        i = self._divider_index_at(self._tree_event_x(event))
        if i is None:
            return None
        self._col_drag = (i, event.x_root)
        self._col_drag_widths = self._tree_col_widths()  # 拖动起点各列宽（基准）
        self.tree.bind_all("<B1-Motion>", self._on_col_drag)
        self.tree.bind_all("<ButtonRelease-1>", self._on_col_drag_end)
        self._set_drag_cursor("sb_h_double_arrow")
        return "break"

    def _on_col_drag(self, event):
        """拖动中：第 i 列跟随指针，其余列让位/补位，总宽恒定。

        以按下瞬间的快照为基准（不叠加累计位移），避免 Motion 事件重复累计。
        总宽守恒规则：第 i 列增宽 Δ，右侧各列总宽必缩 Δ；第 i 列变窄 Δ，右侧各列总宽必涨 Δ。
        """
        drag = getattr(self, "_col_drag", None)
        if not drag or not self._col_drag_widths:
            return
        i, x_root0 = drag
        cols = self._TREE_COLS
        start = list(self._col_drag_widths)
        total = sum(start)
        dx = event.x_root - x_root0

        _, floor, _, _ = self._col_metrics()
        min_me = floor[cols[i]]
        min_suffix = sum(floor[cols[j]] for j in range(i + 1, len(cols)))
        # 可拖范围：自身不窄于硬底线；也不超过「总宽 - 右侧硬底线总和」（右侧不可被挤破）
        w_i = max(min_me, min(start[i] + int(round(dx)), total - min_suffix))
        if w_i == start[i]:
            return
        self.tree.column(cols[i], width=w_i)
        delta = w_i - start[i]

        if delta > 0:
            # 加宽：右侧按各自“可压缩余量”成比例收缩（不突破硬底线）
            slack = [start[j] - floor[cols[j]] for j in range(i + 1, len(cols))]
            total_slack = sum(slack)
            if total_slack <= 0:
                return
            used = 0
            for off, j in enumerate(range(i + 1, len(cols))):
                shrink = min(slack[off], int(round(delta * slack[off] / total_slack)))
                self.tree.column(cols[j], width=start[j] - shrink)
                used += shrink
            # 舍入尾差：从最右列起，把仍有压缩余量的列再压缩补齐
            rest = delta - used
            for j in range(len(cols) - 1, i, -1):
                if rest <= 0:
                    break
                cur = self.tree.column(cols[j], "width")
                cap = cur - floor[cols[j]]
                take = min(cap, rest)
                if take > 0:
                    self.tree.column(cols[j], width=cur - take)
                    rest -= take
            self._sync_header_labels()
        else:
            # 变窄：释放的像素按右侧当前宽度比例补宽（总宽守恒）
            grow = -delta
            suffix_w = start[i + 1:]
            total_w = sum(suffix_w)
            if total_w <= 0:
                return
            added = 0
            for j in range(i + 1, len(cols)):
                w = start[j] + int(round(grow * suffix_w[j - i - 1] / total_w))
                self.tree.column(cols[j], width=w)
                added += w - start[j]
            # 舍入尾差并入最后一列
            self.tree.column(cols[-1],
                             width=self.tree.column(cols[-1], "width") + (grow - added))
            self._sync_header_labels()

    def _on_col_drag_end(self, event):
        """拖动结束：固化为自定义列宽，窗口缩放时按其比例等比缩放。"""
        try:
            self.tree.unbind_all("<B1-Motion>")
            self.tree.unbind_all("<ButtonRelease-1>")
        except tk.TclError:
            pass
        self._col_drag = None
        self._col_drag_widths = None
        self._tree_col_custom = True
        self._tree_col_cache = self._tree_col_widths()
        self._tree_cursor = ""
        self._set_drag_cursor("")
        self._sync_header_labels()

    # ---------------- 左右分栏拖拽（分隔条） ----------------

    def _render_sash_band(self, color: Optional[str] = None):
        """重绘分隔条中间的视觉条（全高、6px 居中）；color 缺省用当前色。"""
        sash = self.sash
        try:
            w, h = sash.winfo_width(), sash.winfo_height()
            if w <= 1 or h <= 1:
                return  # 尚未布局：<Configure> 回调后会再画
        except tk.TclError:
            return
        if color is None:
            color = self._sash_color
        band = self._s(6)
        x0 = max(0, (w - band) // 2)
        try:
            sash.coords(self._sash_band, x0, 0, x0 + band, h)
            sash.itemconfigure(self._sash_band, fill=color)
        except tk.TclError:
            pass

    def _set_sash_color(self, color: str):
        """hover/拖动高亮：只换视觉条颜色（#6 canvas 化后命中区几何恒定）。"""
        self._sash_color = color
        self._render_sash_band(color)

    def _on_sash_enter(self, event):
        if not getattr(self, "_pane_drag", None):
            self._set_sash_color("#3a4d63")

    def _on_sash_leave(self, event):
        if not getattr(self, "_pane_drag", None):
            self._set_sash_color(COLOR_BORDER)

    def _on_sash_press(self, event):
        if self._left_pane_w is None or self._minimal_mode:
            return
        self._pane_drag = (event.x_root, self._left_pane_w)
        self._set_sash_color(COLOR_CARD_HOVER)
        self.sash.bind_all("<B1-Motion>", self._on_sash_drag)
        self.sash.bind_all("<ButtonRelease-1>", self._on_sash_release)

    def _on_sash_drag(self, event):
        drag = getattr(self, "_pane_drag", None)
        if not drag:
            return
        x_root0, w0 = drag
        body_w = self.body.winfo_width()
        # 拖动范围随 DPI 缩放（原 240/260/520 逻辑像素硬编码）：右侧详情区
        # 保留最低可用宽度
        lo, hi = self._s(240), max(self._s(260), body_w - self._s(520))
        target = max(lo, min(hi, int(round(w0 + (event.x_root - x_root0)))))
        if target != self._left_pane_w:
            self._apply_pane_width(target)  # 右栏(weight=1)自动吃剩余宽度并动态重排

    def _on_sash_release(self, event):
        self._pane_custom = True
        self._pane_drag = None
        self._set_sash_color(COLOR_BORDER)
        try:
            self.sash.unbind_all("<B1-Motion>")
            self.sash.unbind_all("<ButtonRelease-1>")
        except tk.TclError:
            pass

    def _on_body_configure(self, event):
        """body 首次布局/窗口缩放时同步左栏像素宽：默认按 30% 比例，用户拖过则保持不变。"""
        if self._minimal_mode:
            # 极简模式：右栏/分隔条已移除，左栏经 col0 minsize 吃满 body 内部宽
            # （随窗口缩放联动；不改权重，见 _minimal_finish_enter 的说明）
            if self._minimal_anim_after is None:
                self._apply_pane_width(self._body_interior_w() - self._LEFT_PADX)
            return
        if self._pane_custom:
            return
        try:
            target = self._default_pane_width(event.width)
        except Exception:
            return
        if self._left_pane_w is None or abs(self._left_pane_w - target) >= 6:
            self._apply_pane_width(target)

    def _default_pane_width(self, body_w: int) -> int:
        """左栏默认宽度：约 body 可视宽 40%——候选列表是核心阅读区，需保证
        料号列在默认窗口宽度下完整可读（40% 时 avail ≈ 364px ≥ 料号完整宽
        128 + 其余列硬底线）；且保证右侧详情区不小于 480（逻辑）px。"""
        content = max(self._s(400), body_w - self._s(30))
        left = max(self._s(250), int(content * 0.40))
        return min(left, max(self._s(250), content - self._s(480)))

    def _apply_pane_width(self, px: int):
        """设置左栏像素宽：同步 grid 列 minsize 强制重排。

        仅 left.configure(width=) 修改 ttk Frame 宽度选项不会主动触发 grid 重排，
        因此必须以 columnconfigure 通知 grid 管理器。
        注意：grid 会把列 cell 的 padx（左栏右缘 7px）计入整列宽度，所以 minsize
        要加回 _LEFT_PADX，widget 实际宽度才等于 px。

        复钉机制：紧跟几何事件（body Configure / grid_remove 右栏）发出的
        minsize 变更，Tk 可能在同一轮布局计算中吞掉（左栏卡在旧宽度，实测
        复现）。因此调度一次 after_idle 复钉——读当前 _left_pane_w 重设同一
        minsize，下一轮布局计算稳定生效。幂等且防抖（拖栏高频调用无副作用）。
        """
        self._left_pane_w = px
        self.left.configure(width=px)
        try:
            self.body.columnconfigure(0, minsize=px + self._LEFT_PADX)
        except tk.TclError:
            pass
        if self._pane_repin_after is None:
            try:
                self._pane_repin_after = self.after_idle(self._pane_repin)
            except tk.TclError:
                pass

    def _pane_repin(self):
        """after_idle 复钉：用最新 _left_pane_w 重设 col0 minsize（见 _apply_pane_width）。"""
        self._pane_repin_after = None
        px = self._left_pane_w
        if px is None:
            return
        try:
            self.body.columnconfigure(0, minsize=px + self._LEFT_PADX)
        except tk.TclError:
            pass

    # ---------------- 窗口尺寸记忆 ----------------

    DEFAULT_WINDOW_SIZE = (1040, 720)  # 默认窗口尺寸（从未手动调整过时使用）

    def _enable_win_size_tracking(self):
        """启动布局稳定后开启尺寸跟踪；当前尺寸/位置视为基准（不触发保存）。"""
        self._win_size_ready = True
        self._last_saved_win_size = (self.winfo_width(), self.winfo_height())
        self._last_saved_win_pos = "+%d+%d" % (self.winfo_rootx(), self.winfo_rooty())
        # 布局已稳定 → 按真实窗口宽重算一次状态栏三态文案（#5/#8：
        # __init__ 里的首刷发生在布局前，文案态判定还没法基于真实宽度）
        self._refresh_status()

    def _initial_window_size(self) -> Tuple[int, int]:
        """启动窗口尺寸（物理像素）：优先用户上次手动调整并保存的尺寸。

        配置里 window_size 存的是「逻辑像素」（96 DPI 基准），按当前 DPI 因子
        换算为物理像素——跨缩放比例（如换 125%/100% 显示器）恢复的视觉大小
        一致。收敛规则：不小于 minsize，不超过当前屏幕大小（防止换显示器/
        改分辨率后窗口超出屏幕）。
        """
        w, h = self.DEFAULT_WINDOW_SIZE
        saved = self.settings.window_size
        if saved:
            try:
                sw, sh = str(saved).lower().split("x", 1)
                w, h = int(sw), int(sh)
            except (ValueError, AttributeError):
                pass  # 非法格式回落默认（validate 一般已拦截）
        w, h = self._s(w), self._s(h)
        try:
            screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        except Exception:
            screen_w, screen_h = 3840, 2160
        min_w, min_h = self.minsize()
        w = max(min_w, min(w, screen_w))
        # 高度预留标题栏/任务栏余量（逻辑 60px）：客户区顶满屏幕时，WM 会先压
        # 窗口、Tk 再强制回 minsize，请求值静默失效（实测 864 高请求落在 850）
        h = max(min_h, min(h, screen_h - self._s(60)))
        return w, h

    def _saved_window_pos_suffix(self) -> str:
        """恢复上次关闭时的窗口位置（"+x+y"，#20），按屏幕边界收敛防丢失。

        收敛规则：x/y 不超过「屏幕尺寸 - 200px」——标题栏至少 200px 露在
        屏内，换小屏/拔掉显示器后窗口仍可被拖回（评审 #20 的防丢诉求）。
        从未记录或解析失败 → 返回空串（用系统默认位置）。
        """
        pos = getattr(self.settings, "window_pos", None)
        if not pos:
            return ""
        try:
            xs, ys = str(pos).lstrip("+").split("+", 1)
            x, y = int(xs), int(ys)
        except (ValueError, AttributeError):
            return ""
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        except Exception:
            return ""
        x = max(0, min(x, sw - 200))
        y = max(0, min(y, sh - 200))
        return "+%d+%d" % (x, y)

    def _zoom_factor(self) -> float:
        """用户界面缩放偏好（#16）：乘在 DPI 因子上，0.8~1.6，异常回 1.0。"""
        try:
            z = float(getattr(self.settings, "ui_zoom", 1.0))
        except (TypeError, ValueError):
            return 1.0
        return z if 0.8 <= z <= 1.6 else 1.0

    def _set_ui_zoom(self, zoom: float):
        """保存界面缩放偏好（⋯菜单，#16）。

        布局常量（列宽/行高/分栏/窗口尺寸）在启动时已按当时因子实例化，
        热更需整体重建 → 写入配置后提示重启生效。
        """
        self.settings.ui_zoom = zoom
        try:
            self.settings.save()
        except Exception:
            pass  # 写失败不影响运行，重启后仍会回退默认
        self._toast(f"界面缩放已设为 {int(round(zoom * 100))}%，重启后生效")

    def _show_about(self):
        """关于对话框（#18）：版本与当前数据源概况。"""
        messagebox.showinfo(
            "关于 ChipLookup",
            f"ChipLookup v{APP_VERSION}\n"
            "芯片料号查询器（本地数据 / 网络数据源 / 混合检索）\n\n"
            f"当前模式：{MODE_LABELS.get(self.settings.mode, self.settings.mode)}"
            f" · 共 {self.source.count()} 条记录\n"
            f"数据源：{self.source.describe()}",
        )

    def _on_window_configure(self, event):
        """顶层尺寸变化 → 防抖 500ms 后持久化（只记用户手动调整）。

        注意：toplevel 的 bind(<Configure>) 会经 bindtags 收到所有子组件的
        Configure 事件，必须用 event.widget is self 过滤；启动就绪前的
        程序性变化一律忽略；拖拽缩放期间连续事件用防抖合并。
        """
        if event.widget is not self or not self._win_size_ready:
            return
        # 跨越状态栏文案阈值 → full/compact/hidden 三态切换（#5；翻转时才重绘）
        if self._status_mode() != self._status_mode_state:
            self._refresh_status()
        if self._win_size_save_after is not None:
            try:
                self.after_cancel(self._win_size_save_after)
            except Exception:
                pass
        self._win_size_save_after = self.after(500, self._save_window_size)

    def _save_window_size(self):
        """防抖落地：当前窗口尺寸与位置写入 chiplookup.json。

        最大化(zoomed)时不记录——保留最近一次正常尺寸，还原窗口不丢；
        尺寸与位置都与上次已保存值相同则跳过（去重，避免无谓写盘；
        位置单独变化——只拖不动窗——也要记录，故两者分别比较）。
        写入的是「逻辑像素」（物理尺寸 ÷ DPI 因子）：配置跨缩放比例稳定，
        96 DPI 环境下因子为 1.0，与旧格式完全兼容。

        位置取自 geometry()（WM 框架原点），与 _saved_window_pos_suffix 的
        geometry("+x+y") 恢复端同基准——用 winfo_rootx（客户区原点）会带入
        标题栏/边框偏移，存取一次位置就漂移（实测 +120+90 → +128+121）。
        """
        self._win_size_save_after = None
        try:
            if self.state() == "zoomed":
                return
            w, h = self.winfo_width(), self.winfo_height()
        except Exception:
            return
        if w < 300 or h < 300:
            return
        m = _re.search(r"^\d+x\d+([+-]\d+)([+-]\d+)", self.geometry())
        if m and int(m.group(1)) >= 0 and int(m.group(2)) >= 0:
            pos = "+%d+%d" % (int(m.group(1)), int(m.group(2)))
        else:
            # 解析失败或负坐标（副屏在主屏左侧）：不更新位置记忆
            pos = self._last_saved_win_pos
        if ((w, h) == self._last_saved_win_size
                and pos == self._last_saved_win_pos):
            return
        self._last_saved_win_size = (w, h)
        self._last_saved_win_pos = pos
        scale = self._ui_scale if self._ui_scale else 1.0
        self.settings.window_size = "%dx%d" % (
            int(round(w / scale)), int(round(h / scale)))
        if pos is not None:
            self.settings.window_pos = pos
        try:
            self.settings.save()
        except Exception:
            pass  # 写失败不影响运行

    # ---------------- 极简模式（隐藏右栏详情区，仅保留查询列表） ----------------

    def _body_interior_w(self) -> int:
        """body 内部可用宽（扣除 ttk padding (14, 0) 的左右 14+14，随 DPI 缩放）。"""
        return max(self._s(300), self.body.winfo_width() - self._s(28))

    def _toggle_minimal_mode(self):
        self._set_minimal_mode(not self._minimal_mode)

    def _set_minimal_mode(self, minimal: bool, animate: bool = True):
        """切换极简模式，并把偏好持久化到 chiplookup.json。

        进极简：右栏/分隔条保留在布局中，左栏 minsize 逐帧增大——grid 在空间
        不足时从 weight>0 的列（右栏）收缩，因此只改左栏 minsize 就能得到
        右栏被平滑挤压的过渡；收尾时移除右栏与分隔条，左栏列改为 weight=1
        继续吃掉窗口剩余宽（窗口缩放由 _on_body_configure 联动）。
        出极简：先恢复右栏/分隔条布局（右栏从 0 宽起步），左栏 minsize 逐帧
        减回原宽（用户没拖过分隔条则按窗口 30% 重新计算）。
        """
        minimal = bool(minimal)
        if minimal == self._minimal_mode and self._minimal_anim_after is None:
            return
        self._cancel_minimal_anim()
        self._minimal_mode = minimal
        self.btn_minimal.configure(text="退出极简" if minimal else "极简模式")

        if minimal:
            if self._minimal_saved_w is None:
                body_w = max(self.body.winfo_width(), 600)
                self._minimal_saved_w = (
                    self._left_pane_w
                    if self._left_pane_w is not None
                    else self._default_pane_width(body_w)
                )
            if animate:
                # 动画终点：右栏恰好被压到 0 宽。扣除 = 左padx + 分隔条命中区宽
                # + 右padx3（#6 扩宽后分隔条 12px，不再写死 16=7+6+3）
                overhead = self._s(7) + self._s(12) + 3
                target = self._body_interior_w() - overhead
                self._anim_left_width(
                    self._left_pane_w or target, target,
                    on_done=self._minimal_finish_enter,
                )
            else:
                self._minimal_finish_enter()
        else:
            # 恢复右栏/分隔条布局（col0 minsize 仍占满 → 右栏从 0 宽起步，
            # 随后逐帧减 minsize 让右栏回弹；列权重全程不变，见 _minimal_finish_enter）
            self.right.grid()
            self.sash.grid()
            if self._pane_custom:
                target = self._minimal_saved_w or self._default_pane_width(
                    max(self.body.winfo_width(), 600)
                )
            else:
                target = self._default_pane_width(max(self.body.winfo_width(), 600))
            if animate:
                self._anim_left_width(self._left_pane_w or target, target)
            else:
                self._apply_pane_width(target)

        # 偏好持久化（下次启动自动恢复）
        self.settings.minimal_mode = minimal
        try:
            self.settings.save()
        except Exception:
            pass  # 配置写失败不影响本次切换

    def _minimal_finish_enter(self):
        """进极简收尾：左栏经 col0 minsize 吃满窗口内部宽，再移除右栏/分隔条。

        顺序至关重要（实测锁定）：必须先应用目标宽、再 grid_remove。同一轮
        回调里「先移除、后改 minsize」会被 Tk 合并成一次布局计算，左栏会卡
        在进极简前的旧宽度；而「先加宽（此时右栏还在布局中，被 weight=1 的
        col2 挤到 0 宽）再移除」宽度稳定生效。全程不改列权重（col0 恒
        weight=0 / col2 恒 weight=1）：实测 grid 对 weight=1 列的 minsize
        变化不触发重新分配，weight=0 列的 minsize 变化始终可靠（完整模式
        拖栏即依赖此机制）。末尾 after_idle 复钉一次，防跨轮合并丢失。
        """
        # widget 宽 = 内部宽 - 自身 padx(7)，cell 恰好占满、不依赖边缘裁剪。
        # 移除右栏后可能被吞掉的这次 minsize 变更，由 _apply_pane_width 的
        # after_idle 复钉机制兜底，无需在此额外补一次。
        self._apply_pane_width(self._body_interior_w() - self._LEFT_PADX)
        self.right.grid_remove()
        self.sash.grid_remove()

    def _cancel_minimal_anim(self):
        if self._minimal_anim_after is not None:
            try:
                self.after_cancel(self._minimal_anim_after)
            except Exception:
                pass
            self._minimal_anim_after = None

    def _anim_left_width(self, w_from: int, w_to: int, on_done=None):
        """~180ms ease-out 逐帧过渡左栏宽（右栏 weight=1 自动跟随伸缩）。"""
        steps = 10
        interval = max(16, 180 // steps)

        def frame(i: int):
            self._minimal_anim_after = None
            t = i / steps
            eased = 1.0 - (1.0 - t) ** 2  # 二次 ease-out：先快后慢
            self._apply_pane_width(int(round(w_from + (w_to - w_from) * eased)))
            if i < steps:
                self._minimal_anim_after = self.after(interval, lambda: frame(i + 1))
            elif on_done:
                on_done()

        frame(0)

    def _refresh_value_wrap(self):
        """详情字段值随详情画布宽度自适应换行（长文本不再被静默裁切）。"""
        canvas = self.detail_canvas
        try:
            cw = canvas.winfo_width()
        except Exception:
            return
        if cw <= 0:
            return
        for val, lab in self._detail_value_labels:
            # Text控件根据宽度自动换行，无需额外设置
            # 保留方法以兼容可能的Label控件
            if not isinstance(val, tk.Text):
                try:
                    lw = lab.winfo_reqwidth()
                except Exception:
                    lw = 158
                val.configure(wraplength=max(120, cw - 40 - lw - 12))

    def _refresh_pager(self):
        """刷新加载更多模式下的 pager 控件。"""
        for w in self.pager_frame.winfo_children():
            w.destroy()
        total = len(self._candidates)
        count = min(self._visible_count, total)
        if count < total:
            load_more_btn = ttk.Button(
                self.pager_frame,
                text=f"加载更多（还剩 {total - count} 条）",
                style="ModeSel.TButton",
                command=self._load_more,
            )
            ToolTip(load_more_btn, "在当前列表末尾追加一页候选")
            load_more_btn.pack(side=tk.LEFT)
        else:
            ttk.Label(self.pager_frame, text="已全部加载",
                      style="Hint.TLabel").pack(side=tk.LEFT)
        ttk.Label(self.pager_frame, text=f"已显示 {count} / {total} 条",
                  style="Hint.TLabel").pack(side=tk.LEFT, padx=(8, 0))

    def _select_first(self):
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])

    def _refresh_mode_buttons(self):
        """分页策略分段按钮已随 #10 移除（固定「加载更多」模式）。

        保留空实现兼容既有调用点（__init__ / _render_candidates）；
        分页渲染路径将来若配置化复用，再恢复按钮创建与高亮逻辑。
        """
        return

    def _set_mode(self, mode: str):
        if self.page_mode.get() == mode:
            return
        self.page_mode.set(mode)
        self._current_page = 0
        self._visible_count = self.PAGE_SIZE
        if self._candidates:
            self._render_candidates()
        else:
            self._refresh_mode_buttons()

    def _prev_page(self):
        if self._current_page > 0:
            self._current_page -= 1
            self._render_candidates()

    def _next_page(self):
        total = len(self._candidates)
        if total <= self.PAGE_SIZE:
            return
        total_pages = (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        if self._current_page < total_pages - 1:
            self._current_page += 1
            self._render_candidates()

    def _load_more(self, keep_focus: bool = False):
        new_count = min(self._visible_count + self.PAGE_SIZE, len(self._candidates))
        old_count = self._visible_count
        self._visible_count = new_count
        # 增量插入新行（不重置已加载的，保留滚动位置）
        if new_count > old_count:
            self._insert_rows(old_count, new_count)
        self._refresh_pager()
        if keep_focus:
            # 自动追加（#10）时不回选首行——用户正在浏览，焦点跳回开头会打断
            return
        self._select_first()

    def _maybe_autoload_more(self, row: str):
        """加载更多模式的自动追加（#10）：指针/焦点到达已加载末行且还有剩余。"""
        if self.page_mode.get() != "load_more":
            return
        if self._visible_count >= len(self._candidates):
            return
        try:
            rows = self.tree.get_children()
        except tk.TclError:
            return
        if rows and row == rows[-1]:
            self._load_more(keep_focus=True)

    def _run_query(self):
        q = self.var_query.get().strip()
        # 清空候选列表（树+pager）
        for r in self.tree.get_children():
            self.tree.delete(r)
        for w in self.pager_frame.winfo_children():
            w.destroy()
        self._selected_index = -1
        self._current_page = 0
        self._visible_count = self.PAGE_SIZE

        if not q:
            self._render_detail_empty()
            return

        # 先尝试精确匹配
        exacts = lookup_exact(q, self._records_cache)
        if exacts:
            results = [(r, 1001.0, "part_number 精确匹配") for r in exacts]
            # 精确命中后不再对 4 万条做全量模糊扫描，只用廉价匹配（前缀/包含）
            # 快速把相近料号垫在精确结果下方，体感几乎无延迟
            seen = {id(r) for r, _, _ in results}
            partials = cheap_partials(q, self._records_cache, top_n=20, exclude_ids=seen)
            results.extend(partials)
            self._candidates = results[:20]
        else:
            self._candidates = search(q, self._records_cache, top_n=20)

        if not self._candidates:
            sug = suggest_terms(q, self._records_cache, max_n=5)
            if sug:
                self._render_detail_not_found(q, sug)
            else:
                self._render_detail_not_found(q, [])
            return

        # 填充候选表 + 分页/加载更多控件
        self._render_candidates()
        # 渲染首条详情（_render_candidates 已选第一行）
        first_iid = self.tree.get_children()
        if first_iid:
            self._selected_index = int(first_iid[0])
            self._render_detail(self._candidates[int(first_iid[0])][0])

    # ---------------- 详情渲染 ----------------

    def _clear_detail(self):
        self._detail_value_labels = []
        for w in self.detail_inner.winfo_children():
            w.destroy()

    def _render_detail_empty(self):
        self._clear_detail()
        f = ttk.Frame(self.detail_inner, style="TFrame", padding=(20, 30))
        f.pack(fill=tk.X, expand=True)
        ttk.Label(f, text="在上方输入料号开始查询。", style="TLabel",
                  font=("Microsoft YaHei UI", 12)).pack(pady=(40, 4))
        ttk.Label(f, text="提示：支持料号、型号、厂商、容量；空格不区分大小写。", style="TLabel",
                  font=("Microsoft YaHei UI", 9), foreground=COLOR_TEXT_DIM).pack()
        # 空状态给「可点的第一步」（#15）：放几个库里现成的示例料号，
        # 点击填入查询框（var_query trace 自动触发防抖查询），零学习成本
        samples = []
        for rec in self._records_cache[:3]:
            pn = (rec.get("part_number", "") or "").strip()
            if pn:
                samples.append(pn)
        if samples:
            row = ttk.Frame(f, style="TFrame")
            row.pack(pady=(16, 0))
            ttk.Label(row, text="试试：", style="TLabel",
                      foreground=COLOR_TEXT_DIM).pack(side=tk.LEFT, padx=(0, 6))
            for pn in samples:
                btn = ttk.Button(row, text=pn, style="ModeSel.TButton",
                                 command=lambda p=pn: self._fill_query(p))
                btn.pack(side=tk.LEFT, padx=(0, 6))
                ToolTip(btn, "点击填入查询框并搜索")

    def _render_detail_not_found(self, query: str, suggests: List[str]):
        self._clear_detail()
        wrap = ttk.Frame(self.detail_inner, style="TFrame", padding=(16, 12))
        wrap.pack(fill=tk.X, expand=True)

        head = ttk.Frame(wrap, style="Card.TFrame", padding=(16, 16))
        head.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(head, text=f"未找到与「{query}」匹配的记录",
                  style="Big.TLabel", foreground=COLOR_WARN).pack(anchor="w")
        ttk.Label(
            head,
            text="可能原因：① 拼写错误 ② 数据库里尚未收录此料号 ③ 大小写/符号差异",
            style="Card.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        if suggests:
            sug = ttk.Frame(wrap, style="Card.TFrame", padding=(16, 16))
            sug.pack(fill=tk.X)
            ttk.Label(sug, text="相近的料号候选（双击可填回输入框）：",
                      style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
            for s in suggests:
                btn = ttk.Button(sug, text=s, style="Ghost.TButton")
                btn.configure(command=lambda s=s: self._fill_query(s))
                btn.pack(anchor="w", pady=2)

        advice = ttk.Frame(wrap, style="Card.TFrame", padding=(16, 16))
        advice.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(advice, text="操作建议",
                  style="Card.TLabel", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        ttk.Label(advice, text="• 试着只输入前几位或后几位字母\n"
                  "• 核对料号来源文档\n"
                  "• 用 「导入 xlsx」 把新批次料号纳入数据库\n"
                  "• 命令行直接维护：python tools/import_export.py add <料号> <型号>",
                  style="Card.TLabel",
                  justify="left").pack(anchor="w")

    def _render_detail(self, record: dict):
        self._clear_detail()
        # 主信息卡
        head = ttk.Frame(self.detail_inner, style="Card.TFrame", padding=CARD_PADDING)
        head.pack(fill=tk.X, pady=(0, 10))
        # 料号（使用 Text 控件支持复制）
        pn_text = tk.Text(
            head, height=1, wrap="word",
            font=(FONT_FAMILY_UI, FONT_SIZE_XXL, "bold"),
            bg=COLOR_CARD, fg=COLOR_TEXT,
            relief="flat", bd=0, highlightthickness=0,
            padx=0, pady=0, spacing1=0, spacing3=0,
        )
        pn_text.insert("1.0", record.get("part_number", "") or "(无料号)")
        pn_text.configure(state="disabled")
        pn_text.pack(anchor="w")
        # 型号（使用 Text 控件支持复制）
        model_val = record.get("model", "") or ""
        if model_val:
            model_text = tk.Text(
                head, height=1, wrap="word",
                font=(FONT_FAMILY_MONO, FONT_SIZE_MD),
                bg=COLOR_CARD, fg=COLOR_TEXT,
                relief="flat", bd=0, highlightthickness=0,
                padx=0, pady=0, spacing1=0, spacing3=0,
            )
            model_text.insert("1.0", model_val)
            model_text.configure(state="disabled")
            model_text.pack(anchor="w", pady=(2, 0))
        # 标签行（使用 Text 控件支持复制）
        tag_text = " ".join(
            f"[{v}]" for v in [
                record.get("manufacturer", ""),
                record.get("type", ""),
                _capacity_display(record.get("capacity", ""), record.get("bit_width", "")),
            ] if v
        )
        if tag_text:
            tag_widget = tk.Text(
                head, height=1, wrap="word",
                font=(FONT_FAMILY_UI, FONT_SIZE_SM),
                bg=COLOR_CARD, fg=COLOR_TEXT_DIM,
                relief="flat", bd=0, highlightthickness=0,
                padx=0, pady=0, spacing1=0, spacing3=0,
            )
            tag_widget.insert("1.0", tag_text)
            tag_widget.configure(state="disabled")
            tag_widget.pack(anchor="w", pady=(6, 0))

        # 分组卡片（#17：字段标签主中文，英文全称悬停可见——双语并排每行
        # 占 ~80px 宽度且对中文用户是噪音，字段值获得更多显示空间）
        groups = [
            ("基本", [
                ("厂商", "Manufacturer", record.get("manufacturer", "")),
                ("类型", "Type", record.get("type", "")),
                ("型号", "Model", record.get("model", "")),
            ]),
            ("存储参数", [
                ("容量", "Capacity",
                 _capacity_display(record.get("capacity", ""), record.get("bit_width", ""))),
                ("GB值", "GB Value",
                 _capacity_gb(record.get("capacity", ""))),
                ("位宽", "Bit Width", record.get("bit_width", "")),
                ("速度", "Speed", record.get("speed", "")),
                ("电压", "Voltage", record.get("voltage", "")),
            ]),
            ("物理信息", [
                ("封装", "Package", record.get("package", "")),
                ("尺寸", "Dimensions", record.get("dimensions", "")),
                ("Die 数", "Die Count", record.get("die_count", "")),
                ("CS 数", "CS Count", record.get("cs_count", "")),
                ("Die 版本", "Die Revision", record.get("die_revision", "")),
            ]),
            ("使用条件", [
                ("工作温度", "Op Temp", record.get("op_temp", "")),
                ("备注", "Notes", record.get("notes", "")),
            ]),
        ]
        for title, rows in groups:
            card = ttk.Frame(self.detail_inner, style="Card.TFrame", padding=(20, 16))
            card.pack(fill=tk.X, pady=(0, 10))
            ttk.Label(card, text=title,
                      style="Card.TLabel",
                      font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 10))
            for label, en, value in rows:
                display = str(value) if value not in ("", None) else "\u2014"  # 空值显示占位符「—」
                is_empty = value in ("", None)
                row = ttk.Frame(card, style="Card.TFrame")
                row.pack(fill=tk.X, pady=2)
                lab = ttk.Label(row, text=label, style="Field.TLabel", width=10, anchor="w")
                lab.pack(side=tk.LEFT)
                ToolTip(lab, en)  # 英文全称悬停可见（#17）
                val = tk.Text(
                    row, height=1, wrap="word",
                    font=("Microsoft YaHei UI", 11, "bold") if not is_empty else ("Microsoft YaHei UI", 11),
                    bg=COLOR_CARD, fg=COLOR_TEXT if not is_empty else COLOR_TEXT_DIM,
                    relief="flat", bd=0, highlightthickness=0,
                    padx=0, pady=0, spacing1=0, spacing3=0,
                )
                val.insert("1.0", display)
                val.configure(state="disabled")
                val.pack(side=tk.LEFT, fill=tk.X, expand=True)
                # 记录字段值，随窗口缩放自适应换行（详情区宽度变化时统一刷新）
                self._detail_value_labels.append((val, lab))
        self._refresh_value_wrap()

    # ---------------- 操作栏 ----------------

    def _fill_query(self, s: str):
        self.var_query.set(s)
        self.entry.focus_set()
        self.entry.icursor(tk.END)

    def _clear_query(self):
        self.var_query.set("")
        self._render_detail_empty()

    def _copy_part_no(self):
        rec = self._current_record()
        if not rec:
            self._toast("当前无内容可复制")
            return
        self._copy_to_clipboard(rec.get("part_number", ""))
        self._toast("已复制料号：" + (rec.get("part_number", "") or ""))

    def _copy_current(self, fn, msg_on_ok="已复制"):
        rec = self._current_record()
        if not rec:
            self._toast("当前无内容可复制")
            return
        text = fn(rec)
        if text:
            self._copy_to_clipboard(text)
            self._toast(msg_on_ok)

    def _current_record(self) -> Optional[dict]:
        if not self._candidates:
            return None
        if 0 <= self._selected_index < len(self._candidates):
            return self._candidates[self._selected_index][0]
        return None

    def _copy_to_clipboard(self, text: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        # Tk 在某些平台需要 update 才能落到剪贴板
        self.update()

    def _toast(self, msg: str, error: bool = False):
        """状态栏 toast：✓/✗ 文案停留后恢复常规状态。

        恢复不走弹出时的文本快照——旧实现会把 toast 期间发生的正常状态
        更新覆盖回旧值，且连续 toast 各自排期、相互闪断。改为到期直接调
        _refresh_status() 重算当前真实状态；连续 toast 只保留最后一个到期
        回调（防抖）。同时承担极简模式的操作反馈（#11）与非阻断错误提示
        （#14：error=True 红字、停留 3.8s，替代数据源切换失败的模态框）。
        """
        if self._toast_after is not None:
            try:
                self.after_cancel(self._toast_after)
            except Exception:
                pass
        self.status_label.configure(
            text=f"{'✗' if error else '✓'} {msg}",
            foreground=COLOR_DANGER if error else COLOR_TEXT_DIM,
        )
        self._toast_after = self.after(3800 if error else 1800, self._toast_reset)

    def _toast_reset(self):
        """toast 到期：恢复常规前景色并按当前真实状态重算文案。"""
        self._toast_after = None
        self.status_label.configure(foreground=COLOR_TEXT_DIM)
        self._refresh_status()

    # ---------------- 数据导入导出 ----------------

    def _import_csv(self):
        if not self.source.supports_import():
            self._toast("当前模式（%s）不支持导入" % self.source.LABEL)
            return
        path = filedialog.askopenfilename(
            title="导入数据（合并到现有库）",
            filetypes=[("Excel 文件", "*.xlsx"), ("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            n = self.source.import_csv(path, replace=False)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        # 数据已变化：所有读取该文件的模式缓存全部失效（import_csv 已就地
        # reload 当前 source，把它重新放回缓存即可；其余模式下次切换再重建）
        self._source_cache.clear()
        self._source_cache[self.settings.mode] = self.source
        # 重新加载缓存
        self._records_cache = self.source.list_records()
        self._refresh_status()
        # 明确反馈：弹窗告知导入结果
        basename = os.path.basename(path)
        if n > 0:
            messagebox.showinfo(
                "导入成功",
                "文件：%s\n导入 %d 条记录（新增 + 更新）" % (basename, n),
            )
        else:
            messagebox.showinfo(
                "导入完成",
                "文件：%s\n所有记录已存在，无新增或更新" % basename,
            )
        if self.var_query.get().strip():
            self._run_query()
        if self.on_change:
            self.on_change()

    def _export_csv(self):
        if not self.source.supports_export():
            self._toast("当前模式（%s）不支持导出" % self.source.LABEL)
            return
        path = filedialog.asksaveasfilename(
            title="导出数据",
            defaultextension=".xlsx",
            initialfile="chip_database_export.xlsx",
            filetypes=[("Excel 文件", "*.xlsx"), ("CSV 文件", "*.csv")],
        )
        if not path:
            return
        try:
            n = self.source.export_csv(path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self._toast(f"已导出 {n} 条 -> {os.path.basename(path)}")

    def _reload_db(self):
        """F5：强制重建当前模式数据源（后台线程执行，UI 不冻结）。"""
        if self._switch_busy:
            self._toast("正在切换/刷新数据源，请稍候…")
            return
        # 丢弃当前模式缓存，确保真正重读磁盘/索引
        self._source_cache.pop(self.settings.mode, None)
        self._request_source_mode(self.settings.mode, force=True)

    # ---------------- 上游数据同步 ----------------
    # 头部「同步」按钮：从 iTXTech/fdnext 拉取最新标记码索引，按「只填空」规则
    # 合入本地 CSV（人工数据优先级最高，原值永不被覆盖）。脚本本身只依赖
    # 标准库（urllib/json/csv），不依赖 Node.js。
    #
    # 实现要点：
    #   1) tools/sync_upstream.py 没 __init__.py，直接用 importlib.util 按文件
    #      路径加载；路径经 bundle_root() 解析，开发模式与 PyInstaller 打包后
    #      都能找到（spec 把 tools/ 整目录打进 _MEIPASS/tools/）。
    #   2) 同步必须放后台线程（脚本涉及网络 I/O + 全表遍历），UI 不能冻结；
    #      期间复用 _switch_busy 当忙碌门 + 把 radios 禁用，与切换模式共用
    #      同一套互斥/视觉提示。
    #   3) run() 内部 print 打到 stdout → 在后台线程里把 sys.stdout 重定向
    #      到 StringIO，run() 结束后从 StringIO 提取关键统计行（耗时/合并
    #      条数/审计提示）做 toast 反馈。
    #   4) 同步成功后：local/hybrid 模式立刻强制重建数据源刷新列表；upstream
    #      模式只 toast 提示「本地 CSV 已同步，需切到本地/混合才看得到」，
    #      避免误导（当前模式的 records 是从网络拉来的，跟本地 CSV 无关）。

    def _sync_script_path(self) -> Optional[str]:
        """定位 tools/sync_upstream.py：开发模式 → 项目根/tools/；
        PyInstaller 打包后 → _MEIPASS/tools/。"""
        path = os.path.join(bundle_root(), "tools", "sync_upstream.py")
        return path if os.path.isfile(path) else None

    def _on_sync_clicked(self):
        """同步按钮点击：忙碌门控 + 启动后台 worker。"""
        if self._switch_busy:
            self._toast("正在执行其他操作，请稍候…")
            return
        script = self._sync_script_path()
        if not script:
            self._toast("找不到 sync_upstream.py，无法同步", error=True)
            return
        # 锁定 UI：复用 _switch_busy 标志（与切换模式共用同一套互斥/提示）
        self._switch_busy = True
        self._set_busy_ui("同步中…", busy=True)
        self._switch_started = time.monotonic()
        self._start_sync_worker(script)

    def _start_sync_worker(self, script_path: str):
        """后台线程跑 tools/sync_upstream.run(args)，结果入 _switch_queue
        复用同一条轮询通道（避免再起一套 after/线程）。"""
        # sync_upstream.run 期望 args.db 为本地 CSV 路径；脚本默认就是
        # default_database_path()，这里显式传以便日志与状态栏文案一致。
        csv_path = default_database_path()
        # 同步任务用独立 token 计数（与切换数据源任务隔离）
        self._sync_token += 1
        token = self._sync_token

        def _run():
            try:
                # 用 importlib 按文件路径加载 tools/sync_upstream.py（该
                # 目录没 __init__.py，普通 import 不可用）
                spec = importlib.util.spec_from_file_location(
                    "_sync_upstream_runtime", script_path
                )
                if spec is None or spec.loader is None:
                    raise RuntimeError("无法加载同步脚本: %s" % script_path)
                mod = importlib.util.module_from_spec(spec)
                # 把脚本所在目录加入 sys.path，让脚本顶部的「找到 src 包」
                # 逻辑（sys.path.insert ROOT）能正常完成
                script_dir = os.path.dirname(script_path)
                project_root = os.path.dirname(script_dir)
                path_inserted = False
                if project_root and project_root not in sys.path:
                    sys.path.insert(0, project_root)
                    path_inserted = True
                try:
                    spec.loader.exec_module(mod)
                    args = argparse.Namespace(
                        db=csv_path,
                        cache_dir=None,
                        offline=False,
                        refresh=False,
                        max_age=mod.DEFAULT_MAX_AGE_SECONDS,
                        timeout=mod.DEFAULT_TIMEOUT_SECONDS,
                        dry_run=False,
                        verbose=False,
                        export_index=None,
                        show_limit=20,
                    )
                    # 重定向 stdout → StringIO，捕获 print 输出
                    buf = io.StringIO()
                    real_stdout = sys.stdout
                    sys.stdout = buf
                    try:
                        rc = mod.run(args)
                    finally:
                        sys.stdout = real_stdout
                    log = buf.getvalue()
                finally:
                    if path_inserted:
                        try:
                            sys.path.remove(project_root)
                        except ValueError:
                            pass
                self._switch_queue.put(
                    (token, "sync_ok", csv_path, rc, log)
                )
            except Exception as exc:
                self._switch_queue.put(
                    (token, "sync_err", csv_path, exc, "")
                )

        threading.Thread(
            target=_run, daemon=True, name="chiplookup-sync-upstream"
        ).start()
        self._ensure_polling()

    def _handle_sync_result(self, token: int, csv_path: str,
                            payload, log: str):
        """同步成功回调：toast 反馈 + 必要的数据源刷新。

        UI 收尾策略按当前模式分两支：
            - local / hybrid：调 _request_source_mode(force=True) 重建数据
              源，最后由 _apply_switch_result 一并把按钮 / radio / busy
              复位（统一收尾，避免分散在两条路径里漏掉）。
            - upstream / 失败：本地 CSV 跟当前网络视图无关 或 同步失败，
              直接 _finalize_sync_only() 主动复位 UI。
        """
        # 解包：rc == 0 视为成功，非 0 视为失败（脚本约定）
        rc, log_text = payload if isinstance(payload, tuple) else (payload, log)
        elapsed = self._switch_elapsed()
        stats = _parse_sync_log(log_text)
        if rc == 0:
            # 构造 toast 文案：耗时 + 关键统计；网络降级用旧缓存时也提示
            parts = []
            if stats["filled_model"] is not None:
                parts.append("补 model %d 条" % stats["filled_model"])
            if stats["filled_manufacturer"] is not None:
                parts.append("manufacturer %d 条" % stats["filled_manufacturer"])
            if stats["changed"] is not None:
                parts.append("已合并 %d 条" % stats["changed"])
            elif stats.get("stale_cache"):
                parts.append("网络失败已降级用旧缓存")
            detail = " · ".join(parts) if parts else "无新数据可补"
            self._toast("同步完成 · %s · %.1fs" % (detail, elapsed))
            # local / hybrid 模式：本地 CSV 已变化，强制重建数据源刷新列表
            if self.settings.mode in ("local", "hybrid"):
                self._source_cache.pop(self.settings.mode, None)
                # 先释放同步阶段的忙碌锁，否则 _pump_switch 会因
                # _switch_busy=True 直接跳过，数据源重建永远不启动
                self._switch_busy = False
                self._request_source_mode(self.settings.mode, force=True)
                # _request_source_mode → _pump_switch 会重新加锁并
                # 在 _apply_switch_result 收尾时恢复按钮/radio
                return
            # upstream 模式：本地 CSV 变化跟当前网络视图无关，仅提示用户
            self._toast("当前是「网络」模式，切回「本地CSV」或「混合」可看到新数据")
        else:
            self._toast("同步失败：返回码 %d · %.1fs" % (rc, elapsed), error=True)
        # upstream 模式 / 非 0 返回码：没有触发数据源重建，主动复位 UI
        self._finalize_sync_only()

    def _handle_sync_error(self, token: int, csv_path: str, exc: Exception):
        """同步失败回调：错误 toast，恢复 UI 状态。"""
        elapsed = self._switch_elapsed()
        self._toast("同步失败：%s · %.1fs" % (exc, elapsed), error=True)
        # 没有触发数据源重建，主动复位 UI
        self._finalize_sync_only()

    # ---------------- 同步结束后 UI 收尾（在 _apply_switch_result 风格的
    # 数据源切换收尾里复用，本方法单独处理「只跑同步、不重建数据源」
    # 的分支，如 upstream 模式同步、或同步失败场景） ----------------

    def _finalize_sync_only(self):
        """同步跑完但不需要重建数据源时（upstream 模式 / 失败），
        主动清掉忙碌门 + 恢复按钮/radio。"""
        self._switch_busy = False
        self._set_busy_ui("同步", busy=False)
        # 切换队列里还有 latest-wins 目标？接力跑
        self._pump_switch()

    def _set_busy_ui(self, sync_label: str, busy: bool):
        """统一处理「后台任务期间」的 UI 状态：同步按钮 + radio。

        busy=True  → 同步按钮 disabled 并显示给定文案，radio 禁用；
        busy=False → 全部恢复。

        数据源切换时由 _pump_switch 单独管 radio 启停（#12），这里只动
        同步按钮；切换收尾时也会调本方法，把同步按钮一并复位，避免
        「同步成功后切模式重置数据源」这种联动场景把按钮卡在禁用态。
        """
        try:
            self.btn_sync.state(["disabled"] if busy else ["!disabled"])
        except tk.TclError:
            pass
        self.btn_sync.configure(text=sync_label)
        self._set_radios_enabled(not busy)


def _parse_sync_log(log: str) -> dict:
    """从 sync_upstream.run() 的 print 输出里提取关键统计。

    脚本约定的可识别行（任何一项缺失都返回 None，按钮反馈照样能渲染）：
        [sync] 本地记录 N 条；本次可补: model X 条 / manufacturer Y 条
        [sync] 已合并 N 条并写回 <path>
        [sync] 无可填空字段，未写盘（保持不变）。
        ...（含 "下载失败...降级" 时表示 stale_cache）
    """
    import re as _re

    out: dict = {
        "filled_model": None,
        "filled_manufacturer": None,
        "changed": None,
        "stale_cache": False,
    }
    m = _re.search(r"本次可补:\s*model\s+(\d+)\s*条\s*/\s*manufacturer\s+(\d+)\s*条", log)
    if m:
        out["filled_model"] = int(m.group(1))
        out["filled_manufacturer"] = int(m.group(2))
    m = _re.search(r"已合并\s+(\d+)\s*条并写回", log)
    if m:
        out["changed"] = int(m.group(1))
    if "下载失败" in log and "降级" in log:
        out["stale_cache"] = True
    return out


def run(settings: Settings, source: RecordSource,
        screenshot_to: Optional[str] = None, screenshot_query: Optional[str] = None,
        screenshot_delay_ms: int = 0, screenshot_mode: Optional[str] = None):
    """供 main.py 调用的启动入口。

    settings / source：运行配置与已构建好的数据源（由 main 负责按模式构建）。
    screenshot_to / screenshot_query 非空时，启动 UI、跑一次查询、再截图保存到该路径后退出。
    screenshot_mode: "paginate" 或 "load_more"，None 则保持默认
    """
    app = App(settings, source)
    if screenshot_mode:
        try:
            app.page_mode.set(screenshot_mode)
        except Exception:
            pass
    if screenshot_to:
        # 截图模式：固定大小放左上角，确保 pager 在可见区域内
        try:
            app.geometry("1200x680+0+0")
            app.update_idletasks()
            app.update()
        except Exception:
            pass
        def _after():
            if screenshot_query:
                app.var_query.set(screenshot_query)
                app._run_query()
            try:
                # 让事件循环处理一次布局再截
                for _ in range(3):
                    app.update_idletasks()
                    app.update()
                # 强制重绘（让新增 widgets 真正 paint）
                try:
                    app.update_idletasks()
                    app.event_generate("<Expose>", when="now")
                    app.update()
                except Exception:
                    pass
                from PIL import ImageGrab  # type: ignore
                # 抬到最前 + 强制刷新位置
                app.lift()
                app.attributes("-topmost", True)
                app.update()
                app.attributes("-topmost", False)
                # 再来几轮确保绘制
                for _ in range(5):
                    app.update_idletasks()
                    app.update()
                # 注意 DPI 缩放：winfo_* 用逻辑坐标，PIL 用物理坐标
                x = app.winfo_rootx()
                y = app.winfo_rooty()
                w = app.winfo_width()
                h = app.winfo_height()
                # 估算 DPI 缩放：用 PIL 取全屏与 Tk 屏幕的比值
                _full = ImageGrab.grab()
                sw_log = app.winfo_screenwidth()
                sh_log = app.winfo_screenheight()
                sx = _full.size[0] / max(1, sw_log)
                sy = _full.size[1] / max(1, sh_log)
                phys_bbox = (
                    max(0, int(x * sx)),
                    max(0, int(y * sy)),
                    min(_full.size[0], int((x + w) * sx)),
                    min(_full.size[1], int((y + h) * sy)),
                )
                img = ImageGrab.grab(bbox=phys_bbox, all_screens=True)
                img.save(screenshot_to)
                print(f"[screenshot] bbox={phys_bbox} size={img.size} saved -> {screenshot_to}")
            except Exception as exc:
                print(f"[screenshot] failed: {exc}")
            finally:
                app.after(200, app.destroy)

        def _after_bottom():
            # 复用 _after，但截图前先滚到 detail 底部
            if screenshot_query:
                app.var_query.set(screenshot_query)
                app._run_query()
            try:
                app.update_idletasks()
                app.update()
                # 滚到 detail_canvas 底部
                try:
                    app.detail_canvas.yview_moveto(1.0)
                except Exception:
                    pass
                app.update_idletasks()
                app.update()
                from PIL import ImageGrab  # type: ignore
                app.lift()
                app.attributes("-topmost", True)
                app.update()
                app.attributes("-topmost", False)
                # DPI 缩放处理
                x = app.winfo_rootx()
                y = app.winfo_rooty()
                w = app.winfo_width()
                h = app.winfo_height()
                sw_log = app.winfo_screenwidth()
                sh_log = app.winfo_screenheight()
                _full = ImageGrab.grab()
                sx = _full.size[0] / max(1, sw_log)
                sy = _full.size[1] / max(1, sh_log)
                phys_bbox = (
                    max(0, int(x * sx)),
                    max(0, int(y * sy)),
                    min(_full.size[0], int((x + w) * sx)),
                    min(_full.size[1], int((y + h) * sy)),
                )
                img = ImageGrab.grab(bbox=phys_bbox, all_screens=True)
                img.save(screenshot_to)
                print(f"[screenshot-bottom] phys={phys_bbox} size={img.size} saved -> {screenshot_to}")
            except Exception as exc:
                print(f"[screenshot-bottom] failed: {exc}")
            finally:
                app.after(200, app.destroy)

        def _after_stitch():
            """截图模式（拼接）：临时把 detail 区扩到内容全高，一次截全图。"""
            from PIL import ImageGrab, Image  # type: ignore
            try:
                if screenshot_query:
                    app.var_query.set(screenshot_query)
                    app._run_query()
                app.update_idletasks()
                app.update()

                # 抬到最前
                app.lift()
                app.attributes("-topmost", True)
                app.update()
                app.attributes("-topmost", False)

                # 临时让 detail 区域显示完整内容（取消滚动 + 放大内部 frame）
                # 1) 让 detail_canvas 知道它很高
                try:
                    app.detail_canvas.yview_moveto(0.0)
                except Exception:
                    pass
                # 2) 强制 detail_inner 用其内容需要的尺寸
                app.update_idletasks()
                req_h = app.detail_inner.winfo_reqheight()
                req_w = app.detail_inner.winfo_reqwidth()
                # 把 detail_canvas 的 window（detail_inner）固定到完整尺寸
                try:
                    # 让 canvas 内部 frame 区域足够大
                    app.detail_canvas.itemconfigure(
                        app.detail_window, height=req_h, width=req_w
                    )
                except Exception:
                    pass
                # 3) 调整 detail_canvas 自己的高度（与 detail_inner 同步）
                target_h = max(req_h, 800)
                try:
                    app.detail_canvas.configure(height=target_h, scrollregion=(0, 0, req_w, req_h))
                except Exception:
                    pass
                app.update_idletasks()
                app.update()

                # 4) 调整整个窗口高度以容纳扩展后的 detail
                try:
                    # 估算 title + 状态栏 + 操作栏 + 间距 ~ 170
                    new_h = min(target_h + 200, 1800)
                    app.geometry(f"1200x{new_h}+0+0")
                except Exception:
                    pass
                app.update_idletasks()
                app.update()

                # 截图（DPI 缩放：winfo_* 是逻辑坐标，PIL 用物理坐标）
                x = app.winfo_rootx()
                y = app.winfo_rooty()
                w = app.winfo_width()
                h = app.winfo_height()
                sw_log = app.winfo_screenwidth()
                sh_log = app.winfo_screenheight()
                _full = ImageGrab.grab()
                sx = _full.size[0] / max(1, sw_log)
                sy = _full.size[1] / max(1, sh_log)
                phys_bbox = (
                    max(0, int(x * sx)),
                    max(0, int(y * sy)),
                    min(_full.size[0], int((x + w) * sx)),
                    min(_full.size[1], int((y + h) * sy)),
                )
                full = ImageGrab.grab(bbox=phys_bbox, all_screens=True)
                # 只裁 detail 列
                dx = int((app.detail_canvas.winfo_rootx() - x) * sx)
                dy = int((app.detail_canvas.winfo_rooty() - y) * sy)
                dw = int(app.detail_canvas.winfo_width() * sx)
                dh = int(app.detail_canvas.winfo_height() * sy)
                crop_box = (dx, dy, dx + dw, dy + dh)
                detail = full.crop(crop_box)
                detail.save(screenshot_to)
                print(
                    f"[screenshot-stitch] saved -> {screenshot_to} "
                    f"size={detail.size} (detail {dw}x{dh}, target_h={target_h})"
                )
            except Exception as exc:
                print(f"[screenshot-stitch] failed: {exc}")
            finally:
                app.after(200, app.destroy)

        if getattr(source, "_screenshot_mode", None) == "stitch":
            app.after(screenshot_delay_ms or 800, _after_stitch)
        elif getattr(source, "_screenshot_mode", None) == "bottom":
            app.after(screenshot_delay_ms or 800, _after_bottom)
        else:
            app.after(screenshot_delay_ms or 800, _after)
    app.mainloop()
