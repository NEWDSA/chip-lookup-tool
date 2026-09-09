# -*- coding: utf-8 -*-
"""
chip_lookup.math_canvas
------------------------
手写数学公式识别模块：
1. Tkinter Canvas 手写板
2. 将 Canvas 内容转换为 PIL Image
3. 调用 pix2tex 识别为 LaTeX
4. 使用 sympy 解析并计算结果
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Callable, Optional, Tuple

# 配色（与 ui.py 保持一致）
COLOR_CANVAS_BG = "#ffffff"       # 画布背景（白色，pix2tex 需要白底黑字）
COLOR_CANVAS_FG = "#000000"       # 笔迹颜色（黑色）
COLOR_CANVAS_BORDER = "#2a3a4f"   # 边框颜色
COLOR_PANEL = "#16202c"           # 面板背景
COLOR_TEXT = "#e6edf3"            # 主文字
COLOR_ACCENT = "#16c47e"          # 强调绿


class MathCanvas:
    """深色主题手写板，与现有 UI 风格一致"""

    def __init__(
        self,
        parent: tk.Widget,
        width: int = 400,
        height: int = 150,
        stroke_width: int = 3,
    ):
        self.canvas = tk.Canvas(
            parent,
            width=width,
            height=height,
            bg=COLOR_CANVAS_BG,
            highlightthickness=1,
            highlightbackground=COLOR_CANVAS_BORDER,
            cursor="crosshair",
        )
        self._last_x: Optional[int] = None
        self._last_y: Optional[int] = None
        self._stroke_width = stroke_width
        self._strokes: list = []  # 存储所有笔画的 ID

        # 绑定鼠标事件
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

    def _on_press(self, event: tk.Event) -> None:
        """鼠标按下：记录起始点"""
        self._last_x = event.x
        self._last_y = event.y

    def _on_drag(self, event: tk.Event) -> None:
        """鼠标拖动：绘制线条"""
        if self._last_x is not None and self._last_y is not None:
            line_id = self.canvas.create_line(
                self._last_x,
                self._last_y,
                event.x,
                event.y,
                fill=COLOR_CANVAS_FG,
                width=self._stroke_width,
                capstyle=tk.ROUND,
                smooth=True,
            )
            self._strokes.append(line_id)
        self._last_x = event.x
        self._last_y = event.y

    def _on_release(self, event: tk.Event) -> None:
        """鼠标释放：清除起始点"""
        self._last_x = None
        self._last_y = None

    def clear(self) -> None:
        """清空画布"""
        for stroke in self._strokes:
            self.canvas.delete(stroke)
        self._strokes.clear()

    def get_image(self):
        """将 Canvas 内容转换为 PIL Image（白底黑字）

        注意：pix2tex 需要白底黑字的图片，所以这里创建白色背景，
        并用黑色绘制所有笔画。
        """
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            raise ImportError("需要安装 pillow 库：pip install pillow")

        # 创建白色背景图片
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        img = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(img)

        # 获取所有线条坐标并绘制
        for item_id in self._strokes:
            coords = self.canvas.coords(item_id)
            # 每个线条有 4 个坐标点：x1, y1, x2, y2
            for i in range(0, len(coords) - 2, 2):
                x1, y1, x2, y2 = coords[i], coords[i + 1], coords[i + 2], coords[i + 3]
                draw.line(
                    [x1, y1, x2, y2],
                    fill="black",
                    width=self._stroke_width,
                )
        return img


class MathRecognizer:
    """LaTeX 识别器：pix2tex 识别 + sympy 计算"""

    def __init__(self):
        self._model = None  # 延迟加载模型
        self._lock = threading.Lock()

    def _get_model(self):
        """延迟加载 pix2tex 模型（首次调用时加载，避免启动卡顿）"""
        if self._model is None:
            with self._lock:
                if self._model is None:  # 双重检查锁定
                    try:
                        from pix2tex.cli import LatexOCR
                        self._model = LatexOCR()
                    except ImportError:
                        raise ImportError("需要安装 pix2tex 库：pip install pix2tex[gui]")
        return self._model

    def recognize(self, image) -> str:
        """识别图片中的数学公式，返回 LaTeX 字符串"""
        model = self._get_model()
        latex_str = model(image)
        return latex_str

    def calculate(self, latex_str: str) -> dict:
        """解析 LaTeX 并计算结果"""
        try:
            from sympy.parsing.latex import parse_latex
            from sympy import latex

            expr = parse_latex(latex_str)
            result = expr.evalf()
            return {
                "latex": latex_str,
                "expr": str(expr),
                "result": str(result),
                "latex_result": latex(result),
            }
        except Exception as e:
            return {"error": str(e)}


class MathCanvasWithRecognizer:
    """集成手写板和识别器的组合类"""

    def __init__(
        self,
        parent: tk.Widget,
        width: int = 400,
        height: int = 150,
        on_result: Optional[Callable[[str, dict], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ):
        """
        参数:
            parent: 父窗口
            width: 画布宽度
            height: 画布高度
            on_result: 识别成功回调 (latex_str, result_dict)
            on_error: 识别失败回调 (error_message)
        """
        self.parent = parent
        self.on_result = on_result
        self.on_error = on_error

        # 创建手写板
        self.canvas = MathCanvas(parent, width=width, height=height)

        # 创建识别器
        self.recognizer = MathRecognizer()

        # 结果队列（用于线程间通信）
        self._queue: queue.Queue = queue.Queue()
        self._is_recognizing = False

    def pack(self, **kwargs) -> None:
        """打包画布"""
        self.canvas.canvas.pack(**kwargs)

    def grid(self, **kwargs) -> None:
        """网格布局画布"""
        self.canvas.canvas.grid(**kwargs)

    def clear(self) -> None:
        """清空画布"""
        self.canvas.clear()

    def recognize_async(self) -> None:
        """异步识别手写公式"""
        if self._is_recognizing:
            return  # 正在识别中

        # 获取画布图像
        image = self.canvas.get_image()

        # 检查画布是否为空（全白）
        from PIL import Image
        pixels = list(image.getdata())
        if all(p == (255, 255, 255) for p in pixels):
            if self.on_error:
                self.on_error("画布为空，请先手写公式")
            return

        self._is_recognizing = True

        def _recognize():
            try:
                latex_str = self.recognizer.recognize(image)
                result = self.recognizer.calculate(latex_str)
                self._queue.put(("ok", latex_str, result))
            except Exception as e:
                self._queue.put(("error", str(e)))

        threading.Thread(target=_recognize, daemon=True).start()
        self._poll_queue()

    def _poll_queue(self) -> None:
        """轮询识别结果"""
        try:
            while True:
                item = self._queue.get_nowait()
                self._is_recognizing = False
                if item[0] == "ok":
                    latex_str, result = item[1], item[2]
                    if self.on_result:
                        self.on_result(latex_str, result)
                else:
                    if self.on_error:
                        self.on_error(item[1])
        except queue.Empty:
            # 队列为空，继续轮询
            self.parent.after(50, self._poll_queue)
