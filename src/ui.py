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

import copy
import math
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional, Tuple

from config import MODE_LABELS, Settings
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
    return "\n".join(lines)


import re as _re

_CAPACITY_RE = _re.compile(r"(\d+(?:\.\d+)?)\s*[GTgT]", _re.IGNORECASE)
_BITWIDTH_RE = _re.compile(r"[xX]?(\d+)")


def _capacity_display(capacity: str, bit_width: str) -> str:
    """把原始容量换算为带 GB 的显示文本。

    公式：单颗粒容量(GB) = 标称容量(Gb) ÷ 8
    例：16Gb → 2 GB；24Gb → 3 GB；4Gb → 0.5 GB

    说明：DRAM 标称容量已包含总存储位数，bit_width 表示数据总线宽度，
    不参与容量换算。
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
    gb = density_g / 8
    gb_str = ("%g" % gb) if gb != int(gb) else str(int(gb))
    return "%s (%s GB)" % (capacity, gb_str)


# ---------------- 细条滚动条（详情区） ----------------

def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


_BG_RGB = _hex_to_rgb(COLOR_BG)


class SlimVScrollbar(tk.Canvas):
    """右侧详情区的自绘细条竖向滚动条（替代 clam 主题默认 ttk.Scrollbar）。

    设计要点（对应视觉优化需求）：
    1. 纤细化：命中区仅 11px，视觉条宽 6px，去掉了原生粗大的上下箭头按钮；
    2. 长度准确：滑块高度只由 canvas 回报的 (first,last) 比例换算，有多少
       可滚动范围就显示多长；内容不溢出时被 _sync_detail_scrollbar 整体
       卸载，从根上杜绝“整条轨道占满的无效滑块”；
    3. 圆角 + 透明感：轨道/滑块是圆角胶囊形，hover / 按下时透明度逐帧过渡；
    4. 交互可用：滑块最短 24px 保证易抓取，按住拖动、点击轨道翻页。

    说明：Tk 的 PhotoImage 不支持逐像素 alpha，这里把透明度按已知底色
    COLOR_BG 预先混合成不透明像素（伪透明），因此本控件必须贴在纯
    COLOR_BG 底色的区域上使用。
    """

    WIDTH = 11            # 命中区宽度（整条都响应悬停/点击，保证可点性）
    BAR_W = 6.0           # 视觉条宽（纤细）
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

        self._blank_photo = tk.PhotoImage(width=1, height=1)
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


# ---------------- 主窗口 ----------------

class App(tk.Tk):
    def __init__(self, settings: Settings, source: RecordSource,
                 on_change: Optional[Callable[[], None]] = None):
        super().__init__()
        self.title("ChipLookup · 芯片料号查询器")
        self.geometry("1200x860")
        self.minsize(1000, 680)
        self.configure(bg=COLOR_BG)

        self.settings = settings
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
        self._desired_mode: Optional[str] = None   # 用户最新想切到的模式
        self._pending_force = False        # 下一个切换是否为强制重建（F5 刷新）
        self._switch_queue: "queue.Queue[tuple]" = queue.Queue()
        self._polling = False              # 队列轮询是否已挂起
        self._source_radios: List[ttk.Radiobutton] = []
        # 输入防抖：连续敲键只触发最后一次查询（上游 4 万条时省去中间全量扫描）
        self._query_after_id: Optional[str] = None

        # ---------- 候选区分页参数 ----------
        self.PAGE_SIZE = 8                # 每页条数，与 tree 可见行数一致
        self.page_mode = tk.StringVar(value="paginate")  # "paginate" | "load_more"
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
        self._col_drag: Optional[Tuple[int, int]] = None   # (被拖分隔线左侧列下标, 拖动起始 x_root)
        self._col_drag_widths: Optional[Tuple[int, ...]] = None  # 拖动起点各列宽（拖动基准）

        self._configure_style()
        self._build_layout()
        self._refresh_status()
        self._bind_global_keys()
        self._refresh_mode_buttons()  # 初始化分页分段按钮高亮
        self._refresh_source_buttons()  # 初始化数据源 radio 选中态（回填已保存模式）
        # 窗口首帧布局稳定后，按内容高度决定详情滚动条是否出现
        self._schedule_scroll_sync()

    # ---------------- 样式 ----------------

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
            font=("Consolas", 12),
        )

        # 按钮
        style.configure(
            "Accent.TButton",
            background=COLOR_ACCENT,
            foreground="#0e1620",
            borderwidth=0,
            focusthickness=0,
            padding=BTN_PADDING_LG,
            font=(FONT_FAMILY_UI, FONT_SIZE_SM, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#0fa970"), ("disabled", "#3a4a48")], foreground=[("disabled", "#b0c4d4")])

        style.configure(
            "Blue.TButton",
            background=COLOR_ACCENT2,
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=0,
            padding=BTN_PADDING_LG,
            font=(FONT_FAMILY_UI, FONT_SIZE_SM, "bold"),
        )
        style.map("Blue.TButton", background=[("active", "#2563eb"), ("disabled", "#3b4862")])

        style.configure(
            "Ghost.TButton",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            borderwidth=0,
            focusthickness=0,
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
            focusthickness=0,
            padding=BTN_PADDING_SM,
            font=(FONT_FAMILY_UI, FONT_SIZE_XS),
        )
        style.map("ModeSel.TButton", background=[("active", "#3a4d63")])
        style.configure(
            "ModeSelActive.TButton",
            background=COLOR_ACCENT,
            foreground="#0e1620",
            borderwidth=0,
            focusthickness=0,
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
            foreground=[("selected", "#0e1620"), ("active", COLOR_TEXT)],
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
            rowheight=32,
            font=("Microsoft YaHei UI", 10),
        )
        style.configure(
            "Candidate.Treeview.Heading",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT_DIM,
            borderwidth=0,
            font=("Microsoft YaHei UI", 10, "bold"),
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
        top = ttk.Frame(self, style="Panel.TFrame", padding=(14, 8))
        top.pack(fill=tk.X, side=tk.TOP)
        ttk.Label(top, text="ChipLookup", style="Title.TLabel").pack(side=tk.LEFT)
        self.status_label = ttk.Label(top, text="", style="Status.TLabel")
        self.status_label.pack(side=tk.LEFT, padx=(24, 0))

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

        # 主体（左右两栏 + 可拖分隔条）
        body = ttk.Frame(self, style="TFrame", padding=(14, 0))
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
        left = ttk.Frame(body, style="Panel.TFrame", padding=(12, 12))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.configure(width=300)  # 先占位，首帧由 body Configure 校正
        body.columnconfigure(0, minsize=300)
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
        entry.bind("<Escape>", lambda e: self._clear_query())

        # 按钮行
        btn_row = ttk.Frame(left, style="Panel.TFrame")
        btn_row.pack(fill=tk.X, pady=(0, 12))
        ttk.Button(btn_row, text="解析料号", style="Accent.TButton",
                   command=self._on_enter).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        self.btn_fuzzy = ttk.Button(btn_row, text="搜索", style="Blue.TButton",
                                    command=self._on_fuzzy)
        self.btn_fuzzy.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        ttk.Label(left, text="候选（↑↓ 选择，Enter 查看详情）",
                  style="Hint.TLabel").pack(anchor="w", pady=(0, 6))

        # 分页模式分段按钮（紧凑）：[分页] [加载更多]
        mode_row = ttk.Frame(left, style="Panel.TFrame")
        mode_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(mode_row, text="分页：", style="Hint.TLabel").pack(side=tk.LEFT, padx=(0, 6))
        self.btn_mode_paginate = ttk.Button(
            mode_row, text="分页", style="ModeSel.TButton",
            command=lambda: self._set_mode("paginate"),
        )
        self.btn_mode_paginate.pack(side=tk.LEFT, padx=(0, 2))
        self.btn_mode_load_more = ttk.Button(
            mode_row, text="加载更多", style="ModeSel.TButton",
            command=lambda: self._set_mode("load_more"),
        )
        self.btn_mode_load_more.pack(side=tk.LEFT)

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

        # 候选列表（高度 = PAGE_SIZE 行，限制候选区过高）
        cols = self._TREE_COLS
        self.tree = ttk.Treeview(
            tree_box, columns=cols, show="headings", height=self.PAGE_SIZE,
            style="Candidate.Treeview", selectmode="browse",
        )
        for c, w, anchor in [
            ("part_number", 110, "w"),
            ("model", 200, "w"),
            ("manufacturer", 110, "w"),
            ("capacity", 130, "center"),
            ("type", 80, "center"),
        ]:
            self.tree.heading(c, text={
                "part_number": "料号", "model": "型号", "manufacturer": "厂商",
                "capacity": "容量", "type": "类型",
            }[c])
            self.tree.column(c, width=w, anchor=anchor)
        # tree 占大头（fill both + expand），但放进 tree_box 后由 box 控制边界
        # side=TOP + expand=True：吃占 pager 留下的剩余空间
        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_candidate_select)
        self.tree.bind("<Double-1>", self._on_candidate_activate)
        # 宽度自适应：五列始终铺满当前左栏可视宽度（不裁剪也不留白）
        self.tree.bind("<Configure>", lambda e: self._fit_tree_columns(e.width))
        # 鼠标拖拽改列宽：悬停表头分隔线显双箭头光标，按下拖动联动相邻列
        self.tree.bind("<Motion>", self._on_tree_motion)
        self.tree.bind("<ButtonPress-1>", self._on_tree_press)
        self.tree.bind("<Leave>", self._on_tree_leave)

        # 左右分栏分隔条：按住拖动调整左栏宽度，右栏随之动态适配
        sash = tk.Frame(body, width=6, bg=COLOR_BORDER, cursor="sb_h_double_arrow")
        sash.grid(row=0, column=1, sticky="ns")
        sash.bind("<Enter>", self._on_sash_enter)
        sash.bind("<Leave>", self._on_sash_leave)
        sash.bind("<ButtonPress-1>", self._on_sash_press)
        self.sash = sash

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

        # 底部操作栏
        bottom = ttk.Frame(self, style="Panel.TFrame", padding=(14, 8))
        bottom.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Button(bottom, text="复制全部", style="Accent.TButton",
                   command=lambda: self._copy_current(make_text_for_copy, "已复制全部字段到剪贴板")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bottom, text="复制料号", style="Blue.TButton",
                   command=self._copy_part_no).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bottom, text="清空", style="Ghost.TButton",
                   command=self._clear_query).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Separator(bottom, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        ttk.Button(bottom, text="导入 CSV", style="Ghost.TButton",
                   command=self._import_csv).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bottom, text="导出 CSV", style="Ghost.TButton",
                   command=self._export_csv).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(bottom, text="刷新数据", style="Ghost.TButton",
                   command=self._reload_db).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Separator(bottom, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        ttk.Button(bottom, text="退出", style="Ghost.TButton",
                   command=self.destroy).pack(side=tk.RIGHT)

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
        self.bind("<Control-c>", lambda e: self._copy_part_no())
        self.bind("<Control-C>", lambda e: self._copy_part_no())
        self.bind("<Escape>", lambda e: self._clear_query())
        self.bind("<F5>", lambda e: self._reload_db())

    def _refresh_status(self):
        mode_label = MODE_LABELS.get(self.settings.mode, self.settings.mode)
        self.status_label.configure(
            text=f"模式 {mode_label} · {self.source.describe()}"
                 f"   •   共 {self.source.count()} 条记录"
        )

    def _refresh_source_buttons(self):
        """把 radio 组件选中项同步到当前模式（互斥由共享变量自动保证）。"""
        if self.var_source_mode.get() != self.settings.mode:
            self.var_source_mode.set(self.settings.mode)

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
        """主线程轮询后台切换结果并落地（token 校验防串台）。"""
        try:
            while True:
                item = self._switch_queue.get_nowait()
                token = item[0]
                if token != self._switch_token:
                    continue  # 过期结果（理论上忙碌门控下不会出现）
                kind = item[1]
                mode = item[2]
                if kind == "ok":
                    _, _, _, src, records = item
                    self._apply_switch_result(token, mode, src, records)
                else:
                    _, _, _, exc = item
                    self._handle_switch_error(token, mode, exc)
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
            if changed:
                label = MODE_LABELS.get(mode, mode)
                self._toast(f"已切换至「{label}」：{self.source.describe()} 共 {len(records)} 条")
            else:
                self._toast(f"已刷新：{self.source.describe()} 共 {len(records)} 条")
            if self.var_query.get().strip():
                self._run_query()
            else:
                self._clear_detail()
                self._render_detail_empty()
            if not changed and self.on_change:
                self.on_change()
        self._switch_busy = False
        self._pump_switch()

    def _handle_switch_error(self, token: int, mode: str, exc: Exception):
        """后台构建失败：回滚 radio/状态；若用户又点了新模式则直接接力。"""
        self._switch_busy = False
        if self._desired_mode is None:
            # 没有更新的目标 → 回滚到当前已加载模式并提示
            self._refresh_source_buttons()
            self._refresh_status()
            messagebox.showerror(
                "切换模式失败",
                f"无法载入「{MODE_LABELS.get(mode, mode)}」数据源：\n{exc}",
            )
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

    def _on_enter(self):
        """回车：如果候选唯一或已选定某项 → 显示详情；否则聚焦候选列表第一项。"""
        self._cancel_pending_query()
        if not self._candidates:
            self._run_query()
            return
        if self._selected_index >= 0 and self._selected_index < len(self._candidates):
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

    def _on_candidate_activate(self, event=None):
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
            # 分页控件：«  第 N/M 页  ·  共 X 条  »
            ttk.Button(self.pager_frame, text="«", style="ModeSel.TButton", width=2,
                       command=self._prev_page).pack(side=tk.LEFT, padx=(0, 4))
            ttk.Label(self.pager_frame, text=f"第 {page + 1} / {total_pages} 页",
                      style="Hint.TLabel").pack(side=tk.LEFT)
            ttk.Button(self.pager_frame, text="»", style="ModeSel.TButton", width=2,
                       command=self._next_page).pack(side=tk.LEFT, padx=(4, 0))
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
                    rec.get("type", ""),
                ),
            )

    # ---------------- 布局自适应 ----------------

    # 候选表五列（顺序/语义固定）
    _TREE_COLS = ("part_number", "model", "manufacturer", "capacity", "type")
    # 左栏右缘与分隔条之间的留白（grid padx，_apply_pane_width 需一并计入列宽）
    _LEFT_PADX = 7
    # 候选表五列的权重与最小列宽（仅布局细节，不改变列语义/顺序）
    _TREE_COL_MIN = {
        "part_number": 64, "model": 50, "manufacturer": 44,
        "capacity": 130, "type": 40,
    }
    _TREE_COL_WEIGHT = {
        "part_number": 0.35, "model": 0.30, "manufacturer": 0.20,
        "capacity": 0.10, "type": 0.05,
    }

    def _fit_tree_columns(self, width: Optional[int] = None):
        """把候选表 5 列的总宽铺满当前左栏可视宽度，杜绝横向裁切/留白。

        - 默认：按信息重要度权重分配（料号/型号优先）。
        - 用户拖过列宽后：保留用户设定的各列比例，窗口缩放时等比缩放铺满。
        """
        tree = self.tree
        try:
            avail = (width if width else tree.winfo_width()) - 4
        except Exception:
            return
        if avail < 180:
            return
        cols = self._TREE_COLS
        if self._tree_col_custom and self._tree_col_cache:
            # 自定义模式：把当前各列宽等比缩放到新可视宽度（保留用户手感）
            old_total = sum(self._tree_col_cache)
            if old_total > 0:
                plan = [
                    max(self._TREE_COL_MIN[c], int(w * avail / old_total + 0.5))
                    for w, c in zip(self._tree_col_cache, cols)
                ]
                # 四舍五入误差并入首列，保证总宽严格等于可视宽度
                plan[0] += avail - sum(plan)
                widths = tuple(plan)
                if widths == self._tree_col_cache:
                    return
                self._tree_col_cache = widths
                for c, w in zip(cols, widths):
                    tree.column(c, width=w)
                return
        base = sum(self._TREE_COL_MIN[k] for k in cols)
        extra = max(0, avail - base)
        plan = {
            k: self._TREE_COL_MIN[k] + int(extra * self._TREE_COL_WEIGHT[k])
            for k in cols
        }
        # 把四舍五入误差并入「料号」列，保证总和与可视宽度严格一致
        plan["part_number"] = avail - sum(plan[k] for k in cols[1:])
        widths = tuple(plan[k] for k in cols)
        if widths == self._tree_col_cache:
            return
        self._tree_col_cache = widths
        for k in cols:
            tree.column(k, width=max(20, plan[k]))

    # ---------------- 左栏列宽鼠标拖拽 ----------------

    def _tree_header_height(self) -> int:
        """估算表头高度：优先取第一行顶边的 y 坐标，空表时用常量兜底。"""
        try:
            items = self.tree.get_children()
            if items:
                b = self.tree.bbox(items[0])
                if b and b[1] > 0:
                    return b[1]
        except tk.TclError:
            pass
        return 27

    def _tree_col_widths(self) -> Tuple[int, ...]:
        try:
            return tuple(self.tree.column(c, "width") for c in self._TREE_COLS)
        except tk.TclError:
            return self._tree_col_cache or tuple(self._TREE_COL_MIN[c] for c in self._TREE_COLS)

    def _divider_index_at(self, x: int, tol: int = 6) -> Optional[int]:
        """返回 x 落在哪条表头分隔线上（第 i 列与第 i+1 列之间）；不在则 None。"""
        widths = self._tree_col_widths()
        acc = 0
        for i, w in enumerate(widths[:-1]):
            acc += w
            if abs(x - acc) <= tol:
                return i
        return None

    def _on_tree_motion(self, event):
        """悬停表头分隔线时把光标切换成左右拖拽箭头，提供可拖拽的视觉反馈。"""
        if getattr(self, "_col_drag", None):
            return
        want = ""
        if event.y <= self._tree_header_height():
            if self._divider_index_at(event.x) is not None:
                want = "sb_h_double_arrow"
        if want != self._tree_cursor:
            self._tree_cursor = want
            self.tree.configure(cursor=want)

    def _on_tree_leave(self, event=None):
        if not getattr(self, "_col_drag", None):
            self._tree_cursor = ""
            try:
                self.tree.configure(cursor="")
            except tk.TclError:
                pass

    def _on_tree_press(self, event):
        """左键落在表头分隔线附近时接管拖动：本列变宽、右侧各列等量让位（总宽恒定）。

        返回 "break" 以阻止 Treeview 自带的列宽处理，避免两种逻辑互相叠加。
        """
        if event.y > self._tree_header_height():
            return None
        i = self._divider_index_at(event.x)
        if i is None:
            return None
        self._col_drag = (i, event.x_root)
        self._col_drag_widths = self._tree_col_widths()  # 拖动起点各列宽（基准）
        self.tree.bind_all("<B1-Motion>", self._on_col_drag)
        self.tree.bind_all("<ButtonRelease-1>", self._on_col_drag_end)
        self.tree.configure(cursor="sb_h_double_arrow")
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

        min_me = self._TREE_COL_MIN[cols[i]]
        min_suffix = sum(self._TREE_COL_MIN[cols[j]] for j in range(i + 1, len(cols)))
        # 可拖范围：自身不窄于 min_me；也不超过「总宽 - 右侧最小宽总和」（右侧不可被挤破）
        w_i = max(min_me, min(start[i] + int(round(dx)), total - min_suffix))
        if w_i == start[i]:
            return
        self.tree.column(cols[i], width=w_i)
        delta = w_i - start[i]

        if delta > 0:
            # 加宽：右侧按各自“可压缩余量”成比例收缩（不突破最小宽）
            slack = [start[j] - self._TREE_COL_MIN[cols[j]] for j in range(i + 1, len(cols))]
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
                cap = cur - self._TREE_COL_MIN[cols[j]]
                take = min(cap, rest)
                if take > 0:
                    self.tree.column(cols[j], width=cur - take)
                    rest -= take
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
        self.tree.configure(cursor="")

    # ---------------- 左右分栏拖拽（分隔条） ----------------

    def _on_sash_enter(self, event):
        if not getattr(self, "_pane_drag", None):
            self.sash.configure(bg="#3a4d63")

    def _on_sash_leave(self, event):
        if not getattr(self, "_pane_drag", None):
            self.sash.configure(bg=COLOR_BORDER)

    def _on_sash_press(self, event):
        if self._left_pane_w is None:
            return
        self._pane_drag = (event.x_root, self._left_pane_w)
        self.sash.configure(bg=COLOR_CARD_HOVER)
        self.sash.bind_all("<B1-Motion>", self._on_sash_drag)
        self.sash.bind_all("<ButtonRelease-1>", self._on_sash_release)

    def _on_sash_drag(self, event):
        drag = getattr(self, "_pane_drag", None)
        if not drag:
            return
        x_root0, w0 = drag
        body_w = self.body.winfo_width()
        lo, hi = 240, max(260, body_w - 520)  # 右侧详情区保留最低可用宽度
        target = max(lo, min(hi, int(round(w0 + (event.x_root - x_root0)))))
        if target != self._left_pane_w:
            self._apply_pane_width(target)  # 右栏(weight=1)自动吃剩余宽度并动态重排

    def _on_sash_release(self, event):
        self._pane_custom = True
        self._pane_drag = None
        self.sash.configure(bg=COLOR_BORDER)
        try:
            self.sash.unbind_all("<B1-Motion>")
            self.sash.unbind_all("<ButtonRelease-1>")
        except tk.TclError:
            pass

    def _on_body_configure(self, event):
        """body 首次布局/窗口缩放时同步左栏像素宽：默认按 30% 比例，用户拖过则保持不变。"""
        if self._pane_custom:
            return
        try:
            target = self._default_pane_width(event.width)
        except Exception:
            return
        if self._left_pane_w is None or abs(self._left_pane_w - target) >= 6:
            self._apply_pane_width(target)

    def _default_pane_width(self, body_w: int) -> int:
        """左栏默认宽度：约 body 可视宽 30%，且保证右侧详情区不小于 480px。"""
        content = max(400, body_w - 30)
        left = max(250, int(content * 0.30))
        return min(left, max(250, content - 480))

    def _apply_pane_width(self, px: int):
        """设置左栏像素宽：同步 grid 列 minsize 强制重排。

        仅 left.configure(width=) 修改 ttk Frame 宽度选项不会主动触发 grid 重排，
        因此必须以 columnconfigure 通知 grid 管理器。
        注意：grid 会把列 cell 的 padx（左栏右缘 7px）计入整列宽度，所以 minsize
        要加回 _LEFT_PADX，widget 实际宽度才等于 px。
        """
        self._left_pane_w = px
        self.left.configure(width=px)
        try:
            self.body.columnconfigure(0, minsize=px + self._LEFT_PADX)
        except tk.TclError:
            pass

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
            try:
                lw = lab.winfo_reqwidth()
            except Exception:
                lw = 158
            # 卡片左右内边距各 20px，再加 12px 视觉余量
            val.configure(wraplength=max(120, cw - 40 - lw - 12))

    def _refresh_pager(self):
        """刷新加载更多模式下的 pager 控件。"""
        for w in self.pager_frame.winfo_children():
            w.destroy()
        total = len(self._candidates)
        count = min(self._visible_count, total)
        if count < total:
            ttk.Button(
                self.pager_frame,
                text=f"加载更多（还剩 {total - count} 条）",
                style="ModeSel.TButton",
                command=self._load_more,
            ).pack(side=tk.LEFT)
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
        is_paginate = self.page_mode.get() == "paginate"
        self.btn_mode_paginate.configure(
            style="ModeSelActive.TButton" if is_paginate else "ModeSel.TButton"
        )
        self.btn_mode_load_more.configure(
            style="ModeSelActive.TButton" if not is_paginate else "ModeSel.TButton"
        )

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

    def _load_more(self):
        new_count = min(self._visible_count + self.PAGE_SIZE, len(self._candidates))
        old_count = self._visible_count
        self._visible_count = new_count
        # 增量插入新行（不重置已加载的，保留滚动位置）
        if new_count > old_count:
            self._insert_rows(old_count, new_count)
        self._refresh_pager()
        self._select_first()

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
                  "• 用 「导入 CSV」 把新批次料号纳入数据库\n"
                  "• 命令行直接维护：python tools/import_export.py add <料号> <型号>",
                  style="Card.TLabel",
                  justify="left").pack(anchor="w")

    def _render_detail(self, record: dict):
        self._clear_detail()
        # 主信息卡
        head = ttk.Frame(self.detail_inner, style="Card.TFrame", padding=CARD_PADDING)
        head.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            head,
            text=record.get("part_number", "") or "(无料号)",
            style="Big.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            head,
            text=record.get("model", "") or "",
            style="Card.TLabel",
            font=("Consolas", 12),
        ).pack(anchor="w", pady=(2, 0))
        tag_text = " ".join(
            f"[{v}]" for v in [
                record.get("manufacturer", ""),
                record.get("type", ""),
                _capacity_display(record.get("capacity", ""), record.get("bit_width", "")),
            ] if v
        )
        if tag_text:
            ttk.Label(head, text=tag_text, style="Dim.TLabel").pack(anchor="w", pady=(6, 0))

        # 分组卡片
        groups = [
            ("基本", [
                ("厂商 Manufacturer", record.get("manufacturer", "")),
                ("类型 Type", record.get("type", "")),
                ("型号 Model", record.get("model", "")),
            ]),
            ("存储参数", [
                ("容量 Capacity", _capacity_display(record.get("capacity", ""), record.get("bit_width", ""))),
                ("位宽 Bit Width", record.get("bit_width", "")),
                ("速度 Speed", record.get("speed", "")),
                ("电压 Voltage", record.get("voltage", "")),
            ]),
            ("物理信息", [
                ("封装 Package", record.get("package", "")),
                ("尺寸 Dimensions", record.get("dimensions", "")),
                ("Die 数 Die Count", record.get("die_count", "")),
                ("CS 数 CS Count", record.get("cs_count", "")),
                ("Die 版本 Die Rev", record.get("die_revision", "")),
            ]),
            ("使用条件", [
                ("工作温度 Op Temp", record.get("op_temp", "")),
                ("备注 Notes", record.get("notes", "")),
            ]),
        ]
        for title, rows in groups:
            card = ttk.Frame(self.detail_inner, style="Card.TFrame", padding=(20, 16))
            card.pack(fill=tk.X, pady=(0, 10))
            ttk.Label(card, text=title,
                      style="Card.TLabel",
                      font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 10))
            for label, value in rows:
                display = str(value) if value not in ("", None) else "\u2014"  # 空值显示占位符「—」
                is_empty = value in ("", None)
                row = ttk.Frame(card, style="Card.TFrame")
                row.pack(fill=tk.X, pady=2)
                lab = ttk.Label(row, text=label, style="Field.TLabel", width=22, anchor="w")
                lab.pack(side=tk.LEFT)
                val = ttk.Label(
                    row, text=display,
                    style="DimValue.TLabel" if is_empty else "Value.TLabel",
                    anchor="w", justify="left",
                )
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

    def _toast(self, msg: str):
        # 没有专门 toast，用状态栏续命
        prev = self.status_label.cget("text")
        self.status_label.configure(text=f"✓ {msg}")
        self.after(1800, lambda: self.status_label.configure(text=prev))
        # 同时写一份 last_query 方便用户从状态栏看出最近动作

    # ---------------- 数据导入导出 ----------------

    def _import_csv(self):
        if not self.source.supports_import():
            self._toast("当前模式（%s）不支持导入 CSV" % self.source.LABEL)
            return
        path = filedialog.askopenfilename(
            title="导入 CSV（合并到现有库）",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            n = self.source.import_csv(path, replace=False)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        # CSV 已变化：所有读取该 CSV 的模式缓存全部失效（import_csv 已就地
        # reload 当前 source，把它重新放回缓存即可；其余模式下次切换再重建）
        self._source_cache.clear()
        self._source_cache[self.settings.mode] = self.source
        # 重新加载缓存
        self._records_cache = self.source.list_records()
        self._refresh_status()
        self._toast(f"导入 {n} 条")
        if self.var_query.get().strip():
            self._run_query()
        if self.on_change:
            self.on_change()

    def _export_csv(self):
        if not self.source.supports_export():
            self._toast("当前模式（%s）不支持导出 CSV" % self.source.LABEL)
            return
        path = filedialog.asksaveasfilename(
            title="导出 CSV",
            defaultextension=".csv",
            initialfile="chip_database_export.csv",
            filetypes=[("CSV 文件", "*.csv")],
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
